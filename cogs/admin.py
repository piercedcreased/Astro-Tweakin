"""Owner/admin commands: announce channel, server luck events, giving coins, testing reveals."""
import io
import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

import asyncio
import time

from game import backup, images, updates
from game.data import MUT_BY_KEY, MUTATIONS, ORES, ORE_BY_KEY
from cogs.game import shout_line


def is_admin(interaction: discord.Interaction):
    bot = interaction.client
    if interaction.user.id in bot.admin_ids:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


async def is_owner(interaction: discord.Interaction):
    """The bot's owners: ADMIN_USER_IDS in .env, or whoever owns the bot in the Discord developer portal.
    The save is shared by every server, so anything that hands out coins, luck or loot is owners-only;
    a server's own admins can still set up channels."""
    bot = interaction.client
    return interaction.user.id in bot.admin_ids or await bot.is_owner(interaction.user)


admin_only = app_commands.check(is_admin)
owner_only = app_commands.check(is_owner)


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    group = app_commands.Group(name="admin", description="Astro Tweakin admin tools")

    @group.command(name="announce", description="Set the channel for rare finds, meteors and Tweaker Tim")
    @admin_only
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.db.set_announce_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(f"Rare finds will be announced in {channel.mention}.", ephemeral=True)

    @group.command(name="event", description="Start a luck event for every player")
    @owner_only
    async def event(self, interaction: discord.Interaction, luck: app_commands.Range[float, 1, 100],
                    minutes: app_commands.Range[int, 1, 1440], name: str = "Meteor Shower"):
        self.db.set_event(luck, minutes * 60, name)
        await interaction.response.send_message(
            f"☄️ **{name}!** Everyone has **x{luck:g} luck** for the next **{minutes} minutes**!")

    @group.command(name="give", description="Give a player coins (negative takes them away)")
    @owner_only
    async def give(self, interaction: discord.Interaction, member: discord.Member,
                   coins: app_commands.Range[int, -10 ** 15, 10 ** 15]):
        self.db.user(member.id, member.display_name)
        self.db.add_coins(member.id, coins)
        await interaction.response.send_message(f"Gave {member.mention} {coins:,} coins.", ephemeral=True)

    @group.command(name="backup", description="Save a backup of all player data right now")
    @owner_only
    async def backup_now(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        path = await asyncio.to_thread(backup.make_backup, self.db, "manual")
        n = len(backup.list_backups())
        await interaction.followup.send(f"Backup saved: `{path}` ({n} backups kept).", ephemeral=True)

    @group.command(name="restart", description="Restart the bot to load new code and post new update notes")
    @owner_only
    async def restart(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Restarting. Back in about 10 seconds.", ephemeral=True)
        await self.bot.close()   # start.bat brings it straight back up

    @group.command(name="meteor", description="Drop a meteor strike in this channel right now")
    @owner_only
    async def meteor(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n = await self.bot.get_cog("Space").drop_meteor(channels=[interaction.channel])
        await interaction.followup.send("☄️ Meteor dropped here." if n else
                                        "I couldn't post here (needs Send Messages, Embed Links and Attach Files).", ephemeral=True)

    @group.command(name="tim", description="Make Tweaker Tim show up in the announce channel right now")
    @owner_only
    async def tim(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n = await self.bot.get_cog("Space").tim_arrives()
        await interaction.followup.send(f"👽 Tim showed up in {n} channel(s)." if n else
                                        "No announce channel set. Use `/admin announce` first.", ephemeral=True)

    @group.command(name="cops", description="Send the Space Authority after yourself to test a cargo check")
    @owner_only
    async def cops(self, interaction: discord.Interaction):
        sp = self.bot.get_cog("Space")
        if sp.busy(interaction.user.id):
            await interaction.response.send_message("You're already being searched.", ephemeral=True)
            return
        await interaction.response.send_message("🚔 Calling the cops on you...", ephemeral=True)
        await sp.start_check(interaction, interaction.user.id)

    @group.command(name="preview", description="Show what a rare-find animation looks like (doesn't give the ore)")
    @admin_only
    @app_commands.choices(mutation=[app_commands.Choice(name=f"{m.emoji} {m.name}", value=m.key) for m in reversed(MUTATIONS)])
    async def preview(self, interaction: discord.Interaction, ore: str, mutation: app_commands.Choice[str] = None):
        o = ORE_BY_KEY.get(ore)
        if not o:
            await interaction.response.send_message("Unknown ore.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        m = MUT_BY_KEY[mutation.value] if mutation else None
        gif = await asyncio.to_thread(images.reveal_gif, o, interaction.user.display_name, None, m)
        text = f"{m.emoji} {m.name} {o.name} (1 in {m.chance:,} mutation, x{m.mult} value)" if m else shout_line(interaction.user.display_name, o)
        await interaction.followup.send("Preview: " + text,
                                        file=discord.File(io.BytesIO(gif), filename=f"{o.key}.gif"), ephemeral=True)

    @preview.autocomplete("ore")
    async def ore_autocomplete(self, interaction, current: str):
        return [app_commands.Choice(name=o.name[:100], value=o.key) for o in ORES
                if current.lower() in o.name.lower()][:25]

    @group.command(name="botchannel", description="Choose which channels the game works in")
    @app_commands.describe(action="add or remove a channel, or clear to allow every channel")
    @app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove"),
                                  app_commands.Choice(name="clear (allow everywhere)", value="clear")])
    @admin_only
    async def botchannel(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                         channel: discord.TextChannel = None):
        gid = interaction.guild.id
        chans = self.db.bot_channels(gid)
        if action.value == "clear":
            chans = []
        elif channel is None:
            await interaction.response.send_message("Pick a channel too.", ephemeral=True)
            return
        elif action.value == "add":
            chans.append(channel.id)
        else:
            chans = [c for c in chans if c != channel.id]
        self.db.set_bot_channels(gid, chans)
        where = ", ".join(f"<#{c}>" for c in chans) or "every channel"
        await interaction.response.send_message(f"✅ Game commands now work in: {where}. (/admin commands work anywhere.)",
                                                ephemeral=True)

    async def _set_update_channel(self, interaction, channel):
        """Saves the update channel and posts the latest notes there if this server hasn't had them.
        Returns an error message, or None when it worked."""
        gid = interaction.guild.id
        notes = updates.all_notes()
        if notes and os.path.basename(notes[-1]) not in (self.bot.updates_posted(gid) or set()):
            try:
                await updates.send(channel, notes[-1])
            except discord.HTTPException:
                return (f"I can't post in {channel.mention}. Give the bot **Send Messages**, **Embed Links** and "
                        "**Attach Files** there and try again.")
        self.db.set_meta(f"updatechannel:{gid}", channel.id)
        self.bot.mark_updates_posted(gid, [os.path.basename(p) for p in notes])
        return None

    @group.command(name="channel", description="Put everything in one channel: the game, rare finds, meteors, Tim and update notes")
    @admin_only
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        err = await self._set_update_channel(interaction, channel)
        if err:
            await interaction.followup.send(err, ephemeral=True)
            return
        gid = interaction.guild.id
        self.db.set_bot_channels(gid, [channel.id])
        self.db.set_announce_channel(gid, channel.id)
        await interaction.followup.send(
            f"✅ Everything now happens in {channel.mention}: the game, rare finds, meteors, Tweaker Tim and update notes. "
            "Game commands won't work in other channels (/admin commands still work anywhere).", ephemeral=True)

    @group.command(name="updatechannel", description="Choose the channel where update notes get posted")
    @admin_only
    async def updatechannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        err = await self._set_update_channel(interaction, channel)
        await interaction.followup.send(err or f"✅ Update notes will now post in {channel.mention}.", ephemeral=True)

    @group.command(name="settings", description="Show where the bot posts and which channels it works in")
    @admin_only
    async def settings(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        chans = self.db.bot_channels(gid)
        ann = self.db.announce_channel(gid) or self.bot.default_announce
        upd = self.db.meta(f"updatechannel:{gid}")
        own_ann = self.db.announce_channel(gid)
        hook = f"<#{upd}>" if upd else (f"<#{own_ann}> (the announce channel; /admin updatechannel to change)" if own_ann
                                       else "Not set (/admin updatechannel)")
        e = discord.Embed(title="⚙️ Astro Tweakin settings", color=discord.Color.blurple())
        e.add_field(name="Game works in", value=", ".join(f"<#{c}>" for c in chans) or "Every channel (/admin botchannel)", inline=False)
        e.add_field(name="Rare finds, meteors, Tweaker Tim", value=f"<#{ann}>" if ann else "Not set (/admin announce)", inline=False)
        e.add_field(name="Update notes", value=hook, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @group.command(name="testerror", description="Cause a fake error to check that error reports reach your DMs")
    @admin_only
    async def testerror(self, interaction: discord.Interaction):
        await interaction.response.send_message("💥 Making a test error. Check your DMs in a few seconds.", ephemeral=True)
        try:
            raise RuntimeError("This is a test error from /admin testerror. Everything is fine.")
        except RuntimeError:
            logging.getLogger("astro-tweakin.admin").exception("Test error requested by %s", interaction.user.display_name)

    # ------------------------------------------------------------ /alcad: logs and usage analytics (owners only)

    @app_commands.command(name="alcad")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(show=[app_commands.Choice(name="usage", value="usage"),
                                app_commands.Choice(name="errors", value="errors")])
    @owner_only
    async def alcad(self, interaction: discord.Interaction, show: app_commands.Choice[str] = None,
                    member: discord.User = None, days: app_commands.Range[int, 1, 90] = 7):
        await interaction.response.defer(ephemeral=True)
        if show and show.value == "errors":
            await interaction.followup.send(**self._errors_report(), ephemeral=True)
        else:
            await interaction.followup.send(embed=self._usage_report(member, days), ephemeral=True)

    def _name(self, uid):
        row = self.db.one("SELECT name FROM users WHERE id=?", uid)
        return (row["name"] if row and row["name"] else None) or str(uid)

    def _guild_name(self, gid):
        g = self.bot.get_guild(gid) if gid else None
        return g.name if g else ("DMs" if not gid else str(gid))

    def _usage_report(self, member, days):
        since = time.time() - days * 86400
        e = discord.Embed(title=f"📈 Usage · last {days} day{'s' if days != 1 else ''}", color=discord.Color.blurple())
        if member:
            e.title += f" · {member.display_name}"
            rows = self.db.q("SELECT action, kind, COUNT(*) AS n FROM usage WHERE at>=? AND user_id=? AND kind IN "
                             "('slash','button','chat') GROUP BY action ORDER BY n DESC LIMIT 20", since, member.id)
            total = sum(r["n"] for r in rows)
            e.description = f"**{total:,}** commands and button presses" + ("" if total else " (nothing yet)")
            if rows:
                e.add_field(name="What they use most", inline=False,
                            value="\n".join(f"`{r['n']:>6,}` {r['action']}" for r in rows)[:1024])
            per = self.db.q("SELECT guild_id, COUNT(*) AS n FROM usage WHERE at>=? AND user_id=? GROUP BY guild_id "
                            "ORDER BY n DESC LIMIT 10", since, member.id)
            if per:
                e.add_field(name="Where", inline=False,
                            value="\n".join(f"`{r['n']:>6,}` {self._guild_name(r['guild_id'])}" for r in per)[:1024])
            return e
        main = "kind IN ('slash','button','chat')"
        tot = self.db.one(f"SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS u, COUNT(DISTINCT guild_id) AS g "
                          f"FROM usage WHERE at>=? AND {main}", since)
        slow = self.db.one("SELECT COUNT(*) AS n FROM usage WHERE at>=? AND kind='slow'", since)["n"]
        expired = self.db.one("SELECT COUNT(*) AS n FROM usage WHERE at>=? AND kind='expired'", since)["n"]
        e.description = (f"**{tot['n']:,}** commands and button presses from **{tot['u']:,}** players in "
                         f"**{tot['g']:,}** server(s).\n🐢 Slow presses caught before they failed: **{slow:,}**  ·  "
                         f"❌ presses that still timed out: **{expired:,}**")
        top = self.db.q(f"SELECT action, COUNT(*) AS n FROM usage WHERE at>=? AND kind IN ('slash','chat') "
                        f"GROUP BY action ORDER BY n DESC LIMIT 12", since)
        if top:
            e.add_field(name="Top commands", value="\n".join(f"`{r['n']:>6,}` {r['action']}" for r in top)[:1024])
        btn = self.db.q("SELECT action, COUNT(*) AS n FROM usage WHERE at>=? AND kind='button' "
                        "GROUP BY action ORDER BY n DESC LIMIT 12", since)
        if btn:
            e.add_field(name="Top buttons", value="\n".join(f"`{r['n']:>6,}` {r['action']}" for r in btn)[:1024])
        users = self.db.q(f"SELECT user_id, COUNT(*) AS n FROM usage WHERE at>=? AND {main} "
                          f"GROUP BY user_id ORDER BY n DESC LIMIT 10", since)
        lines = []
        for r in users:
            fav = self.db.q(f"SELECT action, COUNT(*) AS n FROM usage WHERE at>=? AND user_id=? AND {main} "
                            f"GROUP BY action ORDER BY n DESC LIMIT 3", since, r["user_id"])
            lines.append(f"`{r['n']:>6,}` **{self._name(r['user_id'])}**: " + ", ".join(f"{f['action']} ({f['n']:,})" for f in fav))
        if lines:
            e.add_field(name="Most active players (and what they use most)", value="\n".join(lines)[:1024], inline=False)
        guilds = self.db.q(f"SELECT guild_id, COUNT(*) AS n, COUNT(DISTINCT user_id) AS u FROM usage WHERE at>=? AND {main} "
                           f"GROUP BY guild_id ORDER BY n DESC LIMIT 10", since)
        if guilds:
            e.add_field(name="Servers", inline=False, value="\n".join(
                f"`{r['n']:>6,}` {self._guild_name(r['guild_id'])} ({r['u']} players)" for r in guilds)[:1024])
        e.set_footer(text="Pick a member to see one person · show: errors for the error log · kept for 90 days")
        return e

    def _errors_report(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "errors.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return {"content": "No error log yet. Nothing has gone wrong. 🎉"}
        lines = text.splitlines()
        heads = [l for l in lines if l[:4].isdigit() and (" ERROR " in l or " CRITICAL " in l)]
        e = discord.Embed(title="🧾 Error log", color=discord.Color.red(),
                          description=f"**{len(heads):,}** errors in the current log file. The newest:")
        tail = "\n".join(heads[-12:]) or "(none)"
        e.add_field(name="Latest", value=f"```{tail[-1000:]}```", inline=False)
        recent = "\n".join(lines[-400:])
        return {"embed": e, "file": discord.File(io.BytesIO(recent.encode("utf-8")), filename="errors_recent.log")}

    async def cog_app_command_error(self, interaction, error):
        if not isinstance(error, app_commands.CheckFailure):
            original = getattr(error, "original", error)
            logging.getLogger("astro-tweakin.admin").error(
                "/admin command failed for %s", interaction.user.display_name,
                exc_info=(type(original), original, original.__traceback__))
        msg = ("You need Manage Server to use that (coins, luck, meteors and Tim are bot-owner only)."
               if isinstance(error, app_commands.CheckFailure) else f"Error: {error}")
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
