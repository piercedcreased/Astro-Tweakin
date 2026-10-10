"""The game itself: the /mine panel with buttons, lucky rolls, shop, inventory and rare-drop reveals."""
import asyncio
import collections
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from game import data, engine, images
from game.db import NotEnough
from game.data import LAYERS, ORE_BY_KEY, PARTS, PART_BY_KEY, PICKAXE_BY_KEY, PLANETS, REALMS, TIERS, TIER_ORDER
from game.engine import fmt_chance, fmt_time

log = logging.getLogger("astro-tweakin.game")

TIER_EMOJI = {"common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡",
              "mythic": "🔴", "celestial": "💠", "divine": "🌟", "contraband": "🚨"}


def color_of(rgb):
    return discord.Color.from_rgb(*rgb)


def ore_file(ore, name="ore.png"):
    return discord.File(io.BytesIO(images.ore_icon(ore)), filename=name)


def ore_line(ore, n=None):
    count = f" x{n:,}" if n and n > 1 else ""
    return f"{TIER_EMOJI[ore.tier]} **{ore.name}**{count}  ·  {fmt_chance(ore.chance)}"


def shout_line(name, ore):
    tier = ore.tier_info
    if ore.tier == "contraband":
        return (f"🚨🚔 **{name.upper()} HAS DUG UP CONTRABAND: {ore.name.upper()}!** "
                f"({fmt_chance(ore.chance)}) Nobody tell the space cops.")
    prefix = "ULTRA RARE " if tier.effect >= 3 else ""
    word = f"{prefix}{tier.name.upper()}"
    article = "AN" if word[0] in "AEIOU" else "A"
    ore_name = ore.name[4:] if ore.name.startswith("The ") else ore.name
    return f"🚨 **{name.upper()} HAS DUG UP {article} {word} {ore_name.upper()}!** ({fmt_chance(ore.chance)})"


class PanelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cs:(?P<action>[a-z0-9_]+):(?P<uid>\d+)"):
    """A button that remembers which action it does and whose panel it is, even after a restart."""

    def __init__(self, action, uid, label=None, style=discord.ButtonStyle.secondary, emoji=None, disabled=False, row=None):
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled,
                                           custom_id=f"cs:{action}:{uid}", row=row))
        self.action = action
        self.uid = uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["uid"]), item.label, item.style, item.emoji)

    async def callback(self, interaction):
        cog = interaction.client.get_cog("Game")
        await cog.handle(interaction, self.action, self.uid)


def B(action, uid, label, emoji, style=discord.ButtonStyle.secondary, disabled=False, row=0):
    return PanelButton(action, uid, label, style, emoji, disabled, row)


