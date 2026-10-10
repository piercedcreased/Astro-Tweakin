"""v0.3.0 Smuggler's Run: cargo checks, the spaceship, raids, duels, snacks, bounties, achievements,
meteor strikes and Tweaker Tim. Panel buttons come through Game.handle (owner check + lock),
server-wide event buttons (meteor, Tim) come through EventButton."""
import asyncio
import collections
import io
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from game import data, engine, images, space
from game.data import LAYERS, ORE_BY_KEY, SNACKS, SNACK_BY_KEY, SHIP_UPGRADES, ACHIEVEMENTS
from game.db import NotEnough
from game.engine import fmt_time
from cogs.game import B, ore_line, color_of

log = logging.getLogger("astro-tweakin.space")
GREEN, RED, BLUE = discord.Color.green(), discord.Color.red(), discord.Color.blue()


def gif_file(data_bytes, name):
    return discord.File(io.BytesIO(data_bytes), filename=name)


def ore_summary(ores, limit=8):
    counts = collections.Counter(o.key for o in ores)
    top = sorted(counts.items(), key=lambda kv: -ORE_BY_KEY[kv[0]].chance)[:limit]
    return "\n".join(ore_line(ORE_BY_KEY[k], n) for k, n in top)


class EventButton(discord.ui.DynamicItem[discord.ui.Button], template=r"cg:(?P<action>[a-z0-9_]+):(?P<arg>\d+)"):
    """A button anyone can press (meteors, Tweaker Tim). Survives restarts like the panel buttons."""

    def __init__(self, action, arg, label=None, style=discord.ButtonStyle.secondary, emoji=None, disabled=False):
        super().__init__(discord.ui.Button(label=label, style=style, emoji=emoji, disabled=disabled,
                                           custom_id=f"cg:{action}:{arg}"))
        self.action = action
        self.arg = arg

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], int(match["arg"]), item.label, item.style, item.emoji)

    async def callback(self, interaction):
        await interaction.client.get_cog("Space").handle_event(interaction, self.action, self.arg)


