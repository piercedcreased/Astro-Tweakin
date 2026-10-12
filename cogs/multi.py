"""v0.8.0 multiplayer: Rock Paper Scissors, the Astro Market (player shop), tags and /pay.
Market and tag buttons are normal panel buttons (through Game.handle: owner check + lock). RPS buttons can be
pressed by either player, so they have their own button type."""
import asyncio
import io
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from game import data, engine, images, multi
from game.data import ORE_BY_KEY
from game.db import NotEnough
from cogs.game import B, PanelSelect, TIER_EMOJI, as_files

log = logging.getLogger("astro-tweakin.multi")
MARKET_COLOR = discord.Color.from_rgb(255, 170, 60)


def item_emoji(item):
    kind, key = item.split(":", 1)
    if kind == "ore":
        return TIER_EMOJI.get(ORE_BY_KEY[key].tier, "⛏️")
    if kind == "mut":
        return engine.split_mut(key)[0].emoji
    if kind == "tag":
        return data.TAG_BY_KEY[key].emoji
    if kind == "crate":
        return data.CRATE_BY_KEY[key].emoji
    if kind == "snack":
        return data.SNACK_BY_KEY[key].emoji
    return {"bar": "🧱", "part": "🔧", "rock": "💎"}.get(kind, "📦")


def parse_amount(text):
    """'50000', '50,000', '50k', '1.5m', '2b' -> int, or None."""
    t = (text or "").strip().lower().replace(",", "").replace("_", "").replace(" ", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kmbt]?)", t)
    if not m:
        return None
    n = float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}[m.group(2)]
    return int(n) if n < 1e18 else None


# ================================================================ Rock Paper Scissors buttons

class RPSButton(discord.ui.DynamicItem[discord.ui.Button], template=r"rps:(?P<pick>[rpsxm]):(?P<gid>\d+)"):
    """A Rock Paper Scissors button. Either player can press it."""

    def __init__(self, pick, gid, label=None, style=discord.ButtonStyle.secondary, emoji=None):
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji, custom_id=f"rps:{pick}:{gid}"))
        self.pick = pick
        self.gid = gid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["pick"], int(match["gid"]), item.label, item.style, item.emoji)

    async def callback(self, interaction):
        await interaction.client.get_cog("Multi").rps_press(interaction, self.pick, self.gid)


def rps_view(gid):
    v = discord.ui.View(timeout=None)
    for k, (emoji, name) in data.RPS.items():
        v.add_item(RPSButton(k, gid, name, discord.ButtonStyle.primary, emoji))
    v.add_item(RPSButton("x", gid, "Back out", discord.ButtonStyle.secondary, "🏳️"))
    return v


# ================================================================ pop-up forms

class ListModal(discord.ui.Modal):
    def __init__(self, cog, uid, item, have):
        super().__init__(title=f"Sell {engine.item_name(item)}"[:45])
        self.cog, self.uid, self.item = cog, uid, item
        shop = int(engine.item_value(item) * engine.sell_mult(cog.db.user(uid), cog.db))
        self.qty = discord.ui.TextInput(label=f"How many? (you have {have:,})"[:45], default=str(have), max_length=12)
        self.price = discord.ui.TextInput(
            label="Total price in coins (50k, 1.5m work too)", max_length=20,
            placeholder=f"The shop pays about {shop:,} each" if shop else "What's it worth to you?")
        self.add_item(self.qty)
        self.add_item(self.price)

    async def on_submit(self, interaction):
        qty, price = parse_amount(self.qty.value), parse_amount(self.price.value)
        if not qty or not price:
            await self.cog.after_modal(interaction, self.uid, lambda: (False, "Use plain numbers like `5` and `50000` (or `50k`)."),
                                       self.cog.sell_panel)
            return
        await self.cog.after_modal(interaction, self.uid, lambda: multi.list_item(self.cog.db, self.uid, self.item, qty, price),
                                   self.cog.mine_panel, self.cog.sell_panel)


class TextModal(discord.ui.Modal):
    """One text box (pickaxe name or custom title)."""

    def __init__(self, cog, uid, title, label, limit, use):
        super().__init__(title=title)
        self.cog, self.uid, self.use = cog, uid, use
        self.text = discord.ui.TextInput(label=label, max_length=limit, min_length=1)
        self.add_item(self.text)

    async def on_submit(self, interaction):
        await self.cog.after_modal(interaction, self.uid, lambda: self.use(self.cog.db, self.uid, self.text.value),
                                   self.cog.tags_panel)