class Game(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.next_swing = {}
        self.locks = collections.defaultdict(asyncio.Lock)

    # ------------------------------------------------------------ panels

    def status_text(self, user):
        uid = user["id"]
        pick = PICKAXE_BY_KEY[user["pickaxe"]]
        layer = LAYERS[user["layer"] - 1]
        luck = engine.luck_for(self.db, user)
        lines = [
            f"💰 **{user['coins']:,}** coins",
            f"{layer.realm_info.emoji} {layer.realm_info.name}  ·  {layer.planet_info.emoji} **{layer.planet_info.name}**  ·  🕳️ {layer.name}",
            f"⛏️ {pick.name}",
            f"🍀 Luck **x{luck.total:,.2f}**  ·  ⏱️ {engine.cooldown_for(self.db, user):.2g}s  ·  {engine.rolls_for(self.db, user)} ore/swing",
        ]
        buffs = self.db.buffs(uid)
        if buffs:
            names = {"luck": "🍀 Luck", "haste": "⚡ Haste", "rolls": "⛏️ Double Dig"}
            lines.append("✨ " + "  ·  ".join(
                f"{names[k]} x{a:g} ({fmt_time(e - time.time())})" for k, (a, e) in buffs.items()))
        ev = self.db.event()
        if ev:
            lines.append(f"🎉 Server event: **{ev['label']}** x{ev['luck']:g} luck ({fmt_time(ev['expires'] - time.time())})")
        for label, frac in engine.goals(self.db, uid):
            lines.append(f"🎯 {label}  {engine.bar(frac)} {int(frac * 100)}%")
        if user["sluglings"]:
            waiting = min(time.time() - user["last_collect"], data.COLLECT_CAP_HOURS * 3600)
            n = int(waiting // data.SLUGLING_SECONDS_PER_ROLL) * user["sluglings"]
            lines.append(f"🐌 {user['sluglings']} slugling(s) have dug **{n:,}** ore for you")
        return "\n".join(lines)

    def main_view(self, user):
        uid = user["id"]
        v = discord.ui.View(timeout=None)
        roll_wait = engine.lucky_roll_ready(user)
        v.add_item(B("mine", uid, "Mine", "⛏️", discord.ButtonStyle.success))
        v.add_item(B("roll", uid, "Lucky Roll" if roll_wait <= 0 else f"Roll in {fmt_time(roll_wait)}", "🎰",
                     discord.ButtonStyle.primary, disabled=roll_wait > 0))
        v.add_item(B("sell", uid, "Sell", "💰"))
        v.add_item(B("collect", uid, "Collect", "🐌", disabled=user["sluglings"] == 0))
        v.add_item(B("shop", uid, "Shop", "🛒", row=1))
        v.add_item(B("inv", uid, "Inventory", "🎒", row=1))
        v.add_item(B("up", uid, "Up", "⬆️", disabled=user["layer"] <= 1, row=1))
        v.add_item(B("down", uid, "Down", "⬇️", disabled=user["layer"] >= len(LAYERS), row=1))
        on = bool(user["autosell"])
        v.add_item(B("autosell", uid, f"Auto-sell: {'ON' if on else 'OFF'}", "♻️",
                     discord.ButtonStyle.success if on else discord.ButtonStyle.secondary, row=1))
        return v

    def main_panel(self, uid, headline=None, ores=None, note=None):
        user = self.db.user(uid)
        layer = LAYERS[user["layer"] - 1]
        e = discord.Embed(title=f"⛏️ {user['name']}'s Mine · {layer.planet_info.name}", color=color_of(layer.color))
        files = []
        if ores:
            best = max(ores, key=lambda o: o.chance)
            counts = collections.Counter(o.key for o in ores)
            e.description = "\n".join(ore_line(ORE_BY_KEY[k], n) for k, n in
                                      sorted(counts.items(), key=lambda kv: -ORE_BY_KEY[kv[0]].chance))
            e.color = color_of(best.tier_info.color) if best.tier != "common" else e.color
            files.append(ore_file(best))
            e.set_thumbnail(url="attachment://ore.png")
        elif headline is None:
            e.description = f"*{layer.blurb}*\nHit **Mine** to swing your pickaxe."
        if headline:
            e.description = headline + ("\n" + e.description if e.description else "")
        if note:
            e.add_field(name="​", value=note, inline=False)
        e.add_field(name="Status", value=self.status_text(user), inline=False)
        return e, files, self.main_view(user)

    def shop_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🛒 Astro Tweakin Shop", color=discord.Color.from_rgb(90, 230, 120))
        nxt = engine.next_pickaxe(user)
        if nxt:
            mats = ", ".join(f"{q}x {engine.item_name(i)} (have {self.db.qty(uid, i)})" for i, q in nxt.materials.items())
            e.add_field(name=f"⛏️ Craft: {nxt.name}",
                        value=f"{nxt.price:,} coins" + (f" + {mats}" if mats else "") +
                              f"\nLuck x{nxt.luck} · {nxt.cooldown}s · {nxt.rolls} ore/swing", inline=False)
        else:
            e.add_field(name="⛏️ Pickaxe", value="You own the best pickaxe.", inline=False)
        go_label, go_emoji = "Dig deeper", "🕳️"
        if user["max_layer"] < len(LAYERS):
            L = LAYERS[user["max_layer"]]
            if engine.is_new_realm(L):
                go_label, go_emoji = f"Warp to {L.realm_info.name}", "🌀"
            elif engine.is_new_planet(L):
                go_label, go_emoji = f"Launch to {L.planet_info.name}", "🚀"
            mats = "".join(f"\n• {q}x {engine.item_name(i)} (have {self.db.qty(uid, i)})" for i, q in L.materials.items())
            e.add_field(name=f"{go_emoji} {go_label}: {engine.layer_label(L)}",
                        value=f"{L.cost:,} coins · x{L.luck} layer luck, new ores{mats}", inline=False)
        cost = engine.slugling_cost(user["sluglings"])
        e.add_field(name=f"🐌 Hire slugling #{user['sluglings'] + 1}",
                    value=f"{cost:,} coins · digs 1 ore/min while you're away (up to {data.COLLECT_CAP_HOURS}h)", inline=False)
        smeltable = ", ".join(f"{self.db.qty(uid, 'ore:' + b) // data.ORE_PER_BAR}x {n}"
                              for b, (n, _) in data.BARS.items() if self.db.qty(uid, "ore:" + b) >= data.ORE_PER_BAR)
        e.add_field(name="🔥 Smelt", value=f"{data.ORE_PER_BAR} ores → 1 bar (small coin fee). Bars craft pickaxes and sell for more."
                    + (f"\nReady: {smeltable}" if smeltable else ""), inline=False)
        if note:
            e.description = note
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        v.add_item(B("buypick", uid, "Craft pickaxe", "⛏️", discord.ButtonStyle.success, disabled=nxt is None))
        v.add_item(B("dig", uid, go_label[:80], go_emoji, discord.ButtonStyle.success, disabled=user["max_layer"] >= len(LAYERS)))
        v.add_item(B("hire", uid, "Hire slugling", "🐌", discord.ButtonStyle.success))
        v.add_item(B("smelt", uid, "Smelt all", "🔥"))
        v.add_item(B("workshop", uid, "Workshop", "🔧", row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=1))
        banner = images.shop_banner(user["coins"], engine.goals(self.db, uid), nxt)
        files = [discord.File(io.BytesIO(banner), filename="shop.jpg")]
        e.set_image(url="attachment://shop.jpg")
        return e, files, v

    def workshop_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🔧 Rocket Workshop", color=discord.Color.orange(),
                          description=(note + "\n\n" if note else "") +
                          "Build ship parts from ores. You burn them when you launch to a new planet.")
        for part in PARTS:
            mats = ", ".join(f"{q}x {engine.item_name(i)} ({self.db.qty(uid, i)})" for i, q in part.materials.items())
            e.add_field(name=f"{part.name}  ·  you have {self.db.qty(uid, 'part:' + part.key)}",
                        value=f"*{part.blurb}*\n{part.price:,} coins + {mats}", inline=False)
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        for i, part in enumerate(PARTS):
            v.add_item(B("mk_" + part.key, uid, part.name, "🔧", row=i // 4))
        v.add_item(B("shop", uid, "Back to shop", "↩️", row=2))
        return e, [], v

    def inventory_panel(self, uid):
        user = self.db.user(uid)
        items = self.db.items(uid)
        e = discord.Embed(title=f"🎒 {user['name']}'s Inventory", color=discord.Color.dark_teal())
        by_tier = collections.defaultdict(list)
        worth = 0
        mult = engine.sell_mult(user)
        for item, q in items.items():
            kind, key = item.split(":", 1)
            if kind == "ore":
                o = ORE_BY_KEY[key]
                by_tier[o.tier].append((o.chance, f"{o.name} x{q:,}"))
                worth += o.value * q
            elif kind == "bar":
                by_tier["bars"].append((0, f"{data.BARS[key][0]} x{q:,}"))
                worth += data.bar_value(key) * q
            else:
                by_tier["parts"].append((0, f"{PART_BY_KEY[key].name} x{q:,}"))
        for t in list(reversed(TIER_ORDER)) + ["bars", "parts"]:
            if by_tier.get(t):
                name = {"bars": "🧱 Bars", "parts": "🔧 Ship parts"}.get(t) or f"{TIER_EMOJI[t]} {TIERS[t].name}"
                val = ", ".join(s for _, s in sorted(by_tier[t], reverse=True))
                e.add_field(name=name, value=val[:1024], inline=False)
        if not items:
            e.description = "Empty. Go mine something!"
        e.set_footer(text=f"Worth about {int(worth * mult):,} coins · Sell keeps Epic+ ores, bars and pickaxe materials")
        v = discord.ui.View(timeout=None)
        v.add_item(B("sell", uid, "Sell", "💰", discord.ButtonStyle.success))
        v.add_item(B("sellall", uid, "Sell everything (even rares)", "⚠️", discord.ButtonStyle.danger))
        v.add_item(B("back", uid, "Back", "↩️"))
        return e, [], v

    # ------------------------------------------------------------ actions

    async def handle(self, interaction: discord.Interaction, action, uid):
        if interaction.user.id != uid:
            await interaction.response.send_message("That's not your mine! Use `/mine` to open your own.", ephemeral=True)
            return
        self.db.user(uid, interaction.user.display_name)
        async with self.locks[uid]:
            try:
                await self.dispatch_action(interaction, action, uid)
            except NotEnough:
                await self.oops(interaction, "You don't have enough for that anymore. Nothing was taken.")
            except Exception:
                log.exception("Button %s failed for %s", action, uid)
                await self.oops(interaction, "Something went wrong. Your progress is safe, nothing was changed.")

    async def oops(self, interaction, text):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def dispatch_action(self, interaction, action, uid):
        if action.startswith("mk_") and action[3:] in PART_BY_KEY:
            ok, msg = engine.craft_part(self.db, uid, action[3:])
            await self.show(interaction, self.workshop_panel(uid, note=("✅ " if ok else "❌ ") + msg))
            return
        fn = getattr(self, "do_" + action, None)
        if fn:
            await fn(interaction, uid)

    async def show(self, interaction, panel):
        e, files, v = panel
        await interaction.response.edit_message(embed=e, attachments=files, view=v)

    async def do_mine(self, interaction, uid):
        wait = self.next_swing.get(uid, 0) - time.time()
        if wait > 0:
            await interaction.response.defer()   # too fast: quietly ignore the click, no popup
            return
        user = self.db.user(uid)
        self.next_swing[uid] = time.time() + engine.cooldown_for(self.db, user)
        res = engine.swing(self.db, uid)
        head = f"♻️ Auto-sold for **{res.autosold:,}** coins" if res.autosold else None
        await self.show(interaction, self.main_panel(uid, headline=head, ores=res.ores))
        await self.celebrate(interaction, res.best)

    async def celebrate(self, interaction, ore):
        tier = ore.tier_info
        if tier.effect < 1:
            return
        name = interaction.user.display_name
        gif = await asyncio.to_thread(images.reveal_gif, ore, name)
        text = shout_line(name, ore)
        await interaction.followup.send(text, file=discord.File(io.BytesIO(gif), filename=f"{ore.key}.gif"))
        if tier.announce and interaction.guild:
            cid = self.db.announce_channel(interaction.guild.id) or self.bot.default_announce
            if cid and cid != interaction.channel_id:
                ch = self.bot.get_channel(cid)
                if ch:
                    try:
                        await ch.send(text, file=discord.File(io.BytesIO(gif), filename=f"{ore.key}.gif"))
                    except discord.HTTPException:
                        pass

    async def do_roll(self, interaction, uid):
        user = self.db.user(uid)
        if engine.lucky_roll_ready(user) > 0:
            await interaction.response.send_message(
                f"Your Lucky Roll recharges in {fmt_time(engine.lucky_roll_ready(user))}.", ephemeral=True)
            return
        prize = engine.pick_prize()
        msg_text = engine.apply_prize(self.db, uid, prize)
        gif = await asyncio.to_thread(images.reel_gif, prize)
        await self.show(interaction, self.main_panel(uid, headline="🎰 Rolling..."))
        e = discord.Embed(title=f"🎰 {interaction.user.display_name}'s Lucky Roll", color=color_of(prize.color))
        e.set_image(url="attachment://roll.gif")
        msg = await interaction.followup.send(embed=e, file=discord.File(io.BytesIO(gif), filename="roll.gif"), wait=True)
        await asyncio.sleep(2.2)
        e.description = msg_text
        e.set_image(url="attachment://prize.png")
        try:
            await msg.edit(embed=e, attachments=[discord.File(io.BytesIO(images.prize_card(prize)), filename="prize.png")])
            e2, files, v = self.main_panel(uid, headline=f"🎰 {msg_text}")
            await interaction.edit_original_response(embed=e2, attachments=files, view=v)
        except discord.HTTPException:
            pass

    async def do_sell(self, interaction, uid, keep_rare=True):
        sold, coins = engine.sell_all(self.db, uid, keep_rare)
        note = f"💰 Sold **{sold:,}** items for **{coins:,}** coins." if sold else "Nothing to sell (Epic+ ores, bars and pickaxe materials are kept)."
        await self.show(interaction, self.main_panel(uid, headline=note))

    async def do_sellall(self, interaction, uid):
        await self.do_sell(interaction, uid, keep_rare=False)

    async def do_collect(self, interaction, uid):
        ores, elapsed = engine.collect_sluglings(self.db, uid)
        if not ores:
            await self.show(interaction, self.main_panel(uid, headline="🐌 Your sluglings haven't dug anything yet. Give them a minute."))
            return
        best = max(ores, key=lambda o: o.chance)
        sold = engine.autosell(self.db, uid)
        counts = collections.Counter(o.key for o in ores)
        top = sorted(counts.items(), key=lambda kv: -ORE_BY_KEY[kv[0]].chance)[:8]
        lines = "\n".join(ore_line(ORE_BY_KEY[k], n) for k, n in top)
        more = len(counts) - len(top)
        head = f"🐌 Your sluglings dug **{len(ores):,}** ores over {fmt_time(elapsed)}:\n{lines}" + (f"\n…and {more} more kinds" if more > 0 else "") \
            + (f"\n♻️ Auto-sold for **{sold:,}** coins" if sold else "")
        e, files, v = self.main_panel(uid, headline=head)
        files = [ore_file(best)]
        e.set_thumbnail(url="attachment://ore.png")
        await interaction.response.edit_message(embed=e, attachments=files, view=v)
        await self.celebrate(interaction, best)

    async def do_autosell(self, interaction, uid):
        on = engine.toggle_autosell(self.db, uid)
        msg = ("♻️ **Auto-sell is ON.** Common and uncommon ores get sold as you mine them. "
               "Rarer ores and anything you need for your next pickaxe or planet are kept.") if on \
            else "♻️ **Auto-sell is OFF.** Ores stay in your bag until you sell them."
        await self.show(interaction, self.main_panel(uid, headline=msg))

    async def do_shop(self, interaction, uid):
        await self.show(interaction, self.shop_panel(uid))

    async def do_workshop(self, interaction, uid):
        await self.show(interaction, self.workshop_panel(uid))

    async def do_inv(self, interaction, uid):
        await self.show(interaction, self.inventory_panel(uid))

    async def do_back(self, interaction, uid):
        await self.show(interaction, self.main_panel(uid))

    async def do_up(self, interaction, uid):
        ok, msg = engine.ascend(self.db, uid)
        await self.show(interaction, self.main_panel(uid, headline=msg))

    async def do_down(self, interaction, uid):
        user = self.db.user(uid)
        if user["layer"] >= user["max_layer"] and user["max_layer"] < len(LAYERS):
            await self.show(interaction, self.shop_panel(uid, note="You haven't been further than this yet. Unlock the next stop here."))
            return
        ok, msg = engine.descend(self.db, uid)
        await self.show(interaction, self.main_panel(uid, headline=msg))

    async def do_buypick(self, interaction, uid):
        ok, msg = engine.buy_pickaxe(self.db, uid)
        await self.show(interaction, self.shop_panel(uid, note=("✅ " if ok else "❌ ") + msg))

    async def do_dig(self, interaction, uid):
        ok, msg = engine.unlock_next(self.db, uid)
        if ok:
            await self.show(interaction, self.main_panel(uid, headline=msg))
        else:
            await self.show(interaction, self.shop_panel(uid, note="❌ " + msg))

    async def do_hire(self, interaction, uid):
        ok, msg = engine.hire_slugling(self.db, uid)
        await self.show(interaction, self.shop_panel(uid, note=("✅ " if ok else "❌ ") + msg))

    async def do_smelt(self, interaction, uid):
        made, fee = engine.smelt_all(self.db, uid)
        if made:
            note = "🔥 Smelted " + ", ".join(f"{n}x {k}" for k, n in made.items()) + f" for {fee:,} coins."
        else:
            note = f"❌ You need {data.ORE_PER_BAR} of an ore (copper, tin, iron, silver or gold) to smelt a bar."
        await self.show(interaction, self.shop_panel(uid, note=note))

    async def do_rebirthyes(self, interaction, uid):
        ok, msg = engine.rebirth(self.db, uid)
        await self.show(interaction, self.main_panel(uid, headline=("🔁 " if ok else "❌ ") + msg))

    # ------------------------------------------------------------ slash commands

    @app_commands.command(name="mine", description="Open your mine and start digging")
    async def mine(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        e, files, v = self.main_panel(interaction.user.id)
        await interaction.response.send_message(embed=e, files=files, view=v)

    @app_commands.command(name="profile", description="See someone's mining stats")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        user = self.db.user(member.id, member.display_name)
        found = self.db.discovered(member.id)
        e = discord.Embed(title=f"🐌 {member.display_name}", color=discord.Color.green())
        e.add_field(name="Coins", value=f"{user['coins']:,}")
        e.add_field(name="Pickaxe", value=PICKAXE_BY_KEY[user["pickaxe"]].name)
        e.add_field(name="Deepest layer", value=LAYERS[user["max_layer"] - 1].name)
        e.add_field(name="Ores mined", value=f"{user['total_mined']:,}")
        e.add_field(name="Index", value=f"{len(found)}/{len(data.ORES)}")
        e.add_field(name="Rebirths", value=str(user["rebirths"]))
        files = []
        if user["best_ore"]:
            best = ORE_BY_KEY[user["best_ore"]]
            e.add_field(name="Rarest find", value=ore_line(best), inline=False)
            files.append(ore_file(best))
            e.set_thumbnail(url="attachment://ore.png")
        await interaction.response.send_message(embed=e, files=files)

    @app_commands.command(name="index", description="Every ore on a planet and which ones you've found")
    @app_commands.choices(planet=[app_commands.Choice(name=p.name, value=k) for k, p in PLANETS.items()])
    async def index(self, interaction: discord.Interaction, planet: app_commands.Choice[str] = None):
        user = self.db.user(interaction.user.id, interaction.user.display_name)
        pkey = planet.value if planet else LAYERS[user["layer"] - 1].planet
        found = self.db.discovered(interaction.user.id)
        nums = [l.num for l in LAYERS if l.planet == pkey]
        ores = [o for o in data.ORES if min(o.layers) in nums]
        have = sum(1 for o in ores if o.key in found)
        P = PLANETS[pkey]
        e = discord.Embed(title=f"📖 {P.emoji} {P.name} Index  ({have}/{len(ores)})", color=discord.Color.blurple(),
                          description=f"Total found everywhere: {len(found)}/{len(data.ORES)}")
        for t in TIER_ORDER:
            lines = []
            for o in sorted([o for o in ores if o.tier == t], key=lambda o: o.chance):
                where = LAYERS[min(o.layers) - 1].name
                if o.key in found:
                    lines.append(f"✅ **{o.name}** · {fmt_chance(o.chance)} · {where} · x{found[o.key]:,}")
                else:
                    lines.append(f"❔ ??? · {fmt_chance(o.chance)} · {where}")
            if lines:
                e.add_field(name=f"{TIER_EMOJI[t]} {TIERS[t].name}", value="\n".join(lines)[:1024], inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="travel", description="Go to any planet or layer you've already unlocked")
    async def travel(self, interaction: discord.Interaction, place: int):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        ok, msg = engine.travel(self.db, uid, place)
        if not ok:
            await interaction.response.send_message(msg, ephemeral=True)
            return
        e, files, v = self.main_panel(uid, headline=msg)
        await interaction.response.send_message(embed=e, files=files, view=v)

    @travel.autocomplete("place")
    async def travel_autocomplete(self, interaction, current: str):
        user = self.db.user(interaction.user.id)
        return [app_commands.Choice(name=engine.layer_label(l).split(" ", 1)[1], value=l.num)
                for l in LAYERS[:user["max_layer"]] if current.lower() in engine.layer_label(l).lower()][:25]

    @app_commands.command(name="daily", description="Claim your daily coins. Keep a streak going for bigger rewards")
    async def daily(self, interaction: discord.Interaction):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        ok, msg = engine.claim_daily(self.db, uid)
        e = discord.Embed(title="📅 Daily Reward", description=msg,
                          color=discord.Color.green() if ok else discord.Color.dark_grey())
        await interaction.response.send_message(embed=e, ephemeral=not ok)

    @app_commands.command(name="ore", description="Look up any ore: picture, odds, value and where to find it")
    async def ore(self, interaction: discord.Interaction, name: str):
        o = ORE_BY_KEY.get(name) or next((x for x in data.ORES if x.name.lower() == name.lower()), None)
        if not o:
            await interaction.response.send_message("No ore by that name. Pick one from the list as you type.", ephemeral=True)
            return
        found = self.db.discovered(interaction.user.id)
        seen = o.key in found
        where = ", ".join(engine.layer_label(LAYERS[n - 1]).split(" ", 1)[1] for n in o.layers)
        e = discord.Embed(title=f"{TIER_EMOJI[o.tier]} {o.name if seen else '???'}", color=color_of(o.tier_info.color))
        e.add_field(name="Rarity", value=f"{o.tier_info.name} · {fmt_chance(o.chance)}")
        e.add_field(name="Sells for", value=f"{o.value:,} coins")
        e.add_field(name="You've found", value=f"{found.get(o.key, 0):,}")
        e.add_field(name="Found in", value=where[:1024], inline=False)
        files = []
        if seen:
            files.append(ore_file(o))
            e.set_thumbnail(url="attachment://ore.png")
        else:
            e.description = "You haven't found this one yet, so its name and look are hidden."
        await interaction.response.send_message(embed=e, files=files, ephemeral=True)

    @ore.autocomplete("name")
    async def ore_autocomplete(self, interaction, current: str):
        found = self.db.discovered(interaction.user.id)
        return [app_commands.Choice(name=(o.name if o.key in found else f"??? ({fmt_chance(o.chance)})")[:100], value=o.key)
                for o in data.ORES
                if current.lower() in (o.name.lower() if o.key in found else "???")][:25]

    @app_commands.command(name="finds", description="The latest rare finds across the server")
    async def finds(self, interaction: discord.Interaction):
        rows = self.db.q("SELECT f.ore, f.at, u.name FROM finds f LEFT JOIN users u ON u.id = f.user_id "
                         "ORDER BY f.at DESC LIMIT 15")
        lines = [f"{TIER_EMOJI[ORE_BY_KEY[r['ore']].tier]} **{r['name'] or 'Someone'}** found "
                 f"**{ORE_BY_KEY[r['ore']].name}** ({fmt_chance(ORE_BY_KEY[r['ore']].chance)}) <t:{int(r['at'])}:R>"
                 for r in rows if r["ore"] in ORE_BY_KEY]
        e = discord.Embed(title="💎 Recent rare finds", color=discord.Color.gold(),
                          description="\n".join(lines) or "Nobody has found anything 1 in 10,000 or rarer yet.")
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="leaderboard", description="Top miners")
    @app_commands.choices(by=[app_commands.Choice(name="Coins", value="coins"),
                              app_commands.Choice(name="Rarest find", value="best"),
                              app_commands.Choice(name="Ores mined", value="mined")])
    async def leaderboard(self, interaction: discord.Interaction, by: app_commands.Choice[str] = None):
        by = by.value if by else "coins"
        rows = self.db.q("SELECT * FROM users")
        if by == "coins":
            rows = sorted(rows, key=lambda r: (r["rebirths"], r["coins"]), reverse=True)
            fmt = lambda r: f"{r['coins']:,} coins" + (f" · {r['rebirths']} rebirths" if r["rebirths"] else "")
        elif by == "mined":
            rows = sorted(rows, key=lambda r: r["total_mined"], reverse=True)
            fmt = lambda r: f"{r['total_mined']:,} ores"
        else:
            rows = sorted([r for r in rows if r["best_ore"]], key=lambda r: ORE_BY_KEY[r["best_ore"]].chance, reverse=True)
            fmt = lambda r: ore_line(ORE_BY_KEY[r["best_ore"]])
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`#{i + 1}`'} **{r['name']}** · {fmt(r)}" for i, r in enumerate(rows[:10])]
        e = discord.Embed(title="🏆 Leaderboard", description="\n".join(lines) or "Nobody has mined yet.",
                          color=discord.Color.gold())
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="rebirth", description="Reset your progress for permanent luck and sell bonuses")
    async def rebirth(self, interaction: discord.Interaction):
        uid = interaction.user.id
        user = self.db.user(uid, interaction.user.display_name)
        n = user["rebirths"] + 1
        e = discord.Embed(title="🔥 Burn the lab down (Rebirth)", color=discord.Color.purple(), description=(
            f"Rebirth **{n}** gives you **+{int(data.REBIRTH_LUCK * n * 100)}% luck** and "
            f"**+{int(data.REBIRTH_SELL * n * 100)}% sell value** forever.\n\n"
            f"You lose your coins, pickaxe, planets, sluglings, bars, parts and ores below Legendary.\n"
            f"Needs: **{LAYERS[data.REBIRTH_LAYER - 1].name}** unlocked and **{data.REBIRTH_COST:,}** coins."))
        v = discord.ui.View(timeout=None)
        v.add_item(B("rebirthyes", uid, "Rebirth", "🔁", discord.ButtonStyle.danger, disabled=not engine.can_rebirth(user)))
        v.add_item(B("back", uid, "Cancel", "↩️"))
        await interaction.response.send_message(embed=e, view=v)

    @app_commands.command(name="help", description="How to play Astro Tweakin")
    async def help(self, interaction: discord.Interaction):
        e = discord.Embed(title="🚀 Astro Tweakin", color=discord.Color.green(), description=(
            "**/mine** opens your mine. Everything is buttons from there.\n\n"
            "⛏️ **Mine** rolls for ores. Each ore has a 1-in-N chance, and luck divides N.\n"
            "🎰 **Lucky Roll** spins a reel every 10 minutes for a short boost.\n"
            "💰 **Sell** ores for coins. 🔥 **Smelt** them into bars in the shop.\n"
            "🛒 **Shop**: craft pickaxes, dig deeper, hire sluglings.\n"
            "🔧 **Workshop**: build rocket parts from ores, then 🚀 launch to new planets and 🌀 warp to new realms.\n"
            "🐌 **Sluglings** dig while you're offline. Hit **Collect** to bank it.\n"
            "♻️ **Auto-sell** sells common and uncommon ores as you mine.\n"
            "📅 **/daily** gives coins. Keep the streak going for bigger rewards.\n"
            "🔥 **/rebirth** burns the lab down for permanent boosts.\n\n"
            "Every planet hides one ultra-rare piece of 🚨 **contraband**. Legendary and rarer finds "
            "get a special animation and get announced to the server.\n"
            "**/travel** · **/index** · **/ore** · **/finds** · **/profile** · **/leaderboard**"))
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(bot):
    bot.add_dynamic_items(PanelButton)
    await bot.add_cog(Game(bot))