class Space(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.checks = {}          # uid -> space.Check in progress
        self.duels = {}           # duel id -> (challenger, target, wager, expires)
        self.last_duel = {}
        self.live = []            # [kind, event id, expires, [messages]] for meteor/Tim posts to close later
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()

    @property
    def game(self):
        return self.bot.get_cog("Game")

    # ================================================================ routing from Game.handle

    async def dispatch(self, interaction, action, uid):
        """Handles this cog's panel buttons. Returns False if the action isn't ours."""
        if action.startswith("cc_"):
            await self.check_action(interaction, action[3:], uid)
        elif action == "ship":
            await self.show(interaction, self.ship_panel(uid))
        elif action.startswith("shipup_"):
            ok, msg = space.upgrade_ship(self.db, uid, action[7:])
            await self.show(interaction, self.ship_panel(uid, ("✅ " if ok else "❌ ") + msg))
        elif action == "shippaint":
            ok, msg = space.repaint(self.db, uid)
            await self.show(interaction, self.ship_panel(uid, ("✅ " if ok else "❌ ") + msg))
        elif action == "snacks":
            await self.show(interaction, self.snack_panel(uid))
        elif action.startswith("snackmk_"):
            ok, msg = space.make_snack(self.db, uid, action[8:])
            await self.show(interaction, self.snack_panel(uid, ("✅ " if ok else "❌ ") + msg))
        elif action.startswith("snackeat_"):
            ok, msg = space.eat_snack(self.db, uid, action[9:])
            await self.show(interaction, self.snack_panel(uid, msg if ok else "❌ " + msg))
        elif action == "bounties":
            await self.show(interaction, self.bounty_panel(uid))
        elif action.startswith("bounty_"):
            ok, msg = space.claim_bounty(self.db, uid, int(action[7:]))
            await self.show(interaction, self.bounty_panel(uid, msg if ok else "❌ " + msg))
        elif action == "trophies":
            await self.show(interaction, self.trophy_panel(uid, buttons=True))
        elif action.startswith("duelyes_"):
            await self.duel_accept(interaction, int(action[8:]), uid)
        elif action.startswith("duelno_"):
            self.duels.pop(int(action[7:]), None)
            await interaction.response.edit_message(content=f"🏳️ <@{uid}> chickened out of the duel.", embed=None, view=None)
        else:
            return False
        return True

    async def show(self, interaction, panel):
        e, files, v = panel
        await interaction.response.edit_message(embed=e, attachments=files, view=v)

    def busy(self, uid):
        c = self.checks.get(uid)
        return c is not None and not c.done

    async def after_action(self, interaction, uid):
        """Called after every button press and command: announces newly earned achievements."""
        try:
            new = space.check_achievements(self.db, uid)
        except Exception:
            log.exception("Achievement check failed for %s", uid)
            return
        if not new or not interaction.response.is_done():
            return
        name = interaction.user.display_name if interaction.user.id == uid else (self.db.user(uid)["name"] or "Someone")
        lines = "\n".join(f"{a.emoji} **{a.name}**: {a.desc}" for a in new)
        e = discord.Embed(title=f"🏆 {name} unlocked {'an achievement' if len(new) == 1 else f'{len(new)} achievements'}!",
                          description=lines + "\n\nWear it as a title with `/title`.", color=discord.Color.gold())
        try:
            await interaction.followup.send(embed=e)
        except discord.HTTPException:
            pass

    # ================================================================ panels

    def ship_panel(self, uid, note=None):
        user = self.db.user(uid)
        paint_name = data.PAINTS[user["ship_paint"] % len(data.PAINTS)][0]
        e = discord.Embed(title=f"🚀 {user['name']}'s Ship", color=discord.Color.from_rgb(*data.PAINTS[user["ship_paint"] % len(data.PAINTS)][1]))
        hot = space.hot_items(self.db, uid)
        p = space.check_chance(user, hot)
        if hot:
            risk = f"🚨 Carrying **{len(hot)}** kind(s) of hot cargo{' including CONTRABAND' if space.carrying_contraband(hot) else ''}. " \
                   f"Cargo check odds: about **1 in {int(1 / p):,}** swings."
        else:
            risk = "😇 Nothing hot aboard. The Space Authority has no reason to stop you."
        if self.db.buffs(uid).get("nocops"):
            risk += "\n🧴 Cop Repellent is on: no checks right now."
        e.description = (note + "\n\n" if note else "") + risk
        for up in SHIP_UPGRADES:
            lvl = user["ship_" + up.key]
            if lvl >= up.max_level:
                cost = "**MAXED**"
            else:
                coins, mats = space.upgrade_cost(user, up)
                cost = f"Next: {coins:,} coins + " + ", ".join(f"{q}x {engine.item_name(i)} (have {self.db.qty(uid, i)})" for i, q in mats.items())
            e.add_field(name=f"{up.emoji} {up.name}  ·  level {lvl}/{up.max_level}", value=f"*{up.blurb}*\n{cost}", inline=False)
        e.add_field(name="🏴‍☠️ Raid defense", value=f"Raiders have a **{int(space.raid_chance({'ship_engine': 0}, user) * 100)}%** chance "
                    "to find something on your ship (before their engine bonus). You never lose anything.", inline=False)
        e.set_footer(text=f"Paint: {paint_name} · repaint costs {space.paint_cost(user):,} coins · You have {user['coins']:,} coins")
        e.set_image(url="attachment://ship.png")
        files = [gif_file(images.ship_image(user["name"] or "Your", user), "ship.png")]
        v = discord.ui.View(timeout=None)
        for up in SHIP_UPGRADES:
            v.add_item(B("shipup_" + up.key, uid, f"Upgrade {up.name}", up.emoji, discord.ButtonStyle.success,
                         disabled=user["ship_" + up.key] >= up.max_level))
        v.add_item(B("shippaint", uid, "Repaint", "🎨", row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=1))
        return e, files, v

    def snack_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🍬 Space Snack Bar", color=discord.Color.from_rgb(255, 150, 60),
                          description=(note + "\n\n" if note else "") +
                          "Cook snacks from coins and the base ore of the layer you're on, then eat them for boosts.")
        for s in SNACKS:
            coins, mats = space.snack_cost(user, s)
            need = ", ".join(f"{q}x {engine.item_name(i)} (have {self.db.qty(uid, i)})" for i, q in mats.items())
            e.add_field(name=f"{s.emoji} {s.name}  ·  you have {self.db.qty(uid, 'snack:' + s.key)}",
                        value=f"*{s.blurb}*\n{coins:,} coins + {need}", inline=False)
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        for s in SNACKS:
            v.add_item(B("snackmk_" + s.key, uid, f"Make {s.name}", s.emoji, discord.ButtonStyle.success, row=0))
        for s in SNACKS:
            v.add_item(B("snackeat_" + s.key, uid, f"Eat {s.name}", s.emoji, discord.ButtonStyle.primary,
                         disabled=self.db.qty(uid, "snack:" + s.key) <= 0, row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=2))
        return e, [], v

    def bounty_panel(self, uid, note=None):
        rows = space.bounties(self.db, uid)
        e = discord.Embed(title="📜 Bounty Board", color=discord.Color.from_rgb(200, 150, 80),
                          description=(note + "\n\n" if note else "") + "Three jobs a day. Finish all three for a bonus snack.")
        v = discord.ui.View(timeout=None)
        for r in rows:
            prog = space.bounty_progress(self.db, r)
            done = prog >= r["need"]
            mark = "✅" if r["claimed"] else "🟢" if done else "⬜"
            e.add_field(name=f"{mark} {space.bounty_text(r)}",
                        value=f"{engine.bar(prog / r['need'])} {prog:,}/{r['need']:,}  ·  💰 {r['reward']:,} coins"
                              + ("  ·  *claimed*" if r["claimed"] else ""), inline=False)
            v.add_item(B(f"bounty_{r['idx']}", uid, f"Claim #{r['idx'] + 1}", "💰", discord.ButtonStyle.success,
                         disabled=r["claimed"] or not done))
        e.set_footer(text=f"New jobs in {fmt_time(space.seconds_to_reset())} (midnight UTC)")
        v.add_item(B("back", uid, "Back", "↩️"))
        return e, [], v

    def trophy_panel(self, uid, buttons=False):
        user = self.db.user(uid)
        have = space.unlocked(self.db, uid)
        lines = [f"{a.emoji if a.key in have else '🔒'} **{a.name}**: {a.desc}" for a in ACHIEVEMENTS]
        title = space.title_of(user)
        e = discord.Embed(title=f"🏆 {user['name']}'s Achievements  ({len(have)}/{len(ACHIEVEMENTS)})",
                          description="\n".join(lines), color=discord.Color.gold())
        e.set_footer(text=(f"Wearing: {title} · " if title else "") + "Every achievement is a title. Wear one with /title")
        v = None
        if buttons:
            v = discord.ui.View(timeout=None)
            v.add_item(B("back", uid, "Back", "↩️"))
        return e, [], v

    # ================================================================ cargo checks

    async def maybe_check(self, interaction, uid, travel=False):
        if self.busy(uid):
            return
        if space.should_check(self.db, uid, travel=travel):
            await self.start_check(interaction, uid)

    async def start_check(self, interaction, uid):
        name = interaction.user.display_name if interaction.user.id == uid else (self.db.user(uid)["name"] or "Pilot")
        c = space.new_check(self.db, uid, name)
        c.guild = interaction.guild
        self.checks[uid] = c
        gif = await asyncio.to_thread(images.police_gif, "SPACE AUTHORITY CARGO CHECK!", f"PULL OVER, {name.upper()}")
        e, v = self.check_view(c)
        c.message = await interaction.followup.send(content=f"<@{uid}>", embed=e, view=v,
                                                    file=gif_file(gif, "cops.gif"), wait=True)
        asyncio.create_task(self.check_timer(c))

    def check_view(self, c):
        user = self.db.user(c.uid)
        e = discord.Embed(color=RED)
        e.set_image(url="attachment://cops.gif")
        v = discord.ui.View(timeout=None)
        due = f"<t:{int(c.deadline)}:R>"
        if c.step == "choose":
            e.title = "🚔 SPACE AUTHORITY CARGO CHECK"
            e.description = (f"**{c.officer}** flags your ship down. "
                             + ("He can *smell* the contraband from here. " if c.contraband else "Something on your ship looks expensive. ")
                             + f"Mining is paused until this is over.\n\nPick something {due}, or you're busted.")
            v.add_item(B("cc_stash", c.uid, "Hide it", "🫣", discord.ButtonStyle.primary))
            v.add_item(B("cc_talk", c.uid, "Smooth talk", "🗣️", discord.ButtonStyle.primary))
            v.add_item(B("cc_sniff", c.uid, "Distract the K-9", "🐌", discord.ButtonStyle.primary))
            cost = space.bribe_cost(user)
            v.add_item(B("cc_bribe", c.uid, f"Bribe ({cost:,} coins, {int(data.BRIBE_SUCCESS * 100)}%)", "💸",
                         discord.ButtonStyle.success, disabled=user["coins"] < cost, row=1))
            v.add_item(B("cc_dump", c.uid, "Jettison your cheapest hot ore", "🗑️", discord.ButtonStyle.danger, row=1))
        elif c.step == "stash":
            n = len(c.searched)
            e.title = "🫣 Quick, hide it!"
            e.description = (f"{c.officer} is going to search **{n}** of these 3 spots. Pick where your stash goes {due}."
                             + (f"\n🗄️ Your Hidden Compartments (level {user['ship_stash']}) give a "
                                f"{int(data.STASH_MISS * user['ship_stash'] * 100)}% chance he misses it anyway." if user["ship_stash"] else ""))
            for i, spot in enumerate(c.spots):
                v.add_item(B(f"cc_spot{i}", c.uid, spot, "📦"))
        elif c.step == "talk":
            e.title = "🗣️ Talk your way out"
            e.description = f"{c.officer} {c.mood[1]}. What do you say? {due}"
            for i, line in enumerate(c.excuses):
                v.add_item(B(f"cc_say{i}", c.uid, line[:80], "💬", row=i))
        elif c.step == "sniff":
            e.title = "🐌 Distract Sniffles the K-9 Slug"
            e.description = (f"Sniffles is sniffing straight toward your stash. He goes crazy for **{c.treat[1]}**. "
                             f"Toss him the right snack {due}!")
            for i, emoji in enumerate(c.treats):
                v.add_item(B(f"cc_snack{i}", c.uid, None, emoji))
        return e, v

    async def check_timer(self, c):
        while not c.done:
            await asyncio.sleep(max(0.5, c.deadline - time.time()))
            if not c.done and time.time() >= c.deadline:
                await self.finish_check(c, None, "bust", f"{c.officer} got tired of waiting.")
                return

    async def check_action(self, interaction, what, uid):
        c = self.checks.get(uid)
        if c is None or c.done:
            await interaction.response.edit_message(view=None)
            return
        user = self.db.user(uid)
        if what in ("stash", "talk", "sniff"):
            c.step = what
            c.deadline = time.time() + data.CHECK_STEP_SECONDS
            e, v = self.check_view(c)
            await interaction.response.edit_message(embed=e, view=v)
        elif what == "bribe":
            try:
                cost, worked = space.check_bribe(self.db, uid)
            except NotEnough:
                await interaction.response.send_message("You can't afford the bribe anymore.", ephemeral=True)
                return
            if worked:
                await self.finish_check(c, interaction, "clean", f"{c.officer} pockets your **{cost:,}** coins and waves you on. \"Drive safe.\"")
            else:
                await self.finish_check(c, interaction, "bust", f"{c.officer} takes your **{cost:,}** coins... and busts you anyway. For bribery.")
        elif what == "dump":
            c.done = True
            self.checks.pop(uid, None)
            lost = space.check_jettison(self.db, uid)
            text = (f"🗑️ You yeet your **{lost.name}** out the airlock. {c.officer} finds nothing and leaves disappointed."
                    if lost else f"🗑️ You panic and throw out... nothing. {c.officer} shrugs and leaves.")
            e = discord.Embed(title="🗑️ Evidence jettisoned", description=text, color=discord.Color.dark_grey())
            await interaction.response.edit_message(embed=e, attachments=[], view=None)
        elif what.startswith("spot") and c.step == "stash":
            spot = int(what[4:])
            if space.stash_outcome(c, spot, user["ship_stash"]):
                lucky = spot in c.searched
                text = (f"{c.officer} looked right at **{c.spots[spot].lower()}** and somehow missed it. Thank your Hidden Compartments."
                        if lucky else f"{c.officer} tears the ship apart but never checks **{c.spots[spot].lower()}**.")
                await self.finish_check(c, interaction, "clean", text)
            else:
                await self.finish_check(c, interaction, "bust", f"{c.officer} goes straight for **{c.spots[spot].lower()}**. Found it.")
        elif what.startswith("say") and c.step == "talk":
            ok, right = space.talk_outcome(c, int(what[3:]))
            if ok:
                text = (f"{c.officer} was **{c.mood[0]}**, and you nailed it. He lets you go." if right
                        else f"That made no sense, but {c.officer} is too confused to keep going. You're free.")
                await self.finish_check(c, interaction, "clean", text)
            else:
                text = (f"{c.officer} was **{c.mood[0]}**. Wrong move." if not right
                        else f"Right idea, but {c.officer} wasn't buying it today.")
                await self.finish_check(c, interaction, "bust", text)
        elif what.startswith("snack") and c.step == "sniff":
            if space.sniff_outcome(c, int(what[5:])):
                await self.finish_check(c, interaction, "clean", f"Sniffles runs off with the {c.treat[0]}. {c.officer} has to chase him. You fly away.")
            else:
                await self.finish_check(c, interaction, "bust", f"Sniffles ignores it and sits down right on top of your stash. Good boy.")
        else:
            await interaction.response.defer()

    async def finish_check(self, c, interaction, outcome, text):
        if c.done:
            return
        c.done = True
        self.checks.pop(c.uid, None)
        files = []
        if outcome == "clean":
            space.check_clean(self.db, c.uid)
            e = discord.Embed(title="😎 Clean getaway!", description=text, color=GREEN)
        else:
            fine, seized = space.check_bust(self.db, c.uid)
            loss = []
            if fine:
                loss.append(f"fined **{fine:,}** coins")
            if seized:
                loss.append(f"lost your **{seized.name}**")
            e = discord.Embed(title="🚔 BUSTED!", color=RED,
                              description=text + ("\n\nYou " + " and ".join(loss) + "." if loss else ""))
            gif = await asyncio.to_thread(images.police_gif, "BUSTED!", f"{c.name.upper()} IS GOING TO SPACE JAIL")
            files = [gif_file(gif, "busted.gif")]
            e.set_image(url="attachment://busted.gif")
            if seized:
                await self.shout(c.guild,
                                 f"🚔 **{c.name}** got busted by the Space Authority and lost their **{seized.name}**!",
                                 skip=c.message.channel.id if c.message else None)
        try:
            if interaction is not None:
                await interaction.response.edit_message(embed=e, attachments=files, view=None)
            elif c.message is not None:
                await c.message.edit(embed=e, attachments=files, view=None)
        except discord.HTTPException:
            log.warning("Couldn't update cargo check message for %s", c.uid)

    async def shout(self, guild, text, skip=None):
        if guild is None:
            return
        cid = self.db.announce_channel(guild.id) or self.bot.default_announce
        if cid and cid != skip:
            ch = self.bot.get_channel(cid)
            if ch:
                try:
                    await ch.send(text)
                except discord.HTTPException:
                    pass

    # ================================================================ slash commands

    async def _open(self, interaction, panel):
        e, files, v = panel
        kwargs = {"embed": e, "files": files}
        if v is not None:
            kwargs["view"] = v
        await interaction.response.send_message(**kwargs)
        await self.after_action(interaction, interaction.user.id)

    @app_commands.command(name="ship", description="Your spaceship: upgrades, paint and cargo check odds")
    async def ship_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.ship_panel(interaction.user.id))

    @app_commands.command(name="snacks", description="The Space Snack Bar: cook and eat snacks for boosts")
    async def snacks_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.snack_panel(interaction.user.id))

    @app_commands.command(name="bounties", description="Today's three bounty jobs")
    async def bounties_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.bounty_panel(interaction.user.id))

    @app_commands.command(name="achievements", description="Your achievements (each one is a title you can wear)")
    async def achievements_cmd(self, interaction: discord.Interaction, member: discord.Member = None):
        member = member or interaction.user
        self.db.user(member.id, member.display_name)
        space.check_achievements(self.db, member.id)
        e, _, _ = self.trophy_panel(member.id)
        await interaction.response.send_message(embed=e, ephemeral=member.id == interaction.user.id)

    @app_commands.command(name="title", description="Wear one of your achievement titles")
    async def title_cmd(self, interaction: discord.Interaction, title: str):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        ok, msg = space.set_title(self.db, uid, None if title == "none" else title)
        await interaction.response.send_message(("🏷️ " if ok else "❌ ") + msg, ephemeral=True)

    @title_cmd.autocomplete("title")
    async def title_autocomplete(self, interaction, current: str):
        have = space.unlocked(self.db, interaction.user.id)
        opts = [app_commands.Choice(name=f"{a.name}", value=a.key) for a in ACHIEVEMENTS
                if a.key in have and current.lower() in a.name.lower()]
        return ([app_commands.Choice(name="(no title)", value="none")] + opts)[:25]

    @app_commands.command(name="raid", description="Sneak onto someone's ship and look for stuff they forgot about (they lose nothing)")
    async def raid_cmd(self, interaction: discord.Interaction, member: discord.Member):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        if member.bot:
            await interaction.response.send_message("Bots don't have ships. They live in the cloud.", ephemeral=True)
            return
        if self.busy(uid):
            await interaction.response.send_message("🚔 You're in the middle of a cargo check!", ephemeral=True)
            return
        async with self.game.locks[uid]:
            res = space.raid(self.db, uid, member.id)
        if not res.ok:
            await interaction.response.send_message("❌ " + res.message, ephemeral=True)
            return
        target = self.db.user(member.id)
        name = interaction.user.display_name
        if res.success:
            e = discord.Embed(title=f"🏴‍☠️ {name} raided {member.display_name}'s ship!", color=discord.Color.dark_gold(),
                              description=f"You snuck aboard and dug through the junk. Look what {member.display_name} forgot about:\n"
                                          f"{ore_summary(res.ores)}\n\n*They didn't lose a thing. They'll never even know.*")
            pic = images.ship_image(target["name"] or member.display_name, target, stamp="RAIDED!")
        else:
            e = discord.Embed(title=f"🚨 {name}'s raid failed!", color=RED,
                              description=f"{member.display_name}'s ship alarm went off and you ran for it. "
                                          f"(You had a {int(res.chance * 100)}% chance. Upgrade your 🔥 Turbo Engine.)")
            pic = images.ship_image(target["name"] or member.display_name, target, stamp="ALARM!", stamp_color=(255, 210, 40))
        e.set_image(url="attachment://raid.png")
        e.set_footer(text=f"Next raid in {fmt_time(data.RAID_COOLDOWN)}")
        await interaction.response.send_message(content=member.mention, embed=e, file=gif_file(pic, "raid.png"),
                                                allowed_mentions=discord.AllowedMentions(users=True))
        if res.success and res.best:
            await self.game.celebrate(interaction, res.best)
        await self.after_action(interaction, uid)

    @app_commands.command(name="duel", description="Challenge someone to a ship duel, with an optional coin bet")
    async def duel_cmd(self, interaction: discord.Interaction, member: discord.Member, wager: app_commands.Range[int, 0] = 0):
        uid = interaction.user.id
        me = self.db.user(uid, interaction.user.display_name)
        if member.bot or member.id == uid:
            await interaction.response.send_message("Pick a real opponent.", ephemeral=True)
            return
        wait = self.last_duel.get(uid, 0) + data.DUEL_COOLDOWN - time.time()
        if wait > 0:
            await interaction.response.send_message(f"Your blasters are recharging. Try again in {fmt_time(wait)}.", ephemeral=True)
            return
        them = self.db.user(member.id, member.display_name)
        if wager and (me["coins"] < wager or them["coins"] < wager):
            await interaction.response.send_message("You both need to have the full bet in coins.", ephemeral=True)
            return
        self.last_duel[uid] = time.time()
        did = int(time.time() * 1000) % 1_000_000_000
        self.duels[did] = (uid, member.id, wager, time.time() + data.DUEL_TIMEOUT)
        bet = f" for **{wager:,} coins**" if wager else " (no bet, just bragging rights)"
        e = discord.Embed(title="⚔️ Duel challenge!", color=discord.Color.orange(),
                          description=f"{interaction.user.mention} challenges {member.mention} to a ship duel{bet}.\n"
                                      f"Best of 3 laser rounds. Turbo Engines hit harder.\n"
                                      f"{member.display_name}, accept <t:{int(time.time() + data.DUEL_TIMEOUT)}:R>.")
        v = discord.ui.View(timeout=None)
        v.add_item(B(f"duelyes_{did}", member.id, "Accept", "⚔️", discord.ButtonStyle.danger))
        v.add_item(B(f"duelno_{did}", member.id, "Chicken out", "🐔"))
        await interaction.response.send_message(content=member.mention, embed=e, view=v,
                                                allowed_mentions=discord.AllowedMentions(users=True))

    async def duel_accept(self, interaction, did, uid):
        duel = self.duels.pop(did, None)
        if not duel or time.time() > duel[3]:
            await interaction.response.edit_message(content="⌛ That duel challenge expired.", embed=None, view=None)
            return
        a_id, b_id, wager, _ = duel
        a, b = self.db.user(a_id), self.db.user(b_id)
        winner, rounds = space.fight(a, b)
        w_id, l_id = (a_id, b_id) if winner == 0 else (b_id, a_id)
        try:
            space.settle_duel(self.db, w_id, l_id, wager)
        except NotEnough:
            await interaction.response.edit_message(content="💸 Someone spent the bet money. Duel's off.", embed=None, view=None)
            return
        await interaction.response.defer()
        pa = data.PAINTS[a["ship_paint"] % len(data.PAINTS)][1]
        pb = data.PAINTS[b["ship_paint"] % len(data.PAINTS)][1]
        gif = await asyncio.to_thread(images.duel_gif, a["name"] or "Pilot 1", b["name"] or "Pilot 2", pa, pb, rounds, winner)
        score = "  ·  ".join(f"R{i + 1}: {ra} vs {rb}" for i, (ra, rb) in enumerate(rounds))
        e = discord.Embed(title=f"⚔️ {self.db.user(w_id)['name']} wins the duel!", color=discord.Color.orange(),
                          description=f"<@{a_id}> vs <@{b_id}>\n{score}"
                                      + (f"\n💰 <@{w_id}> takes **{wager:,}** coins from <@{l_id}>." if wager else ""))
        e.set_image(url="attachment://duel.gif")
        await interaction.edit_original_response(content=None, embed=e, attachments=[gif_file(gif, "duel.gif")], view=None)

    # ================================================================ meteors and Tweaker Tim

    def event_channels(self):
        out = []
        for guild in self.bot.guilds:
            cid = self.db.announce_channel(guild.id)
            if not cid and self.bot.default_announce:
                ch = self.bot.get_channel(self.bot.default_announce)
                cid = self.bot.default_announce if ch and ch.guild.id == guild.id else None
            ch = self.bot.get_channel(cid) if cid else None
            if ch:
                out.append(ch)
        return out

    @tasks.loop(seconds=60)
    async def scheduler(self):
        try:
            now = time.time()
            nm = float(self.db.meta("next_meteor_at", 0) or 0)
            if not nm:
                self.db.set_meta("next_meteor_at", space.next_meteor_time(now))
            elif now >= nm:
                self.db.set_meta("next_meteor_at", space.next_meteor_time(now, today_ok=False))
                await self.drop_meteor()
            elif random.random() < data.METEOR_BONUS_PER_MINUTE:
                await self.drop_meteor()
            nt = float(self.db.meta("next_tim_at", 0) or 0)
            if not nt:
                self.db.set_meta("next_tim_at", now + random.uniform(1800, 3 * 3600))
            elif now >= nt:
                self.db.set_meta("next_tim_at", now + random.uniform(*data.TIM_GAP_HOURS) * 3600)
                await self.tim_arrives()
            await self.close_expired()
        except Exception:
            log.exception("Scheduler tick failed")

    @scheduler.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    async def close_expired(self):
        now = time.time()
        for entry in [x for x in self.live if now > x[2]]:
            self.live.remove(entry)
            kind, event, _, msgs = entry
            for m in msgs:
                try:
                    if kind == "meteor":
                        e = m.embeds[0] if m.embeds else discord.Embed()
                        e.title = "☄️ The meteor cooled off"
                        await m.edit(embed=e, view=None)
                    else:
                        e = discord.Embed(title="👽 Tweaker Tim has left", color=discord.Color.dark_grey(),
                                          description="He'll be back. He always comes back.")
                        e.set_image(url="attachment://tim.jpg")
                        await m.edit(embed=e, attachments=[gif_file(images.tim_card(leaving=True), "tim.jpg")], view=None)
                except discord.HTTPException:
                    pass

    async def drop_meteor(self):
        event = int(time.time())
        expires = time.time() + data.METEOR_MINUTES * 60
        self.db.set_meta(f"meteor:{event}", expires)
        luck, minutes = data.METEOR_SHOWER
        ev = self.db.event()
        if not ev or ev["luck"] < luck:
            self.db.set_event(luck, minutes * 60, "Meteor Shower")
        channels = self.event_channels()
        if not channels:
            log.warning("A meteor hit but no announce channel is set (/admin announce)")
            return 0
        gif = await asyncio.to_thread(images.meteor_gif)
        sent = []
        for ch in channels:
            e = discord.Embed(title="☄️ METEOR STRIKE!", color=discord.Color.from_rgb(255, 140, 40), description=(
                f"A meteor just slammed into the server! The first **{data.METEOR_CLAIMS}** miners get "
                f"**{data.METEOR_ROLLS} rolls at x{data.METEOR_LUCK:g} luck** plus coins.\n"
                f"☄️ Everyone gets **x{luck:g} luck** for {minutes} minutes from the meteor shower.\n"
                f"It cools off <t:{int(expires)}:R>."))
            e.set_image(url="attachment://meteor.gif")
            v = discord.ui.View(timeout=None)
            v.add_item(EventButton("meteor", event, "Mine it!", discord.ButtonStyle.success, "⛏️"))
            try:
                sent.append(await ch.send(embed=e, view=v, file=gif_file(gif, "meteor.gif")))
            except discord.HTTPException:
                log.warning("Couldn't post meteor in %s", ch)
        self.live.append(["meteor", event, expires, sent])
        return len(sent)

    async def tim_arrives(self):
        visit_id = int(time.time())
        expires = time.time() + data.TIM_MINUTES * 60
        visit = space.new_tim_visit()
        space.save_visit(self.db, visit_id, visit, expires)
        channels = self.event_channels()
        if not channels:
            log.warning("Tweaker Tim came by but no announce channel is set (/admin announce)")
            return 0
        card = images.tim_card()
        sent = []
        for ch in channels:
            e = discord.Embed(title="👽 Tweaker Tim is in town!", color=discord.Color.from_rgb(120, 255, 140), description=(
                "\"Psst. Hey. You. Wanna buy something?\"\n"
                f"One of each deal per customer. Prices grow with how deep you've gone. He leaves <t:{int(expires)}:R>."))
            for deal in visit["deals"]:
                emoji, name, blurb = space.TIM_DEALS[deal]
                if deal == "snack":
                    s = SNACKS[visit["snack"]]
                    blurb = f"{s.emoji} {s.name}: {blurb}"
                e.add_field(name=f"{emoji} {name}", value=blurb, inline=False)
            e.set_image(url="attachment://tim.jpg")
            v = discord.ui.View(timeout=None)
            for i, deal in enumerate(visit["deals"]):
                emoji, name, _ = space.TIM_DEALS[deal]
                v.add_item(EventButton(f"tim{i}", visit_id, name, discord.ButtonStyle.success, emoji))
            try:
                sent.append(await ch.send(embed=e, view=v, file=gif_file(card, "tim.jpg")))
            except discord.HTTPException:
                log.warning("Couldn't post Tim in %s", ch)
        self.live.append(["tim", visit_id, expires, sent])
        return len(sent)

    async def handle_event(self, interaction, action, arg):
        uid = interaction.user.id
        self.db.user(uid, interaction.user.display_name)
        async with self.game.locks[uid]:
            try:
                if action == "meteor":
                    ok, res = space.mine_meteor(self.db, uid, arg)
                    if not ok:
                        await interaction.response.send_message(res, ephemeral=True)
                        return
                    ores, best, coins, place = res
                    await interaction.response.send_message(
                        f"⛏️ You were miner **#{place}** on the meteor! +**{coins:,}** coins and:\n{ore_summary(ores)}", ephemeral=True)
                    try:
                        e = interaction.message.embeds[0]
                        names = [self.db.user(u)["name"] or "?" for u in self.db.claims("meteor", arg)]
                        e.clear_fields()
                        e.add_field(name=f"Miners ({len(names)}/{data.METEOR_CLAIMS})", value=", ".join(names))
                        await interaction.message.edit(embed=e)
                    except (discord.HTTPException, IndexError):
                        pass
                    await self.game.celebrate(interaction, best)
                elif action.startswith("tim"):
                    ok, msg, ore = space.tim_buy(self.db, uid, arg, int(action[3:]))
                    await interaction.response.send_message(("" if ok else "❌ ") + msg, ephemeral=True)
                    if ore:
                        await self.game.celebrate(interaction, ore)
                else:
                    await interaction.response.defer()
                    return
            except NotEnough:
                await self.game.oops(interaction, "You don't have enough for that anymore. Nothing was taken.")
                return
            except Exception:
                log.exception("Event button %s failed for %s", action, uid)
                await self.game.oops(interaction, "Something went wrong. Your progress is safe, nothing was changed.")
                return
        await self.after_action(interaction, uid)


async def setup(bot):
    bot.add_dynamic_items(EventButton)
    await bot.add_cog(Space(bot))
