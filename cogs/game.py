"""The game itself: the /mine panel with buttons, lucky rolls, shop, inventory and rare-drop reveals."""
import asyncio
import collections
import io
import logging
import time
import types

import discord
from discord import app_commands
from discord.ext import commands

from game import data, engine, images, multi, space
from game.db import NotEnough
from game.data import LAYERS, ORE_BY_KEY, PARTS, PART_BY_KEY, PICKAXE_BY_KEY, PLANETS, TIERS, TIER_ORDER
from game.engine import fmt_chance, fmt_time

log = logging.getLogger("astro-tweakin.game")

TIER_EMOJI = {"common": "⚪", "uncommon": "🟢", "rare": "🔵", "epic": "🟣", "legendary": "🟡",
              "mythic": "🔴", "celestial": "💠", "divine": "🌟", "contraband": "🚨"}


def color_of(rgb):
    return discord.Color.from_rgb(*rgb)


def ore_file(ore, name="ore.png"):
    return discord.File(io.BytesIO(images.ore_thumb(ore)), filename=name)


def mut_file(ore, mut, name="ore.png"):
    return discord.File(io.BytesIO(images.mutated_thumb(ore, mut.key)), filename=name)


class Pic:
    """An image for a panel that is only uploaded when it changed. key names what's in the picture: if the
    message already shows that picture, the existing attachment is kept instead of uploading it again."""

    def __init__(self, name, key, make):
        self.name, self.key, self.make = name, key, make

    def file(self):
        return discord.File(io.BytesIO(self.make()), filename=self.name)


def as_files(files):
    """Panel files ready to send in a new message."""
    return [f.file() if isinstance(f, Pic) else f for f in files]


def ore_pic(ore):
    return Pic("ore.png", ore.key, lambda: images.ore_thumb(ore))


def mut_pic(ore, mut):
    return Pic("ore.png", f"{ore.key}:{mut.key}", lambda: images.mutated_thumb(ore, mut.key))


def depth_pic(layer_num):
    return Pic("depth.jpg", f"depth{layer_num}", lambda: images.depth_banner(layer_num))


def find_line(key):
    """A rarest-find entry ('ore:x' or 'mut:shiny:x') as a line of text."""
    o, m = engine.find_label(key)
    if m:
        return f"{m.emoji} **{m.name} {o.name}**  ·  {fmt_chance(engine.find_score(o, m))}"
    return ore_line(o)


def mut_line(ore, mut):
    return f"{mut.emoji} **{mut.name} {ore.name}**! (1 in {mut.chance:,} mutation, x{mut.mult} value)"


def rarest_mut(muts):
    """muts = [(ore, mutation)] -> the most impressive one"""
    return max(muts, key=lambda om: (om[1].chance, om[0].chance)) if muts else None


def ore_line(ore, n=None):
    count = f" x{n:,}" if n and n > 1 else ""
    return f"{TIER_EMOJI[ore.tier]} **{ore.name}**{count}  ·  {fmt_chance(ore)}"


def shout_line(name, ore):
    tier = ore.tier_info
    if ore.tier == "contraband":
        return (f"🚨🚔 **{name.upper()} HAS DUG UP CONTRABAND: {ore.name.upper()}!** "
                f"({fmt_chance(ore)}) Nobody tell the space cops.")
    prefix = "ULTRA RARE " if tier.effect >= 3 else ""
    word = f"{prefix}{tier.name.upper()}"
    article = "AN" if word[0] in "AEIOU" else "A"
    ore_name = ore.name[4:] if ore.name.startswith("The ") else ore.name
    return f"🚨 **{name.upper()} HAS DUG UP {article} {word} {ore_name.upper()}!** ({fmt_chance(ore)})"


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


class PanelSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"cs_sel:(?P<action>[a-z0-9_]+):(?P<uid>\d+)"):
    """A dropdown on a panel (lock items, pick ores to smelt). Works after restarts like PanelButton."""

    def __init__(self, action, uid, options, placeholder=None, row=None, max_values=None):
        options = options or [discord.SelectOption(label="(nothing)", value="none")]
        super().__init__(discord.ui.Select(custom_id=f"cs_sel:{action}:{uid}", options=options[:25],
                                           placeholder=placeholder, min_values=1,
                                           max_values=min(max_values or 25, len(options[:25])), row=row))
        self.action = action
        self.uid = uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["uid"]), list(item.options), item.placeholder, max_values=item.max_values)

    async def callback(self, interaction):
        interaction.extras["values"] = [v for v in interaction.data.get("values", []) if v != "none"]
        await interaction.client.get_cog("Game").handle(interaction, self.action, self.uid)


class _CtxResponse:
    def __init__(self, it):
        self.it = it
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, **kw):
        self._done = True
        self.it._sent = await self.it._send(content, **kw)

    async def defer(self, **kw):
        self._done = True

    async def edit_message(self, **kw):
        self._done = True
        if self.it._sent:
            await self.it._sent.edit(**{k: v for k, v in kw.items() if k in ("content", "embed", "embeds", "attachments", "view")})