class Multi(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.rps = {}                 # game id -> multi.RPSGame
        self.rps_locks = {}
        self.rps_recent = {}          # finished game id -> RPSGame, for the Rematch button
        self.market_view = {}         # uid -> (category, page) last browsed
        self.bg_tasks = set()
        n = multi.expire(self.db)
        if n:
            log.info("Returned %d expired Market listing(s)", n)
        self.market_loop.start()

    def cog_unload(self):
        self.market_loop.cancel()

    @property
    def game(self):
        return self.bot.get_cog("Game")

    async def show(self, interaction, panel):
        await self.game.show(interaction, panel)

    def _bg(self, coro):
        t = asyncio.create_task(coro)
        self.bg_tasks.add(t)
        t.add_done_callback(self.bg_tasks.discard)

    @tasks.loop(minutes=10)
    async def market_loop(self):
        try:
            for row in self.db.q("SELECT seller, item, qty FROM market WHERE status='open' AND expires < ?", time.time()):
                self._bg(self.dm(row["seller"], f"⌛ Nobody bought your **{row['qty']:,}x {engine.item_name(row['item'])}** "
                                                f"on the Astro Market, so it's back in your inventory."))
            multi.expire(self.db)
        except Exception:
            log.exception("Market expiry failed")

    @market_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def dm(self, uid, text):
        try:
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            await user.send(text)
        except (discord.HTTPException, AttributeError):
            pass

    # ================================================================ routing from Game.handle

    async def dispatch(self, interaction, action, uid):
        if action == "mkt" or action.startswith("mkt_"):
            await self.market_action(interaction, action[4:], uid)
        elif action == "tags" or action.startswith("tag_"):
            await self.tag_action(interaction, action[4:], uid)
        else:
            return False
        return True

    async def after_modal(self, interaction, uid, fn, ok_panel, fail_panel=None):
        """A form was sent: do the thing under the player's lock and update their panel."""
        if interaction.user.id != uid:
            return
        try:
            async with self.game.locks[uid]:
                ok, msg = fn()
                panel = ok_panel if ok else (fail_panel or ok_panel)
                await self.show(interaction, panel(uid, msg if ok else "❌ " + msg))
        except NotEnough:
            await self.game.oops(interaction, "You don't have enough for that anymore. Nothing was taken.")
        except Exception:
            log.exception("Form failed for %s", uid)
            await self.game.oops(interaction, "Something went wrong. Your progress is safe.")
        sp = self.bot.get_cog("Space")
        if sp:
            await sp.after_action(interaction, uid)

    # ================================================================ Market

    def market_panel(self, uid, note=None, cat=None, page=None):
        last = self.market_view.get(uid, ("all", 0))
        cat = cat if cat in multi.CATEGORIES else last[0]
        page = last[1] if page is None else page
        multi.expire(self.db)
        listings, pages, page = multi.browse(self.db, cat, page)
        self.market_view[uid] = (cat, page)
        user = self.db.user(uid)
        name, emoji, _ = multi.CATEGORIES[cat]
        lines = []
        for l in listings:
            each = f" ({l.each:,} each)" if l.qty > 1 else ""
            mine = " · **yours**" if l.seller == uid else f" · {discord.utils.escape_markdown(l.seller_name)}"
            lines.append(f"`#{l.id}` {item_emoji(l.item)} **{engine.item_name(l.item)}** x{l.qty:,}\n"
                         f"     💰 **{l.price:,}**{each}{mine} · ends <t:{int(l.expires)}:R>")
        body = "\n".join(lines) or "*Nothing for sale here yet. Be the first: 📤 Sell something.*"
        e = discord.Embed(title=f"🏪 Astro Market · {emoji} {name}", color=MARKET_COLOR,
                          description=(note + "\n\n" if note else "") + body)
        e.set_footer(text=f"Page {page + 1}/{pages} · You have {user['coins']:,} coins · "
                          f"{int(data.MARKET_TAX * 100)}% fee on sales · listings last {data.MARKET_HOURS}h")
        v = discord.ui.View(timeout=None)
        for k, (n, em, _) in multi.CATEGORIES.items():
            v.add_item(B(f"mkt_t_{k}", uid, n, em, discord.ButtonStyle.primary if k == cat else discord.ButtonStyle.secondary,
                         disabled=k == cat))
        v.add_item(B(f"mkt_p_{cat}_{max(0, page - 1)}", uid, None, "◀️", disabled=page == 0, row=1))
        v.add_item(B(f"mkt_n_{cat}_{min(pages - 1, page + 1)}", uid, None, "▶️", disabled=page >= pages - 1, row=1))
        v.add_item(B("mkt_sell", uid, "Sell", "📤", discord.ButtonStyle.success, row=1))
        new = multi.unseen_sales(self.db, uid)
        v.add_item(B("mkt_mine", uid, f"My listings ({new} sold!)" if new else "My listings", "📋",
                     discord.ButtonStyle.success if new else discord.ButtonStyle.secondary, row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=1))
        opts = [discord.SelectOption(label=f"#{l.id} {engine.item_name(l.item)} x{l.qty:,}"[:100], value=str(l.id),
                                     emoji=item_emoji(l.item),
                                     description=f"{l.price:,} coins · from {l.seller_name}"[:100])
                for l in listings if l.seller != uid]
        if opts:
            v.add_item(PanelSelect("mkt_buy", uid, opts, "🛍️ Buy something on this page...", row=2, max_values=1))
        return e, [], v

    def confirm_panel(self, uid, lid):
        l, status = multi.get(self.db, lid)
        if l is None or status != "open" or l.expires < time.time():
            return self.market_panel(uid, "❌ That listing is gone. Someone beat you to it.")
        user = self.db.user(uid)
        e = discord.Embed(title="🛍️ Buy this?", color=MARKET_COLOR, description=(
            f"{item_emoji(l.item)} **{engine.item_name(l.item)}** x{l.qty:,} from **{discord.utils.escape_markdown(l.seller_name)}**\n"
            f"💰 **{l.price:,}** coins" + (f" ({l.each:,} each)" if l.qty > 1 else "") +
            (f"\n🏪 The shop would pay about {int(engine.item_value(l.item) * l.qty * engine.sell_mult(user, self.db)):,} for these."
             if engine.item_value(l.item) else "")))
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        v.add_item(B(f"mkt_yes_{lid}", uid, f"Buy for {l.price:,}"[:80], "🛍️", discord.ButtonStyle.success,
                     disabled=user["coins"] < l.price))
        v.add_item(B("mkt", uid, "Back", "↩️"))
        return e, [], v

    def sell_panel(self, uid, note=None):
        items = {k: q for k, q in self.db.items(uid).items() if multi.tradeable(k)}
        e = discord.Embed(title="📤 Sell on the Astro Market", color=MARKET_COLOR, description=(
            (note + "\n\n" if note else "") +
            "Pick what to sell, then set how many and the **total** price. Your items wait safely in the Market "
            f"until someone buys them. If nobody does in {data.MARKET_HOURS}h they come back to you.\n"
            f"When it sells you get the coins minus a {int(data.MARKET_TAX * 100)}% fee (and a DM). "
            f"You can have {data.MARKET_MAX_LISTINGS} things listed at once."))
        v = discord.ui.View(timeout=None)
        if items:
            ordered = sorted(items.items(), key=lambda kv: -self.game._rank(kv[0]) if kv[0].startswith(("ore:", "mut:"))
                             else -10.0 ** 12 if kv[0].startswith(("tag:", "rock:", "crate:")) else 0)
            more = " (rarest 25 shown)" if len(ordered) > 25 else ""
            opts = [discord.SelectOption(label=f"{engine.item_name(i)} x{q:,}"[:100], value=i, emoji=item_emoji(i))
                    for i, q in ordered[:25]]
            v.add_item(PanelSelect("mkt_pick", uid, opts, f"📤 Pick what to sell...{more}"[:150], row=0, max_values=1))
        else:
            e.description += "\n\n*You have nothing to sell. Go mine something!*"
        v.add_item(B("mkt_mine", uid, "My listings", "📋", row=1))
        v.add_item(B("mkt", uid, "Back to Market", "↩️", row=1))
        return e, [], v

    def mine_panel(self, uid, note=None):
        listings = multi.my_listings(self.db, uid)
        sales = multi.recent_sales(self.db, uid)
        lines = [f"`#{l.id}` {item_emoji(l.item)} **{engine.item_name(l.item)}** x{l.qty:,} · 💰 {l.price:,} · ends <t:{int(l.expires)}:R>"
                 for l in listings]
        e = discord.Embed(title="📋 My listings", color=MARKET_COLOR, description=(
            (note + "\n\n" if note else "") + ("\n".join(lines) or "*You're not selling anything right now.*")))
        if sales:
            e.add_field(name="Recent", inline=False, value="\n".join(
                f"💰 Sold **{engine.item_name(r['item'])}** x{r['qty']:,} to {discord.utils.escape_markdown(r['buyer_name'] or '?')} "
                f"for {r['price'] - multi.tax(r['price']):,} (after fee) <t:{int(r['done_at'])}:R>"
                if r["status"] == "sold" else
                f"⌛ **{engine.item_name(r['item'])}** x{r['qty']:,} didn't sell and came back <t:{int(r['done_at'])}:R>"
                for r in sales)[:1024])
        e.set_footer(text=f"{len(listings)}/{data.MARKET_MAX_LISTINGS} listings")
        v = discord.ui.View(timeout=None)
        if listings:
            opts = [discord.SelectOption(label=f"#{l.id} {engine.item_name(l.item)} x{l.qty:,}"[:100], value=str(l.id),
                                         emoji=item_emoji(l.item), description="Take it off the Market (you get it back)")
                    for l in listings]
            v.add_item(PanelSelect("mkt_cancel", uid, opts, "↩️ Cancel a listing...", row=0, max_values=1))
        v.add_item(B("mkt_sell", uid, "Sell something", "📤", discord.ButtonStyle.success, row=1))
        v.add_item(B("mkt", uid, "Back to Market", "↩️", row=1))
        return e, [], v

    async def market_action(self, interaction, what, uid):
        values = interaction.extras.get("values") or []
        if what == "":
            await self.show(interaction, self.market_panel(uid))
        elif what.startswith("t_"):
            await self.show(interaction, self.market_panel(uid, cat=what[2:], page=0))
        elif what.startswith(("p_", "n_")):
            cat, _, page = what[2:].rpartition("_")
            await self.show(interaction, self.market_panel(uid, cat=cat, page=int(page)))
        elif what == "buy":
            if not values:
                await self.show(interaction, self.market_panel(uid))
                return
            await self.show(interaction, self.confirm_panel(uid, int(values[0])))
        elif what.startswith("yes_"):
            ok, msg, l = multi.buy(self.db, uid, int(what[4:]))
            await self.show(interaction, self.market_panel(uid, msg if ok else "❌ " + msg))
            if ok:
                got = l.price - multi.tax(l.price)
                self._bg(self.dm(l.seller, f"💰 **{interaction.user.display_name}** bought your **{l.qty:,}x "
                                           f"{engine.item_name(l.item)}** on the Astro Market. You got **{got:,}** coins "
                                           f"({l.price:,} minus the {int(data.MARKET_TAX * 100)}% fee)."))
        elif what == "sell":
            await self.show(interaction, self.sell_panel(uid))
        elif what == "pick":
            if not values:
                await self.show(interaction, self.sell_panel(uid))
                return
            have = self.db.qty(uid, values[0])
            if not have or not multi.tradeable(values[0]):
                await self.show(interaction, self.sell_panel(uid, "❌ You don't have that anymore."))
                return
            await interaction.response.send_modal(ListModal(self, uid, values[0], have))
        elif what == "mine":
            await self.show(interaction, self.mine_panel(uid))
        elif what == "cancel":
            if not values:
                await self.show(interaction, self.mine_panel(uid))
                return
            ok, msg = multi.cancel(self.db, uid, int(values[0]))
            await self.show(interaction, self.mine_panel(uid, msg if ok else "❌ " + msg))
        else:
            await self.game.ack(interaction)

    # ================================================================ tags

    def tags_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🏷️ Tags", color=discord.Color.teal(), description=(
            (note + "\n\n" if note else "") +
            "Rare tags come from 🌌 Cosmic and 🌀 Tweaked crates and from Tweaker Tim. Use them here or sell them on the 🏪 Market."))
        v = discord.ui.View(timeout=None)
        for t in data.TAGS:
            n = self.db.qty(uid, "tag:" + t.key)
            e.add_field(name=f"{t.emoji} {t.name} (you have {n})", value=t.desc, inline=False)
            v.add_item(B(f"tag_{t.key}", uid, f"Use {t.name.replace(' Tag', '')}", t.emoji,
                         discord.ButtonStyle.success if n else discord.ButtonStyle.secondary, disabled=not n))
        e.add_field(name="Right now", inline=False, value=f"⛏️ Pickaxe: {multi.pick_name(user)}\n"
                                                          f"🏷️ Title: {engine_title(user) or '(none)'}")
        v.add_item(B("mkt", uid, "Market", "🏪", row=1))
        v.add_item(B("inv", uid, "Back", "↩️", row=1))
        return e, [], v

    async def tag_action(self, interaction, what, uid):
        if what == "":
            await self.show(interaction, self.tags_panel(uid))
            return
        if what not in data.TAG_BY_KEY or self.db.qty(uid, "tag:" + what) <= 0:
            await self.show(interaction, self.tags_panel(uid, "❌ You don't have that tag."))
            return
        if what == "rename":
            await interaction.response.send_modal(TextModal(self, uid, "🏷️ Name your pickaxe", "New pickaxe name",
                                                            data.PICK_NAME_MAX, multi.use_rename))
        elif what == "title":
            await interaction.response.send_modal(TextModal(self, uid, "📛 Write your title", "Your title",
                                                            data.TITLE_MAX, multi.use_title))
        else:
            ok, msg = multi.use_enchreset(self.db, uid)
            await self.show(interaction, self.tags_panel(uid, msg if ok else "❌ " + msg))

    # ================================================================ Rock Paper Scissors

    async def _avatar(self, member):
        try:
            return await asyncio.wait_for(member.display_avatar.replace(size=128, static_format="png").read(), 3)
        except Exception:
            return None

    def _rps_states(self, g, reveal=None):
        if reveal:
            return list(reveal)
        return ["ready" if g.picks.get(u) else "wait" for u in (g.a, g.b)]

    def _rps_embed(self, g, text, color=discord.Color.from_rgb(255, 120, 200)):
        bet = f"💰 Bet: **{g.bet:,}** coins each, winner takes it" if g.bet else "No bet, just bragging rights"
        e = discord.Embed(title="🪨📄✂️ Rock Paper Scissors", color=color,
                          description=f"<@{g.a}> vs <@{g.b}>\n{bet}\n\n{text}")
        e.set_image(url="attachment://rps.png")
        if g.ties:
            e.set_footer(text="Ties so far: " + ", ".join(data.RPS[x][1] for x, _ in g.ties))
        return e

    async def _rps_pic(self, g, states, winner=None, tie=False, banner=None):
        names = [g.names[g.a], g.names[g.b]]
        png = await asyncio.to_thread(images.rps_card, names, [g.avatars.get(g.a), g.avatars.get(g.b)], states,
                                      winner, tie, banner)
        return discord.File(io.BytesIO(png), filename="rps.png")

    @app_commands.command(name="rps", description="Challenge someone to Rock Paper Scissors, with an optional coin bet")
    @app_commands.describe(member="Who to play", bet="Coins each of you puts in (winner takes it)")
    async def rps_cmd(self, interaction: discord.Interaction, member: discord.Member,
                      bet: app_commands.Range[int, 0, 10 ** 15] = 0):
        uid = interaction.user.id
        me = self.db.user(uid, interaction.user.display_name)
        if member.bot or member.id == uid:
            await interaction.response.send_message("Pick a real opponent.", ephemeral=True)
            return
        if any(not g.done and {g.a, g.b} == {uid, member.id} for g in self.rps.values()):
            await interaction.response.send_message("You two already have a game going. Finish that one first.", ephemeral=True)
            return
        them = self.db.user(member.id, member.display_name)
        if bet and (me["coins"] < bet or them["coins"] < bet):
            await interaction.response.send_message(f"You both need **{bet:,}** coins for that bet.", ephemeral=True)
            return
        await interaction.response.defer()
        a1, a2 = await asyncio.gather(self._avatar(interaction.user), self._avatar(member))
        await self._rps_start(interaction, uid, member.id, bet, {uid: interaction.user.display_name, member.id: member.display_name},
                              {uid: a1, member.id: a2})

    async def _rps_start(self, interaction, a, b, bet, names, avatars):
        """Posts a new game (the interaction must already be answered with defer())."""
        gid = int(time.time() * 1000) % 1_000_000_000
        while gid in self.rps or gid in self.rps_recent:
            gid += 1
        g = multi.RPSGame(gid, a, b, bet, names, avatars, expires=time.time() + data.RPS_TIMEOUT)
        self.rps[gid] = g
        self.rps_locks[gid] = asyncio.Lock()
        e = self._rps_embed(g, f"Both of you pick in secret: a ❗ means that player has picked. The picks are revealed when "
                               f"you've both chosen.\n<@{b}>, picking means you accept"
                               + (" the bet." if bet else ".") + f" Game ends <t:{int(g.expires)}:R>.")
        try:
            msg = await interaction.followup.send(content=f"<@{b}>", embed=e, file=await self._rps_pic(g, ["wait", "wait"]),
                                                  view=rps_view(gid), allowed_mentions=discord.AllowedMentions(users=True),
                                                  wait=True)
        except discord.HTTPException:
            self._rps_forget(g)
            raise
        self._bg(self._rps_timeout(g, msg))

    async def rps_rematch(self, interaction, gid):
        old = self.rps_recent.get(gid)
        uid = interaction.user.id
        if old is None or time.time() > old.expires:
            await interaction.response.send_message("That rematch already started or ran out. Start a new game with `/rps @player`.",
                                                    ephemeral=True)
            return
        if uid not in (old.a, old.b):
            await interaction.response.send_message("Only the two players can call a rematch.", ephemeral=True)
            return
        other = old.b if uid == old.a else old.a
        if any(not g.done and {g.a, g.b} == {uid, other} for g in self.rps.values()):
            await interaction.response.send_message("You two already have a game going.", ephemeral=True)
            return
        if old.bet and (self.db.user(uid)["coins"] < old.bet or self.db.user(other)["coins"] < old.bet):
            await interaction.response.send_message(f"You both need **{old.bet:,}** coins for a rematch at the same bet.",
                                                    ephemeral=True)
            return
        self.rps_recent.pop(gid, None)
        await interaction.response.defer()
        try:
            await interaction.message.edit(view=None)
        except (discord.HTTPException, AttributeError):
            pass
        names = dict(old.names)
        names[uid] = interaction.user.display_name
        await self._rps_start(interaction, uid, other, old.bet, names, old.avatars)

    async def _rps_timeout(self, g, msg):
        while not g.done and time.time() < g.expires:
            await asyncio.sleep(max(1.0, g.expires - time.time()))
        if g.done:
            return
        async with self.rps_locks.get(g.gid, asyncio.Lock()):
            if g.done:
                return
            g.done = True
            self._rps_forget(g)
        slow = [f"<@{u}>" for u in (g.a, g.b) if u not in g.picks]
        if msg:
            try:
                await msg.edit(embed=self._rps_embed(g, f"⌛ {' and '.join(slow)} didn't pick in time. Game's off, no coins moved.",
                                                     discord.Color.dark_grey()), view=None)
            except discord.HTTPException:
                pass

    def _rps_forget(self, g):
        self.rps.pop(g.gid, None)
        self.rps_locks.pop(g.gid, None)

    async def rps_press(self, interaction, pick, gid):
        if pick == "m":
            await self.rps_rematch(interaction, gid)
            return
        g = self.rps.get(gid)
        uid = interaction.user.id
        if g is None or g.done:
            await interaction.response.send_message("This game is over. Start a new one with `/rps @player`.", ephemeral=True)
            return
        if uid not in (g.a, g.b):
            await interaction.response.send_message("This isn't your game. Challenge someone with `/rps @player`.", ephemeral=True)
            return
        async with self.rps_locks[gid]:
            if g.done:
                await interaction.response.send_message("This game is over.", ephemeral=True)
                return
            name = g.names[uid]
            if pick == "x":
                g.done = True
                self._rps_forget(g)
                await interaction.response.edit_message(
                    embed=self._rps_embed(g, f"🏳️ <@{uid}> backed out. No coins moved.", discord.Color.dark_grey()), view=None)
                return
            if uid in g.picks:
                emoji, nm = data.RPS[g.picks[uid]]
                other = g.b if uid == g.a else g.a
                await interaction.response.send_message(f"You already picked {emoji} **{nm}**. Waiting on <@{other}>.",
                                                        ephemeral=True)
                return
            g.picks[uid] = pick
            emoji, nm = data.RPS[pick]
            if len(g.picks) < 2:
                other = g.b if uid == g.a else g.a
                e = self._rps_embed(g, f"❗ **{discord.utils.escape_markdown(name)}** has picked. Waiting on <@{other}>...")
                await interaction.response.edit_message(embed=e, attachments=[await self._rps_pic(g, self._rps_states(g))])
                await self._whisper(interaction, f"You picked {emoji} **{nm}**. Shh, it stays hidden until <@{other}> picks.")
                return
            pa, pb = g.picks[g.a], g.picks[g.b]
            w = multi.rps_winner(pa, pb)
            if w is None:
                g.ties.append((pa, pb))
                g.picks = {}
                g.round += 1
                g.expires = max(g.expires, time.time() + 120)
                e = self._rps_embed(g, f"🤝 You both picked {data.RPS[pa][0]} **{data.RPS[pa][1]}**. It's a tie! "
                                       f"**Round {g.round}**: pick again.")
                await interaction.response.edit_message(embed=e, attachments=[await self._rps_pic(g, [pa, pb], tie=True)])
                return
            g.done = True
            self._rps_forget(g)
            win, lose = (g.a, g.b) if w == 0 else (g.b, g.a)
            wp, lp = (pa, pb) if w == 0 else (pb, pa)
            try:
                multi.settle_rps(self.db, win, lose, g.bet)
                money = f"\n💰 <@{win}> wins **{g.bet:,}** coins from <@{lose}>." if g.bet else ""
            except NotEnough:
                money = "\n💸 The loser spent the bet money before the game ended, so no coins moved."
            wn = images.safe_name(g.names[win], "WINNER").upper()
            e = self._rps_embed(g, f"{data.RPS[wp][0]} **{data.RPS[wp][1]}** beats {data.RPS[lp][0]} **{data.RPS[lp][1]}**!\n"
                                   f"🏆 <@{win}> wins!{money}", discord.Color.gold())
            now = time.time()
            self.rps_recent = {k: v for k, v in self.rps_recent.items() if v.expires > now}
            g.expires = now + 600          # the Rematch button works for 10 minutes
            self.rps_recent[gid] = g
            again = discord.ui.View(timeout=None)
            again.add_item(RPSButton("m", gid, "Rematch" + (f" ({g.bet:,})" if g.bet else ""), discord.ButtonStyle.success, "🔁"))
            await interaction.response.edit_message(
                embed=e, view=again, attachments=[await self._rps_pic(g, [pa, pb], winner=w, banner=f"{wn} WINS!")])
        sp = self.bot.get_cog("Space")
        if sp:
            await sp.after_action(interaction, win)

    async def _whisper(self, interaction, text):
        try:
            await interaction.followup.send(text, ephemeral=True)
        except discord.HTTPException:
            pass

    # ================================================================ slash commands

    async def _open(self, interaction, panel):
        e, files, v = panel
        await interaction.response.send_message(embed=e, files=as_files(files), view=v)

    @app_commands.command(name="market", description="The Astro Market: buy and sell items with other players")
    async def market_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.market_panel(interaction.user.id, cat="all", page=0))

    @app_commands.command(name="tags", description="Use your Rename, Enchant Reset and Custom Title tags")
    async def tags_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.tags_panel(interaction.user.id))

    @app_commands.command(name="pay", description="Give someone coins")
    @app_commands.describe(member="Who gets the coins", amount="How many coins")
    async def pay_cmd(self, interaction: discord.Interaction, member: discord.Member,
                      amount: app_commands.Range[int, 1, 10 ** 15]):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        if member.bot:
            await interaction.response.send_message("Bots don't need coins. They run on electricity.", ephemeral=True)
            return
        self.db.user(member.id, member.display_name)
        async with self.game.locks[uid]:
            ok, msg = multi.pay(self.db, uid, member.id, amount)
        if not ok:
            await interaction.response.send_message("❌ " + msg, ephemeral=True)
            return
        await interaction.response.send_message(
            f"💸 {interaction.user.mention} sent {member.mention} **{amount:,}** coins.",
            allowed_mentions=discord.AllowedMentions(users=[member]))


def engine_title(user):
    from game import space
    return space.title_of(user)


async def setup(bot):
    bot.add_dynamic_items(RPSButton)
    await bot.add_cog(Multi(bot))