class CtxInteraction:
    """Lets a ; chat command run the same code as its slash command: looks enough like a discord.Interaction
    for our commands, and turns their replies into normal channel messages (ephemeral ones vanish after a minute)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.user = ctx.author
        self.guild = ctx.guild
        self.guild_id = ctx.guild.id if ctx.guild else None
        self.channel = ctx.channel
        self.channel_id = ctx.channel.id if ctx.channel else None
        self.client = ctx.bot
        self.message = None
        self.command = None
        self.extras = {}
        self._sent = None
        self.response = _CtxResponse(self)
        self.followup = types.SimpleNamespace(send=self._followup)

    async def _send(self, content=None, *, ephemeral=False, wait=None, **kw):
        kw = {k: v for k, v in kw.items() if k in ("embed", "embeds", "file", "files", "view", "allowed_mentions")}
        if ephemeral:
            kw.pop("view", None)
            return await self.ctx.reply(content, mention_author=False, delete_after=60, **kw)
        return await self.ctx.send(content, **kw)

    async def _followup(self, content=None, **kw):
        return await self._send(content, **kw)

    async def edit_original_response(self, **kw):
        if self._sent:
            return await self._sent.edit(**{k: v for k, v in kw.items() if k in ("content", "embed", "embeds", "attachments", "view")})

    async def original_response(self):
        return self._sent


class Game(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.next_swing = {}
        self.locks = collections.defaultdict(asyncio.Lock)
        self.attached = {}        # message id -> {filename: picture key} of what that panel message shows
        self.bg_tasks = set()

    # ------------------------------------------------------------ panels

    BUFF_NAMES = {"luck": "🍀 Luck", "rolls": "⛏️ Double Dig", "echo": "🔁 Echo Swing", "sell": "💰 Midas Touch",
                  "mutluck": "🧬 Mutation Magnet", "snack_luck": "🧃 Snack Luck", "nocops": "🧴 Cop Repellent"}

    def status_text(self, user):
        uid = user["id"]
        pick = PICKAXE_BY_KEY[user["pickaxe"]]
        layer = LAYERS[user["layer"] - 1]
        luck = engine.luck_for(self.db, user)
        title = space.title_of(user)
        buffs = self.db.buffs(uid)
        lines = [f"🏷️ *{title}*"] if title else []
        lines += [
            f"💰 **{user['coins']:,}** coins",
            f"{layer.realm_info.emoji} {layer.realm_info.name}  ·  {layer.planet_info.emoji} **{layer.planet_info.name}**  ·  🕳️ {layer.name}",
            f"⛏️ {multi.pick_name(user)}",
            f"🍀 Luck **x{luck.total:,.2f}**  ·  {engine.rolls_for(self.db, user, buffs)} ore/swing",
        ]
        buffs = {k: v for k, v in buffs.items() if k in self.BUFF_NAMES}
        if buffs:
            lines.append("✨ " + "  ·  ".join(
                f"{self.BUFF_NAMES[k]}{'' if k == 'nocops' else f' x{a:g}'} ({fmt_time(e - time.time())})"
                for k, (a, e) in buffs.items()))
        ev = self.db.event()
        if ev:
            lines.append(f"🎉 Server event: **{ev['label']}** x{ev['luck']:g} luck ({fmt_time(ev['expires'] - time.time())})")
        w = engine.user_weather(user)
        lines.append(f"{w.emoji} Weather: **{w.name}**" + (f" ({w.blurb})" if w.key != "clear" else "")
                     + f" · {fmt_time(engine.weather_ends() - time.time())} left")
        ench = engine.enchants(self.db, uid)
        if ench:
            lines.append("✨ " + "  ·  ".join(engine.enchant_text(r["ench"], r["level"]).replace("**", "").split(":")[0]
                                             for r in ench.values()))
        if user["sluglings"]:
            waiting = min(time.time() - user["last_collect"], engine.collect_cap(user) * 3600)
            n = engine.slug_output(self.db, user, waiting)
            rank = data.SLUG_RANKS[user["slug_rank"]]
            lines.append(f"{rank[1]} {user['sluglings']} {rank[0].lower()} have dug **{n:,}** ore for you")
        return "\n".join(lines)

    def bounties_ready(self, uid):
        """How many of today's bounties are finished but not cashed in yet."""
        try:
            return space.bounties_ready(self.db, uid)
        except Exception:
            log.exception("Couldn't count ready bounties for %s", uid)
            return 0

    def main_view(self, user, ready=0):
        uid = user["id"]
        v = discord.ui.View(timeout=None)
        roll_wait = engine.lucky_roll_ready(user)
        v.add_item(B("mine", uid, "Mine", "⛏️", discord.ButtonStyle.success))
        v.add_item(B("roll", uid, "Lucky Roll" if roll_wait <= 0 else f"Roll in {fmt_time(roll_wait)}", "🎰",
                     discord.ButtonStyle.primary, disabled=roll_wait > 0))
        v.add_item(B("collect", uid, "Collect", "🐌", disabled=user["sluglings"] == 0))
        v.add_item(B("arcade", uid, "Arcade", "🎮"))
        v.add_item(B("mines", uid, "Mines", "🕳️", row=1))
        v.add_item(B("inv", uid, "Inventory", "🎒", row=1))
        v.add_item(B("shop", uid, "Shop", "🛒", row=1))
        v.add_item(B("player", uid, f"Player ({ready} ready!)" if ready else "Player", "👤",
                     discord.ButtonStyle.success if ready else discord.ButtonStyle.secondary, row=1))
        v.add_item(B("settings", uid, None, "⚙️", row=1))
        return v

    def main_panel(self, uid, headline=None, ores=None, note=None, muts=None):
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
            # pictures are only uploaded when they change (see show()), so this stays fast
            if muts:
                mo, mm = rarest_mut(muts)
                e.description = "\n".join(mut_line(o, m) for o, m in muts) + "\n" + e.description
                e.color = color_of(mm.color)
                files.append(mut_pic(mo, mm))
            else:
                files.append(ore_pic(best))
            e.set_thumbnail(url="attachment://ore.png")
        elif headline is None:
            e.description = f"*{layer.blurb}*\nHit **Mine** to swing your pickaxe."
        if headline:
            e.description = headline + ("\n" + e.description if e.description else "")
        ready = self.bounties_ready(uid)
        if ready:
            e.description = (e.description or "") + f"\n\n📜 **{ready} bounty{' is' if ready == 1 else 'ies are'} ready to claim!** (👤 Player → Bounties)"
        if note:
            e.add_field(name="​", value=note, inline=False)
        status = self.status_text(user)
        while status:
            # an embed field holds 1024 characters; spill long statuses into a second field
            cut = status if len(status) <= 1024 else status[:status.rfind("\n", 0, 1024)]
            e.add_field(name="Status" if not e.fields or e.fields[-1].name != "Status" else "​", value=cut, inline=False)
            status = status[len(cut):].lstrip("\n")
        files.append(depth_pic(layer.num))
        e.set_image(url="attachment://depth.jpg")
        return e, files, self.main_view(user, ready)

    def mines_panel(self, uid, note=None):
        """Pick which cave layer to mine in (replaces the old Up/Down buttons)."""
        user = self.db.user(uid)
        lines = []
        for L in LAYERS:
            if L.num <= user["max_layer"]:
                mark = "📍" if L.num == user["layer"] else "✅"
                lines.append(f"{mark} **{L.num}.** {engine.layer_label(L)}  ·  x{L.luck:g} luck")
            elif L.num == user["max_layer"] + 1:
                lines.append(f"🔒 **{L.num}.** {engine.layer_label(L)}  ·  {L.cost:,} coins to unlock")
            else:
                lines.append(f"🔒 **{L.num}.** ???")
        e = discord.Embed(title="🕳️ Mines", color=color_of(LAYERS[user["layer"] - 1].color),
                          description=(note + "\n\n" if note else "") + "\n".join(lines))
        e.set_footer(text="Pick a layer below to go there. Unlocking a new one also needs materials (see the Shop).")
        v = discord.ui.View(timeout=None)
        opts = [discord.SelectOption(label=f"{L.num}. {L.planet_info.name} · {L.name}"[:100], value=str(L.num),
                                     emoji=L.planet_info.emoji, default=L.num == user["layer"])
                for L in LAYERS[:user["max_layer"]]]
        v.add_item(PanelSelect("minesgo", uid, opts, "🕳️ Go to layer...", row=0, max_values=1))
        if user["max_layer"] < len(LAYERS):
            nxt = LAYERS[user["max_layer"]]
            verb = "Warp to" if engine.is_new_realm(nxt) else "Launch to" if engine.is_new_planet(nxt) else "Dig to"
            v.add_item(B("minesdig", uid, f"{verb} {nxt.name}"[:80], "🔓", discord.ButtonStyle.success, row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=1))
        return e, [], v

    def player_panel(self, uid, note=None):
        user = self.db.user(uid)
        stats = self.db.stats(uid)
        ready = self.bounties_ready(uid)
        e = discord.Embed(title=f"👤 {user['name']}", color=discord.Color.green(), description=note)
        if space.title_of(user):
            e.description = (note + "\n" if note else "") + f"🏷️ *{space.title_of(user)}*"
        e.add_field(name="Coins", value=f"{user['coins']:,}")
        e.add_field(name="Ores mined", value=f"{user['total_mined']:,}")
        e.add_field(name="Rebirths", value=str(user["rebirths"]))
        e.add_field(name="Index", value=f"{len(self.db.discovered(uid))}/{len(data.ORES)}")
        e.add_field(name="Achievements", value=f"{len(space.unlocked(self.db, uid))}/{len(data.ACHIEVEMENTS)}")
        e.add_field(name="Bounties done", value=f"{stats.get('bounties_done', 0):,}")
        if user["best_find"]:
            e.add_field(name="Rarest find", value=find_line(user["best_find"]), inline=False)
        if ready:
            e.add_field(name="📜 Bounties", value=f"**{ready}** ready to claim!", inline=False)
        v = discord.ui.View(timeout=None)
        v.add_item(B("bounties", uid, f"Bounties ({ready} ready!)" if ready else "Bounties", "📜",
                     discord.ButtonStyle.success if ready else discord.ButtonStyle.secondary))
        v.add_item(B("trophies", uid, "Achievements", "🏆"))
        v.add_item(B("lb", uid, "Leaderboard", "📊"))
        day, wait, _ = engine.calendar_state(user)
        v.add_item(B("cal", uid, f"Daily (day {day} ready!)" if wait <= 0 else "Daily", "📅",
                     discord.ButtonStyle.success if wait <= 0 else discord.ButtonStyle.secondary))
        v.add_item(B("mkt", uid, "Market", "🏪", row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=1))
        return e, [], v

    def settings_panel(self, uid, note=None):
        user = self.db.user(uid)
        on = bool(user["autosell"])
        e = discord.Embed(title="⚙️ Settings", color=discord.Color.dark_grey(), description=(note + "\n\n" if note else "") + (
            f"♻️ **Auto-sell: {'ON' if on else 'OFF'}**\nSells common and uncommon ores as you mine them. "
            "Rarer ores, mutations, locked items and anything your next pickaxe or planet needs are always kept."))
        v = discord.ui.View(timeout=None)
        v.add_item(B("autosell", uid, f"Auto-sell: {'ON' if on else 'OFF'}", "♻️",
                     discord.ButtonStyle.success if on else discord.ButtonStyle.secondary))
        v.add_item(B("back", uid, "Back", "↩️"))
        return e, [], v

    def shop_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🛒 Astro Tweakin Shop", color=discord.Color.from_rgb(90, 230, 120))
        nxt = engine.next_pickaxe(user)
        if nxt:
            mats = ", ".join(f"{q}x {engine.item_name(i)} (have {self.db.qty(uid, i)})" for i, q in nxt.materials.items())
            e.add_field(name=f"⛏️ Craft: {nxt.name}",
                        value=f"{nxt.price:,} coins" + (f" + {mats}" if mats else "") +
                              f"\nLuck x{nxt.luck} · {nxt.rolls} ore/swing", inline=False)
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
                    value=f"{cost:,} coins · digs while you're away (up to {engine.collect_cap(user)}h). "
                          "Prestige and backpacks in the 🐌 Slug Den.", inline=False)
        smeltable = ", ".join(f"{self.db.qty(uid, 'ore:' + b) // data.ORE_PER_BAR}x {n}"
                              for b, (n, _) in data.BARS.items() if self.db.qty(uid, "ore:" + b) >= data.ORE_PER_BAR)
        e.add_field(name="🔥 Smelter", value=f"{data.ORE_PER_BAR} ores → 1 bar (small coin fee). Bars craft pickaxes and sell for more. "
                    "Open the Smelter to pick which bars to make."
                    + (f"\nReady: {smeltable}" if smeltable else ""), inline=False)
        if note:
            e.description = note
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        v.add_item(B("buypick", uid, "Craft pickaxe", "⛏️", discord.ButtonStyle.success, disabled=nxt is None))
        v.add_item(B("dig", uid, go_label[:80], go_emoji, discord.ButtonStyle.success, disabled=user["max_layer"] >= len(LAYERS)))
        v.add_item(B("hire", uid, "Hire slugling", "🐌", discord.ButtonStyle.success))
        v.add_item(B("smelter", uid, "Smelter", "🔥"))
        v.add_item(B("workshop", uid, "Workshop", "🔧", row=1))
        v.add_item(B("en", uid, "Enchant", "✨", row=1))
        v.add_item(B("sl", uid, "Slug Den", "🐌", row=1))
        v.add_item(B("cr", uid, "Crates", "📦", row=1))
        v.add_item(B("mkt", uid, "Market", "🏪", discord.ButtonStyle.primary, row=2))
        v.add_item(B("back", uid, "Back", "↩️", row=2))
        goals = engine.goals(self.db, uid)
        key = f"shop:{user['coins']}:{nxt.key if nxt else ''}:{goals}"
        files = [Pic("shop.jpg", key, lambda: images.shop_banner(user["coins"], goals, nxt))]
        e.set_image(url="attachment://shop.jpg")
        return e, files, v

    def smelter_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🔥 Smelter", color=discord.Color.from_rgb(255, 120, 40),
                          description=(note + "\n\n" if note else "") +
                          f"Pick which bars to make. Each bar takes {data.ORE_PER_BAR} ore plus a small coin fee.")
        v = discord.ui.View(timeout=None)
        for b, (name, fee) in data.BARS.items():
            ore = self.db.qty(uid, "ore:" + b)
            can = ore // data.ORE_PER_BAR
            if fee:
                can = min(can, user["coins"] // fee)
            e.add_field(name=name, value=f"{ore:,} {ORE_BY_KEY[b].name} ore · can make **{can:,}**\n"
                                         f"{fee:,} coins each · you have {self.db.qty(uid, 'bar:' + b):,} bars", inline=True)
            short = name.replace(" Bar", "")
            v.add_item(B(f"smeltall_{b}", uid, f"All {short} ({can:,})", "🔥", discord.ButtonStyle.success, disabled=can <= 0, row=0))
            v.add_item(B(f"smeltone_{b}", uid, f"1 {name}", "🧱", disabled=can <= 0, row=1))
        e.set_footer(text=f"You have {user['coins']:,} coins · Smelt everything skips 🔒 locked ores")
        v.add_item(B("smelt", uid, "Smelt everything", "🔥", row=2))
        v.add_item(B("shop", uid, "Back to shop", "↩️", row=2))
        return e, [], v

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

    @staticmethod
    def _known(item):
        try:
            engine.item_name(item)
            return True
        except (KeyError, ValueError):
            return False

    def inventory_panel(self, uid, note=None):
        user = self.db.user(uid)
        items = {k: q for k, q in self.db.items(uid).items() if self._known(k)}
        locked = self.db.locked(uid)
        lk = lambda item: "🔒" if item in locked else ""
        e = discord.Embed(title=f"🎒 {user['name']}'s Inventory", color=discord.Color.dark_teal(), description=note)
        by_tier = collections.defaultdict(list)
        worth = 0
        mult = engine.sell_mult(user, self.db)
        for item, q in items.items():
            kind, key = item.split(":", 1)
            if kind == "ore":
                o = ORE_BY_KEY[key]
                by_tier[o.tier].append((o.chance, f"{lk(item)}{o.name} x{q:,}"))
                worth += o.value * q
            elif kind == "bar":
                by_tier["bars"].append((0, f"{lk(item)}{data.BARS[key][0]} x{q:,}"))
                worth += data.bar_value(key) * q
            elif kind == "snack":
                s = data.SNACK_BY_KEY[key]
                by_tier["snacks"].append((0, f"{s.emoji} {s.name} x{q:,}"))
            elif kind == "part":
                by_tier["parts"].append((0, f"{PART_BY_KEY[key].name} x{q:,}"))
            elif kind == "mut":
                m, o = engine.split_mut(key)
                by_tier["muts"].append((m.chance * o.chance, f"{lk(item)}{m.emoji} {m.name} {o.name} x{q:,}"))
                worth += o.value * m.mult * q
            elif kind == "crate":
                c = data.CRATE_BY_KEY[key]
                by_tier["special"].append((c.price or 10 ** 9, f"{c.emoji} {c.name} x{q:,}"))
            elif kind == "rock":
                by_tier["special"].append((10 ** 10, f"💎 Enchanted Crack Rock x{q:,}"))
            elif kind == "tag":
                t = data.TAG_BY_KEY[key]
                by_tier["special"].append((10 ** 11, f"{t.emoji} {t.name} x{q:,}"))
        for t in ["special", "muts"] + list(reversed(TIER_ORDER)) + ["bars", "parts", "snacks"]:
            if by_tier.get(t):
                name = {"bars": "🧱 Bars", "parts": "🔧 Ship parts", "snacks": "🍬 Snacks", "muts": "🧬 Mutations",
                        "special": "✨ Special"}.get(t) \
                    or f"{TIER_EMOJI[t]} {TIERS[t].name}"
                val = ", ".join(s for _, s in sorted(by_tier[t], reverse=True))
                e.add_field(name=name, value=val[:1024], inline=False)
        if not items:
            e.description = "Empty. Go mine something!"
        e.set_footer(text=f"Worth about {int(worth * mult):,} coins · Sell junk keeps Epic+ ores, mutations, bars and "
                          f"pickaxe materials · 🔒 locked items are never sold")
        v = discord.ui.View(timeout=None)
        v.add_item(B("sellsafe", uid, "Sell junk", "💰", discord.ButtonStyle.success))
        v.add_item(B("sellall", uid, "Sell everything...", "⚠️", discord.ButtonStyle.danger))
        v.add_item(B("snacks", uid, "Snacks", "🍬"))
        v.add_item(B("ship", uid, "Ship", "🚀"))
        v.add_item(B("cr", uid, "Crates", "📦", row=3))
        v.add_item(B("en", uid, "Enchant", "✨", row=3))
        n_tags = sum(q for i, q in items.items() if i.startswith("tag:"))
        v.add_item(B("tags", uid, f"Tags ({n_tags})" if n_tags else "Tags", "🏷️", row=3))
        v.add_item(B("mkt", uid, "Market", "🏪", row=3))
        v.add_item(B("back", uid, "Back", "↩️", row=3))
        sellable = [(i, q) for i, q in items.items() if i.split(":", 1)[0] in ("ore", "bar", "mut") and i not in locked]
        if sellable:
            # commonest first: that's usually what you want gone
            sellable.sort(key=lambda kv: self._rank(kv[0]))
            opts = [discord.SelectOption(label=f"{engine.item_name(i)} x{q:,}"[:100], value=i,
                                         description=f"Sell all for about {int(engine.item_value(i) * q * mult):,} coins"[:100])
                    for i, q in sellable[:25]]
            v.add_item(PanelSelect("sellpick", uid, opts, "💰 Pick exactly what to sell..."[:150], row=1))
        opts = []
        for item, q in sorted(items.items(), key=lambda kv: -self._rank(kv[0])):
            if item.split(":", 1)[0] not in ("ore", "bar", "mut"):
                continue
            on = item in locked
            opts.append(discord.SelectOption(label=f"{engine.item_name(item)} x{q:,}"[:100], value=item, emoji="🔒" if on else None,
                                             description="Locked. Pick to unlock" if on else "Pick to lock (never sold)"))
        if opts:
            more = " (rarest 25 shown, /lock for the rest)" if len(opts) > 25 else ""
            v.add_item(PanelSelect("lockpick", uid, opts, f"🔒 Lock or unlock items...{more}"[:150], row=2))
        return e, [], v

    def sellall_confirm_panel(self, uid):
        user = self.db.user(uid)
        e = discord.Embed(title="⚠️ Sell everything?", color=discord.Color.red(), description=(
            "This sells **every** ore, mutation and bar you have, including Epic+ ores and mutations. "
            "Only 🔒 locked items and what your next pickaxe or planet needs are kept.\n\n"
            "Use **Pick exactly what to sell** in your Inventory if you only want some of it gone."))
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        v.add_item(B("sellallyes", uid, "Yes, sell everything", "⚠️", discord.ButtonStyle.danger))
        v.add_item(B("inv", uid, "Cancel", "↩️"))
        return e, [], v

    @staticmethod
    def _rank(item):
        kind, key = item.split(":", 1)
        if kind == "ore":
            return ORE_BY_KEY[key].chance
        if kind == "mut":
            m, o = engine.split_mut(key)
            return m.chance * o.chance
        return 0

    # ------------------------------------------------------------ actions

    async def handle(self, interaction: discord.Interaction, action, uid):
        if interaction.user.id != uid:
            await interaction.response.send_message("Those buttons belong to someone else. Use `/mine` to open your own.",
                                                    ephemeral=True)
            return
        if interaction.guild and not await self.bot.channel_allowed(interaction):
            return
        self.db.user(uid, interaction.user.display_name)
        sp = self.bot.get_cog("Space")
        if sp and sp.busy(uid) and not action.startswith("cc_"):
            await interaction.response.send_message("🚔 The Space Authority is searching your ship! Deal with them first.",
                                                    ephemeral=True)
            return
        lock = self.locks[uid]
        if lock.locked() and (action == "mine" or action.startswith("ar_")):
            # still busy with your last press: drop this one quietly instead of queueing it
            await self.ack(interaction)
            return
        dog = asyncio.create_task(self._watchdog(interaction, action))
        try:
            async with lock:
                try:
                    await self.dispatch_action(interaction, action, uid)
                    if sp:
                        await sp.after_action(interaction, uid)
                except NotEnough:
                    await self.oops(interaction, "You don't have enough for that anymore. Nothing was taken.")
                except discord.NotFound as e:
                    # 10062 = Discord gave up waiting for the answer (the click shows "interaction failed")
                    log.warning("Button '%s' for %s expired before it was answered: %s", action, uid, e)
                    self.db.log_usage(interaction.guild_id, uid, "expired", action)
                except Exception:
                    log.exception("Button '%s' failed for %s (%s) in #%s", action, interaction.user.display_name, uid,
                                  getattr(interaction.channel, "name", "?"))
                    await self.oops(interaction, "Something went wrong. Your progress is safe.")
        finally:
            dog.cancel()

    @staticmethod
    def _rlock(interaction):
        return interaction.extras.setdefault("respond_lock", asyncio.Lock())

    async def ack(self, interaction):
        """Answers a press without changing anything (only if nothing answered it yet)."""
        async with self._rlock(interaction):
            if not interaction.response.is_done():
                try:
                    await interaction.response.defer()
                except discord.HTTPException:
                    pass

    async def _watchdog(self, interaction, action):
        """Discord only waits 3 seconds for an answer. If a press is still busy after 2, answer it now
        (as "working on it") so it never shows "This interaction failed"; the panel edit lands right after."""
        await asyncio.sleep(2.0)
        async with self._rlock(interaction):
            if interaction.response.is_done():
                return
            try:
                await interaction.response.defer()
            except discord.HTTPException:
                return
        self.db.log_usage(interaction.guild_id, interaction.user.id, "slow", action)

    async def edit_panel(self, interaction, **kwargs):
        """Edits the panel message, whether or not the press was already answered."""
        async with self._rlock(interaction):
            if interaction.response.is_done():
                return await interaction.edit_original_response(**kwargs)
            await interaction.response.edit_message(**kwargs)

    async def oops(self, interaction, text):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def dispatch_action(self, interaction, action, uid):
        if action.startswith(("smeltall_", "smeltone_")) and action[9:] in data.BARS:
            await self.do_smelt(interaction, uid, only=[action[9:]], limit=1 if action.startswith("smeltone_") else None)
            return
        if action.startswith("mk_") and action[3:] in PART_BY_KEY:
            ok, msg = engine.craft_part(self.db, uid, action[3:])
            await self.show(interaction, self.workshop_panel(uid, note=("✅ " if ok else "❌ ") + msg))
            return
        fn = getattr(self, "do_" + action, None)
        if fn:
            await fn(interaction, uid)
            return
        sp = self.bot.get_cog("Space")
        if sp and await sp.dispatch(interaction, action, uid):
            return
        ft = self.bot.get_cog("Features")
        if ft and await ft.dispatch(interaction, action, uid):
            return
        mp = self.bot.get_cog("Multi")
        if not (mp and await mp.dispatch(interaction, action, uid)):
            await self.ack(interaction)

    async def show(self, interaction, panel):
        """Edits the panel message. Pictures (Pic) the message already shows are kept as they are instead of
        being uploaded again; that upload used to be most of a click's lag."""
        e, files, v = panel
        msg = interaction.message
        mid = msg.id if msg else None
        had = self.attached.get(mid, {}) if mid else {}
        current = {a.filename: a for a in (msg.attachments if msg else [])}
        out, keys, changed = [], {}, False
        for f in files:
            if isinstance(f, Pic):
                if had.get(f.name) == f.key and f.name in current:
                    out.append(current[f.name])
                else:
                    out.append(await asyncio.to_thread(f.file))
                    changed = True
                keys[f.name] = f.key
            else:
                out.append(f)
                keys[f.filename] = None
                changed = True
        if set(current) != set(keys):
            changed = True
        kwargs = {"embed": e, "view": v}
        if changed:
            kwargs["attachments"] = out
        await self.edit_panel(interaction, **kwargs)
        if mid:
            if len(self.attached) > 5000:
                self.attached.clear()
            self.attached[mid] = keys

    async def do_mine(self, interaction, uid):
        wait = self.next_swing.get(uid, 0) - time.time()
        if wait > 0:
            await self.ack(interaction)   # too fast: quietly ignore the click, no popup
            return
        ft = self.bot.get_cog("Features")
        if ft and uid in ft.minigames:
            # a minigame is waiting: answer it first (it's quick, and you always get something)
            await ft.start_minigame(interaction, uid)
            return
        user = self.db.user(uid)
        self.next_swing[uid] = time.time() + engine.cooldown_for(self.db, user)
        res = engine.swing(self.db, uid)
        heads = []
        if res.crack:
            heads.append("💎 **You found an ENCHANTED CRACK ROCK!** Use it in ✨ Enchant (Shop) to empty enchant slots.")
        if res.echoed:
            heads.append("🔁 **Echo!** This swing dug double.")
        if res.autosold:
            heads.append(f"♻️ Auto-sold for **{res.autosold:,}** coins")
        head = "\n".join(heads) or None
        if res.minigame and ft:
            found = ", ".join(f"{o.name}" for o in sorted(res.ores, key=lambda o: -o.chance)[:5])
            await ft.start_minigame(interaction, uid, head=(head + "\n" if head else "") + f"⛏️ You dug: {found}")
        else:
            await self.show(interaction, self.main_panel(uid, headline=head, ores=res.ores, muts=res.mutations))
        await self.celebrate(interaction, res.best, res.mutations)
        sp = self.bot.get_cog("Space")
        if sp:
            await sp.maybe_check(interaction, uid)

    MUT_REVEAL_FROM = 1_000   # Shadow and rarer mutations get the big animation

    async def celebrate(self, interaction, ore, muts=None):
        """Posts the rare-find animation in the background so the next click isn't stuck waiting for it."""
        best_mut = rarest_mut(muts)
        if best_mut and best_mut[1].chance < self.MUT_REVEAL_FROM:
            best_mut = None
        if (ore is None or ore.tier_info.effect < 1) and not best_mut:
            return
        task = asyncio.create_task(self._celebrate(interaction, ore, best_mut))
        self.bg_tasks.add(task)
        task.add_done_callback(self.bg_tasks.discard)

    async def _celebrate(self, interaction, ore, best_mut=None):
        try:
            if ore is not None and ore.tier_info.effect >= 1:
                await self._post_reveal(interaction, ore)
            if best_mut:
                await self._post_reveal(interaction, best_mut[0], best_mut[1])
        except discord.HTTPException:
            log.warning("Couldn't post the reveal for %s", ore.key if ore else "?")
        except Exception:
            log.exception("Reveal failed for %s", ore.key if ore else "?")

    async def _post_reveal(self, interaction, ore, mut=None):
        tier = ore.tier_info
        name = interaction.user.display_name
        gif = await asyncio.to_thread(images.reveal_gif, ore, name, None, mut, interaction.user.name)
        if mut:
            text = (f"{mut.emoji} **{name}** found a **{mut.name.upper()} {ore.name.upper()}**! "
                    f"(1 in {mut.chance:,} mutation, worth x{mut.mult})")
            announce = mut.announce
            fname = f"{mut.key}_{ore.key}.gif"
        else:
            text = shout_line(name, ore)
            announce = tier.announce
            fname = f"{ore.key}.gif"
        await interaction.followup.send(text, file=discord.File(io.BytesIO(gif), filename=fname))
        if announce and interaction.guild:
            cid = self.db.announce_channel(interaction.guild.id) or self.bot.default_announce
            if cid and cid != interaction.channel_id:
                ch = self.bot.get_channel(cid)
                if ch:
                    try:
                        await ch.send(text, file=discord.File(io.BytesIO(gif), filename=fname))
                    except discord.HTTPException:
                        pass

    ROLL_HOLD = 4.5   # seconds the reel stays up before the buttons come back

    async def do_roll(self, interaction, uid):
        user = self.db.user(uid)
        if engine.lucky_roll_ready(user) > 0:
            await interaction.response.send_message(
                f"Your Lucky Roll recharges in {fmt_time(engine.lucky_roll_ready(user))}.", ephemeral=True)
            return
        prize = engine.pick_prize()
        msg_text = engine.apply_prize(self.db, uid, prize)
        gif = await asyncio.to_thread(images.reel_gif, prize)
        e = discord.Embed(title=f"🎰 {interaction.user.display_name}'s Lucky Roll", description="Rolling...",
                          color=color_of(prize.color))
        e.set_image(url="attachment://roll.gif")
        # the reel plays inside the /mine panel itself (no extra message), with the buttons hidden
        await self.edit_panel(interaction, embed=e, view=None,
                              attachments=[discord.File(io.BytesIO(gif), filename="roll.gif")])
        mid = interaction.message.id if interaction.message else None
        if mid:
            self.attached[mid] = {"roll.gif": "roll"}
        # finish in the background so the user's lock isn't held while the reel spins
        task = asyncio.create_task(self._finish_roll(interaction, uid, prize, msg_text))
        self.bg_tasks.add(task)
        task.add_done_callback(self.bg_tasks.discard)

    async def _finish_roll(self, interaction, uid, prize, msg_text):
        """Brings the panel back under the reel. The GIF stays on the message (it ends on the prize), so a slow
        connection that only just loaded it still sees it spin and land instead of having it swapped out."""
        await asyncio.sleep(self.ROLL_HOLD)
        try:
            async with self.locks[uid]:
                e, _, v = self.main_panel(uid, headline=f"🎰 {msg_text}")
                e.color = color_of(prize.color)
                e.set_image(url="attachment://roll.gif")
                msg = await interaction.original_response()
                keep = [a for a in msg.attachments if a.filename == "roll.gif"]
                await interaction.edit_original_response(embed=e, view=v, attachments=keep)
        except discord.HTTPException as ex:
            log.warning("Couldn't bring the panel back after a Lucky Roll for %s: %s", uid, ex)

    async def do_sell(self, interaction, uid):
        # old panels still have a Sell button under Mine: it now opens the Inventory instead of selling
        await self.do_inv(interaction, uid)

    async def do_sellsafe(self, interaction, uid):
        sold, coins = engine.sell_all(self.db, uid, True)
        note = f"💰 Sold **{sold:,}** items for **{coins:,}** coins." if sold else \
            "Nothing to sell (Epic+ ores, mutations, bars and pickaxe materials are kept)."
        await self.show(interaction, self.inventory_panel(uid, note=note))

    async def do_sellall(self, interaction, uid):
        await self.show(interaction, self.sellall_confirm_panel(uid))

    async def do_sellallyes(self, interaction, uid):
        sold, coins = engine.sell_all(self.db, uid, keep_rare=False)
        note = f"💰 Sold **{sold:,}** items for **{coins:,}** coins." if sold else "Nothing to sell."
        await self.show(interaction, self.inventory_panel(uid, note=note))

    async def do_sellpick(self, interaction, uid):
        sold, coins, skipped = engine.sell_items(self.db, uid, interaction.extras.get("values", []))
        note = f"💰 Sold **{sold:,}** items for **{coins:,}** coins." if sold else "Nothing was sold."
        if skipped:
            note += "\nKept: " + ", ".join(skipped)
        await self.show(interaction, self.inventory_panel(uid, note=note))

    async def do_collect(self, interaction, uid):
        ores, elapsed, muts = await self.collect_off_loop(uid)
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
        if muts:
            shown = sorted(muts, key=lambda om: -(om[1].chance * om[0].chance))[:6]
            head = "\n".join(mut_line(o, m) for o, m in shown) + (f"\n…and {len(muts) - 6} more mutations" if len(muts) > 6 else "") + "\n" + head
        e, files, v = self.main_panel(uid, headline=head)
        e.set_thumbnail(url="attachment://ore.png")
        bm = rarest_mut(muts)
        await self.show(interaction, (e, [mut_pic(*bm) if bm else ore_pic(best)] + files, v))
        await self.celebrate(interaction, best, muts)

    async def collect_off_loop(self, uid):
        """A big Collect rolls tens of thousands of ores (fast: it uses a plain random generator)."""
        return engine.collect_sluglings(self.db, uid)

    async def do_autosell(self, interaction, uid):
        on = engine.toggle_autosell(self.db, uid)
        await self.show(interaction, self.settings_panel(uid, note=f"♻️ Auto-sell is now **{'ON' if on else 'OFF'}**."))

    async def do_settings(self, interaction, uid):
        await self.show(interaction, self.settings_panel(uid))

    async def do_player(self, interaction, uid):
        await self.show(interaction, self.player_panel(uid))

    async def do_mines(self, interaction, uid):
        await self.show(interaction, self.mines_panel(uid))

    async def do_minesgo(self, interaction, uid):
        try:
            num = int((interaction.extras.get("values") or ["0"])[0])
        except ValueError:
            num = 0
        ok, msg = engine.travel(self.db, uid, num)
        if ok:
            await self.show(interaction, self.main_panel(uid, headline=msg))
        else:
            await self.show(interaction, self.mines_panel(uid, note="❌ " + msg))

    async def do_minesdig(self, interaction, uid):
        ok, msg = engine.unlock_next(self.db, uid)
        if not ok:
            await self.show(interaction, self.mines_panel(uid, note="❌ " + msg))
            return
        await self.show(interaction, self.main_panel(uid, headline=msg))
        sp = self.bot.get_cog("Space")
        if sp and engine.is_new_planet(LAYERS[self.db.user(uid)["layer"] - 1]):
            await sp.maybe_check(interaction, uid, travel=True)

    async def do_shop(self, interaction, uid):
        await self.show(interaction, self.shop_panel(uid))

    async def do_workshop(self, interaction, uid):
        await self.show(interaction, self.workshop_panel(uid))

    async def do_inv(self, interaction, uid):
        await self.show(interaction, self.inventory_panel(uid))

    async def do_back(self, interaction, uid):
        await self.show(interaction, self.main_panel(uid))

    async def do_up(self, interaction, uid):
        # old panels only: Up/Down became the Mines menu
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
            sp = self.bot.get_cog("Space")
            if sp and engine.is_new_planet(LAYERS[self.db.user(uid)["layer"] - 1]):
                await sp.maybe_check(interaction, uid, travel=True)
        else:
            await self.show(interaction, self.shop_panel(uid, note="❌ " + msg))

    async def do_hire(self, interaction, uid):
        ok, msg = engine.hire_slugling(self.db, uid)
        await self.show(interaction, self.shop_panel(uid, note=("✅ " if ok else "❌ ") + msg))

    async def do_smelter(self, interaction, uid):
        await self.show(interaction, self.smelter_panel(uid))

    async def do_lockpick(self, interaction, uid):
        on, off = engine.toggle_locks(self.db, uid, interaction.extras.get("values", []))
        parts = []
        if on:
            parts.append("🔒 Locked " + ", ".join(on))
        if off:
            parts.append("🔓 Unlocked " + ", ".join(off))
        await self.show(interaction, self.inventory_panel(uid, note=". ".join(parts) or None))

    async def do_smelt(self, interaction, uid, only=None, limit=None):
        made, fee = engine.smelt_all(self.db, uid, only=only, limit=limit)
        if made:
            note = "🔥 Smelted " + ", ".join(f"{n}x {k}" for k, n in made.items()) + f" for {fee:,} coins."
        else:
            note = (f"❌ You need {data.ORE_PER_BAR} of an ore (copper, tin, iron, silver or gold) and the coin fee to smelt a bar."
                    + (" Smelt everything skips 🔒 locked ores, so use the buttons above for those." if only is None and self.db.locked(uid) else ""))
        await self.show(interaction, self.smelter_panel(uid, note=note))

    async def do_rebirthyes(self, interaction, uid):
        ok, msg = engine.rebirth(self.db, uid)
        await self.show(interaction, self.main_panel(uid, headline=("🔁 " if ok else "❌ ") + msg))

    # ------------------------------------------------------------ slash commands

    @app_commands.command(name="mine", description="Open your mine and start digging")
    async def mine(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        e, files, v = self.main_panel(interaction.user.id)
        await interaction.response.send_message(embed=e, files=as_files(files), view=v)

    @app_commands.command(name="profile", description="See someone's mining stats")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        user = self.db.user(member.id, member.display_name)
        found = self.db.discovered(member.id)
        e = discord.Embed(title=f"🐌 {member.display_name}", color=discord.Color.green())
        if space.title_of(user):
            e.description = f"🏷️ *{space.title_of(user)}*"
        e.add_field(name="Coins", value=f"{user['coins']:,}")
        e.add_field(name="Pickaxe", value=multi.pick_name(user))
        e.add_field(name="Deepest layer", value=LAYERS[user["max_layer"] - 1].name)
        e.add_field(name="Ores mined", value=f"{user['total_mined']:,}")
        e.add_field(name="Index", value=f"{len(found)}/{len(data.ORES)}")
        e.add_field(name="Rebirths", value=str(user["rebirths"]))
        e.add_field(name="Ship", value=" · ".join(f"{u.emoji} {user['ship_' + u.key]}" for u in data.SHIP_UPGRADES))
        e.add_field(name="Achievements", value=f"{len(space.unlocked(self.db, member.id))}/{len(data.ACHIEVEMENTS)}")
        stats = self.db.stats(member.id)
        e.add_field(name="Smuggling", value=f"🚔 {stats.get('checks_busted', 0)} busts · 😎 {stats.get('checks_survived', 0)} escapes\n"
                                             f"🏴‍☠️ {stats.get('raids_won', 0)} raids · ⚔️ {stats.get('duels_won', 0)} duel wins", inline=False)
        if stats.get("mutations"):
            e.add_field(name=f"🧬 Mutations ({stats['mutations']:,})", inline=False, value=" · ".join(
                f"{m.emoji} {stats.get('mut_' + m.key, 0):,}" for m in reversed(data.MUTATIONS)))
        files = []
        if user["best_find"]:
            o, m = engine.find_label(user["best_find"])
            e.add_field(name="Rarest find", value=find_line(user["best_find"]), inline=False)
            files.append(mut_file(o, m) if m else ore_file(o))
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
                    lines.append(f"✅ **{o.name}** · {fmt_chance(o)} · {where} · x{found[o.key]:,}")
                else:
                    lines.append(f"❔ ??? · {fmt_chance(o)} · {where}")
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
        await interaction.response.send_message(embed=e, files=as_files(files), view=v)

    @travel.autocomplete("place")
    async def travel_autocomplete(self, interaction, current: str):
        user = self.db.user(interaction.user.id)
        return [app_commands.Choice(name=engine.layer_label(l).split(" ", 1)[1], value=l.num)
                for l in LAYERS[:user["max_layer"]] if current.lower() in engine.layer_label(l).lower()][:25]

    @app_commands.command(name="daily", description="Claim today's reward on the 7-day daily calendar")
    async def daily(self, interaction: discord.Interaction):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        ok, msg = engine.claim_daily(self.db, uid)
        e, files, v = self.bot.get_cog("Features").calendar_panel(uid, msg if ok else "⏳ " + msg)
        await interaction.response.send_message(embed=e, files=as_files(files), view=v)

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
        e.add_field(name="Rarity", value=f"{o.tier_info.name} · {fmt_chance(o)}")
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
        return [app_commands.Choice(name=(o.name if o.key in found else f"??? ({fmt_chance(o)})")[:100], value=o.key)
                for o in data.ORES
                if current.lower() in (o.name.lower() if o.key in found else "???")][:25]

    @app_commands.command(name="finds", description="The latest rare finds across the server")
    async def finds(self, interaction: discord.Interaction):
        rows = self.db.q("SELECT f.ore, f.at, u.name FROM finds f LEFT JOIN users u ON u.id = f.user_id "
                         "ORDER BY f.at DESC LIMIT 15")
        lines = [f"{TIER_EMOJI[ORE_BY_KEY[r['ore']].tier]} **{r['name'] or 'Someone'}** found "
                 f"**{ORE_BY_KEY[r['ore']].name}** ({fmt_chance(ORE_BY_KEY[r['ore']])}) <t:{int(r['at'])}:R>"
                 for r in rows if r["ore"] in ORE_BY_KEY]
        e = discord.Embed(title="💎 Recent rare finds", color=discord.Color.gold(),
                          description="\n".join(lines) or "Nobody has found anything 1 in 10,000 or rarer yet.")
        await interaction.response.send_message(embed=e)

    LB_KINDS = {
        "coins": "💰 Coins", "best": "💎 Rarest find", "mined": "⛏️ Ores mined", "deepest": "🚀 Furthest planet",
        "index": "📖 Index (ores found)", "trophies": "🏆 Achievements", "raids": "🏴‍☠️ Raids won",
        "duels": "⚔️ Duels won", "bounties": "📜 Bounties done", "busts": "🚔 Times busted", "mutations": "🧬 Mutations found",
    }

    def leaderboard_embed(self, by, viewer):
        rows = [r for r in self.db.q("SELECT * FROM users")]
        stat = lambda key: {r["user_id"]: r["value"] for r in self.db.q("SELECT user_id, value FROM stats WHERE key=?", key)}
        count = lambda table: {r["user_id"]: r["n"] for r in self.db.q(f"SELECT user_id, COUNT(*) AS n FROM {table} GROUP BY user_id")}
        if by == "coins":
            key = lambda r: (r["rebirths"], r["coins"])
            fmt = lambda r: f"{r['coins']:,} coins" + (f" · {r['rebirths']} rebirths" if r["rebirths"] else "")
        elif by == "mined":
            key = lambda r: r["total_mined"]
            fmt = lambda r: f"{r['total_mined']:,} ores"
        elif by == "deepest":
            key = lambda r: (r["rebirths"], r["max_layer"])
            fmt = lambda r: engine.layer_label(LAYERS[r["max_layer"] - 1]) + (f" · {r['rebirths']} rebirths" if r["rebirths"] else "")
        elif by == "best":
            # counts mutations too: a Galactic Coal can beat a plain Diamond
            rows = [r for r in rows if r["best_find"] and self._known(r["best_find"])]
            key = lambda r: r["best_score"]
            fmt = lambda r: find_line(r["best_find"])
        else:
            vals = {"index": lambda: count("discovered"), "trophies": lambda: count("achievements"),
                    "raids": lambda: stat("raids_won"), "duels": lambda: stat("duels_won"),
                    "bounties": lambda: stat("bounties_done"), "busts": lambda: stat("checks_busted"),
                    "mutations": lambda: stat("mutations")}[by]()
            unit = {"index": f"/{len(data.ORES)} ores", "trophies": f"/{len(data.ACHIEVEMENTS)}", "raids": " raids",
                    "duels": " wins", "bounties": " bounties", "busts": " busts", "mutations": " mutations"}[by]
            rows = [r for r in rows if vals.get(r["id"], 0) > 0]
            key = lambda r: vals.get(r["id"], 0)
            fmt = lambda r: f"{vals.get(r['id'], 0):,}{unit}"
        rows = sorted(rows, key=key, reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'`#{i + 1}`'} **{r['name']}** · {fmt(r)}" for i, r in enumerate(rows[:10])]
        e = discord.Embed(title=f"📊 Leaderboard · {self.LB_KINDS[by]}", description="\n".join(lines) or "Nobody's on this one yet.",
                          color=discord.Color.gold())
        mine = next((i for i, r in enumerate(rows) if r["id"] == viewer), None)
        if mine is not None and mine >= 10:
            e.set_footer(text=f"You're #{mine + 1} · {fmt(rows[mine])}")
        return e

    def leaderboard_panel(self, uid, by="coins"):
        e = self.leaderboard_embed(by, uid)
        v = discord.ui.View(timeout=None)
        opts = [discord.SelectOption(label=label.split(" ", 1)[1], emoji=label.split(" ", 1)[0], value=k, default=k == by)
                for k, label in self.LB_KINDS.items()]
        v.add_item(PanelSelect("lbpick", uid, opts, "Pick a leaderboard...", row=0, max_values=1))
        v.add_item(B("player", uid, "Back", "↩️", row=1))
        return e, [], v

    async def do_lb(self, interaction, uid):
        await self.show(interaction, self.leaderboard_panel(uid))

    async def do_lbpick(self, interaction, uid):
        picked = (interaction.extras.get("values") or ["coins"])[0]
        await self.show(interaction, self.leaderboard_panel(uid, picked if picked in self.LB_KINDS else "coins"))

    @app_commands.command(name="leaderboard", description="Top players: coins, rarest find, raids, achievements and more")
    @app_commands.choices(by=[app_commands.Choice(name=label.split(" ", 1)[1], value=k) for k, label in LB_KINDS.items()])
    async def leaderboard(self, interaction: discord.Interaction, by: app_commands.Choice[str] = None):
        await interaction.response.send_message(embed=self.leaderboard_embed(by.value if by else "coins", interaction.user.id))

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

    @app_commands.command(name="lock", description="Lock or unlock an item so it never gets sold")
    async def lock(self, interaction: discord.Interaction, item: str):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        if self.db.qty(uid, item) <= 0 and item not in self.db.locked(uid):
            await interaction.response.send_message("You don't have that item. Pick one from the list as you type.", ephemeral=True)
            return
        on, off = engine.toggle_locks(self.db, uid, [item])
        msg = f"🔒 **{on[0]}** is locked. Sell, auto-sell, Smelt everything and Tim won't touch it." if on else \
            f"🔓 **{off[0]}** is unlocked." if off else "That item can't be locked."
        await interaction.response.send_message(msg, ephemeral=True)

    @lock.autocomplete("item")
    async def lock_autocomplete(self, interaction, current: str):
        uid = interaction.user.id
        locked = self.db.locked(uid)
        items = sorted(set(self.db.items(uid)) | locked, key=lambda i: -self._rank(i))
        out = []
        for i in items:
            try:
                name = engine.item_name(i)
            except KeyError:
                continue
            if current.lower() in name.lower():
                out.append(app_commands.Choice(name=("🔒 " if i in locked else "") + name, value=i))
        return out[:25]

    @app_commands.command(name="help", description="How to play Astro Tweakin")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=self.help_embed(), ephemeral=True)

    @staticmethod
    def help_embed():
        return discord.Embed(title="🚀 Astro Tweakin", color=discord.Color.green(), description=(
            "**/mine** (or **;mine**) opens your mine. Everything is buttons from there.\n\n"
            "⛏️ **Mine** rolls for ores. Each ore has a 1-in-N chance, and luck divides N.\n"
            "🎰 **Lucky Roll** spins a reel every 10 minutes for a short boost.\n"
            "🕳️ **Mines** picks which cave layer you dig in. 🎒 **Inventory** is where you sell, lock, eat snacks "
            "and fly your ship. 👤 **Player** has bounties, achievements and the leaderboard. ⚙️ has auto-sell.\n"
            "💰 **Sell** ores for coins in your Inventory. 🔥 **Smelt** them into bars in the shop.\n"
            "🛒 **Shop**: craft pickaxes, dig deeper, hire sluglings.\n"
            "🔧 **Workshop**: build rocket parts from ores, then 🚀 launch to new planets and 🌀 warp to new realms.\n"
            "🐌 **Sluglings** dig while you're offline. Hit **Collect** to bank it.\n"
            "♻️ **Auto-sell** sells common and uncommon ores as you mine.\n"
            "📅 **/daily** gives coins. Keep the streak going for bigger rewards.\n"
            "🔥 **/rebirth** burns the lab down for permanent boosts.\n\n"
            "🚔 Carry Legendary+ ore or contraband and the **Space Authority** may pull you over. Hide it, talk, "
            "distract the K-9, bribe or dump it.\n"
            "🚀 **/ship** upgrades your ship · 🏴‍☠️ **/raid** someone's ship for stuff they forgot (they lose nothing) · "
            "⚔️ **/duel** for coins\n"
            "🍬 **/snacks** for boosts · 📜 **/bounties** daily jobs · 🏆 **/achievements** and **/title**\n"
            "🏪 **/market** buy and sell with other players · 🪨 **/rps** @player [bet] · 💸 **/pay** · 🏷️ **/tags**\n"
            "☄️ A **meteor** hits once a day and 👽 **Tweaker Tim** drops by a few times a day in the announce channel.\n\n"
            "Every planet hides one ultra-rare piece of 🚨 **contraband**. Legendary and rarer finds "
            "get a special animation and get announced to the server.\n"
            "🧬 Any ore can come out **mutated**: ✨ Shiny, ☢️ Radioactive, 🌑 Shadow, 🌈 Holographic, 🌌 Galactic or "
            "🌀 Tweaked, worth x3 up to x150. Regular Sell keeps them.\n"
            "**/travel** · **/index** · **/ore** · **/finds** · **/profile** · **/leaderboard**\n\n"
            "⌨️ **Chat commands** with `;`: every slash command works with `;` too, like `;mine` `;inv` `;raid @player` "
            "`;duel @player 500` `;daily` `;crates` `;enchant` `;arcade` `;weather` `;bal` `;help`"))

    # ------------------------------------------------------------ ; chat commands

    async def cog_check(self, ctx):
        """; commands follow /admin botchannel like the slash commands do."""
        if ctx.guild is None:
            return True
        allowed = self.db.bot_channels(ctx.guild.id)
        return not allowed or ctx.channel.id in allowed

    async def _send_panel(self, ctx, panel):
        e, files, v = panel
        await ctx.send(embed=e, files=as_files(files), view=v)

    def _me(self, ctx):
        return self.db.user(ctx.author.id, ctx.author.display_name)["id"]

    @commands.command(name="mine", aliases=["m"])
    async def p_mine(self, ctx):
        await self._send_panel(ctx, self.main_panel(self._me(ctx)))

    @commands.command(name="inv", aliases=["i", "inventory", "bag"])
    async def p_inv(self, ctx):
        await self._send_panel(ctx, self.inventory_panel(self._me(ctx)))

    @commands.command(name="shop", aliases=["s"])
    async def p_shop(self, ctx):
        await self._send_panel(ctx, self.shop_panel(self._me(ctx)))

    @commands.command(name="mines", aliases=["layers", "layer"])
    async def p_mines(self, ctx):
        await self._send_panel(ctx, self.mines_panel(self._me(ctx)))

    @commands.command(name="player", aliases=["p", "me"])
    async def p_player(self, ctx):
        await self._send_panel(ctx, self.player_panel(self._me(ctx)))

    @commands.command(name="bal", aliases=["b", "coins", "balance"])
    async def p_bal(self, ctx):
        user = self.db.user(self._me(ctx))
        await ctx.reply(f"💰 You have **{user['coins']:,}** coins.", mention_author=False)

    @commands.command(name="daily", aliases=["d"])
    async def p_daily(self, ctx):
        uid = self._me(ctx)
        ok, msg = engine.claim_daily(self.db, uid)
        await self._send_panel(ctx, self.bot.get_cog("Features").calendar_panel(uid, msg if ok else "⏳ " + msg))

    @commands.command(name="lb", aliases=["leaderboard", "top"])
    async def p_lb(self, ctx):
        await self._send_panel(ctx, self.leaderboard_panel(self._me(ctx)))

    @commands.command(name="bounties", aliases=["bounty"])
    async def p_bounties(self, ctx):
        await self._send_panel(ctx, self.bot.get_cog("Space").bounty_panel(self._me(ctx)))

    @commands.command(name="snacks", aliases=["snack"])
    async def p_snacks(self, ctx):
        await self._send_panel(ctx, self.bot.get_cog("Space").snack_panel(self._me(ctx)))

    @commands.command(name="ship")
    async def p_ship(self, ctx):
        await self._send_panel(ctx, self.bot.get_cog("Space").ship_panel(self._me(ctx)))

    @commands.command(name="help", aliases=["h"])
    async def p_help(self, ctx):
        await ctx.reply(embed=self.help_embed(), mention_author=False)

    # chat versions of the other slash commands: they run the slash command's own code (see CtxInteraction)

    async def _run(self, ctx, cog_name, cmd, *args):
        cog = self.bot.get_cog(cog_name)
        self.db.user(ctx.author.id, ctx.author.display_name)
        it = CtxInteraction(ctx)
        await getattr(cog, cmd).callback(cog, it, *args)

    @commands.command(name="raid")
    async def p_raid(self, ctx, member: discord.Member):
        await self._run(ctx, "Space", "raid_cmd", member)

    @commands.command(name="duel")
    async def p_duel(self, ctx, member: discord.Member, wager: int = 0):
        await self._run(ctx, "Space", "duel_cmd", member, max(0, wager))

    @commands.command(name="profile", aliases=["prof"])
    async def p_profile(self, ctx, member: discord.Member = None):
        await self._run(ctx, "Game", "profile", member)

    @commands.command(name="achievements", aliases=["ach", "trophies"])
    async def p_achievements(self, ctx, member: discord.Member = None):
        await self._run(ctx, "Space", "achievements_cmd", member)

    @commands.command(name="rebirth")
    async def p_rebirth(self, ctx):
        await self._run(ctx, "Game", "rebirth")

    @commands.command(name="finds")
    async def p_finds(self, ctx):
        await self._run(ctx, "Game", "finds")

    @commands.command(name="rps")
    async def p_rps(self, ctx, member: discord.Member, bet: int = 0):
        await self._run(ctx, "Multi", "rps_cmd", member, max(0, bet))

    @commands.command(name="pay", aliases=["give"])
    async def p_pay(self, ctx, member: discord.Member, amount: str):
        from cogs.multi import parse_amount
        n = parse_amount(amount)
        if not n:
            await ctx.reply("Use it like `;pay @player 5000` (5k and 1.5m work too)", mention_author=False)
            return
        await self._run(ctx, "Multi", "pay_cmd", member, n)

    @commands.command(name="market", aliases=["mkt", "ah", "auction"])
    async def p_market(self, ctx):
        await self._run(ctx, "Multi", "market_cmd")

    @commands.command(name="tags", aliases=["tag"])
    async def p_tags(self, ctx):
        await self._run(ctx, "Multi", "tags_cmd")

    @commands.command(name="weather", aliases=["w"])
    async def p_weather(self, ctx):
        await self._run(ctx, "Features", "weather_cmd")

    @commands.command(name="crates", aliases=["crate", "cr"])
    async def p_crates(self, ctx):
        await self._run(ctx, "Features", "crates_cmd")

    @commands.command(name="enchant", aliases=["en"])
    async def p_enchant(self, ctx):
        await self._run(ctx, "Features", "enchant_cmd")

    @commands.command(name="arcade", aliases=["jack", "bj", "blackjack"])
    async def p_arcade(self, ctx):
        await self._run(ctx, "Features", "arcade_cmd")

    @commands.command(name="travel", aliases=["t", "go"])
    async def p_travel(self, ctx, place: int):
        await self._run(ctx, "Game", "travel", place)

    @commands.command(name="index", aliases=["idx"])
    async def p_index(self, ctx, *, planet: str = None):
        choice = None
        if planet:
            key = next((k for k, p in PLANETS.items() if planet.lower() in (k, p.name.lower())), None)
            if key is None:
                await ctx.reply("No planet by that name. Try: " + ", ".join(p.name for p in PLANETS.values()), mention_author=False)
                return
            choice = types.SimpleNamespace(value=key, name=PLANETS[key].name)
        await self._run(ctx, "Game", "index", choice)

    @commands.command(name="ore")
    async def p_ore(self, ctx, *, name: str):
        await self._run(ctx, "Game", "ore", name)

    @commands.command(name="lock")
    async def p_lock(self, ctx, *, item: str):
        uid = self._me(ctx)
        items = set(self.db.items(uid)) | self.db.locked(uid)
        key = item if item in items else next((i for i in items if self._known(i) and engine.item_name(i).lower() == item.lower()), item)
        await self._run(ctx, "Game", "lock", key)

    @commands.command(name="title")
    async def p_title(self, ctx, *, title: str = "none"):
        key = "none" if title.lower() in ("none", "off", "clear") else next(
            (a.key for a in data.ACHIEVEMENTS if title.lower() in (a.key, a.name.lower())), title)
        await self._run(ctx, "Space", "title_cmd", key)

    async def cog_command_error(self, ctx, error):
        if isinstance(error, (commands.CheckFailure, commands.CommandNotFound)):
            return
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            usage = {"raid": ";raid @player", "duel": ";duel @player [bet]", "rps": ";rps @player [bet]",
                     "pay": ";pay @player <amount>", "travel": ";travel <layer number>",
                     "ore": ";ore <name>", "lock": ";lock <item name>", "title": ";title <achievement name>"}
            try:
                await ctx.reply(f"Use it like `{usage.get(ctx.command.name, ';' + ctx.command.name)}`", mention_author=False)
            except discord.HTTPException:
                pass
            return
        original = getattr(error, "original", error)
        log.error("; command %s failed for %s (%s)", ctx.command, ctx.author.display_name, ctx.author.id,
                  exc_info=(type(original), original, original.__traceback__))
        try:
            await ctx.reply("Something went wrong. Your progress is safe.", mention_author=False)
        except discord.HTTPException:
            pass


async def setup(bot):
    bot.add_dynamic_items(PanelButton, PanelSelect)
    await bot.add_cog(Game(bot))
