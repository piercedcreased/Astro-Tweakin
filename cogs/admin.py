"""Owner/admin commands: announce channel, server luck events, giving coins, testing reveals."""
import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

import asyncio

from game import backup, images, updates
from game.data import ORES, ORE_BY_KEY
from cogs.game import shout_line


def is_admin(interaction: discord.Interaction):
    bot = interaction.client
    if interaction.user.id in bot.admin_ids:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


admin_only = app_commands.check(is_admin)


class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db

    group = app_commands.Group(name="admin", description="Astro Tweakin admin tools")

    @group.command(name="announce", description="Set the channel where rare finds get announced")
    @admin_only
    async def announce(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.db.set_announce_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(f"Rare finds will be announced in {channel.mention}.", ephemeral=True)

    @group.command(name="event", description="Start a server-wide luck event")
    @admin_only
    async def event(self, interaction: discord.Interaction, luck: app_commands.Range[float, 1, 100],
                    minutes: app_commands.Range[int, 1, 1440], name: str = "Meteor Shower"):
        self.db.set_event(luck, minutes * 60, name)
        await interaction.response.send_message(
            f"☄️ **{name}!** Everyone has **x{luck:g} luck** for the next **{minutes} minutes**!")

    @group.command(name="give", description="Give a player coins")
    @admin_only
    async def give(self, interaction: discord.Interaction, member: discord.Member, coins: int):
        self.db.user(member.id, member.display_name)
        self.db.add_coins(member.id, coins)
        await interaction.response.send_message(f"Gave {member.mention} {coins:,} coins.", ephemeral=True)

    @group.command(name="backup", description="Save a backup of all player data right now")
    @admin_only
    async def backup_now(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        path = await asyncio.to_thread(backup.make_backup, self.db, "manual")
        n = len(backup.list_backups())
        await interaction.followup.send(f"Backup saved: `{path}` ({n} backups kept).", ephemeral=True)

    @group.command(name="restart", description="Restart the bot to load new code and post new update notes")
    @admin_only
    async def restart(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔄 Restarting. Back in about 10 seconds.", ephemeral=True)
        await self.bot.close()   # start.bat brings it straight back up

    @group.command(name="meteor", description="Drop a meteor strike in the announce channel right now")
    @admin_only
    async def meteor(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n = await self.bot.get_cog("Space").drop_meteor()
        await interaction.followup.send(f"☄️ Meteor dropped in {n} channel(s)." if n else
                                        "No announce channel set. Use `/admin announce` first.", ephemeral=True)

    @group.command(name="tim", description="Make Tweaker Tim show up in the announce channel right now")
    @admin_only
    async def tim(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        n = await self.bot.get_cog("Space").tim_arrives()
        await interaction.followup.send(f"👽 Tim showed up in {n} channel(s)." if n else
                                        "No announce channel set. Use `/admin announce` first.", ephemeral=True)

    @group.command(name="cops", description="Send the Space Authority after yourself to test a cargo check")
    @admin_only
    async def cops(self, interaction: discord.Interaction):
        sp = self.bot.get_cog("Space")
        if sp.busy(interaction.user.id):
            await interaction.response.send_message("You're already being searched.", ephemeral=True)
            return
        await interaction.response.send_message("🚔 Calling the cops on you...", ephemeral=True)
        await sp.start_check(interaction, interaction.user.id)

    @group.command(name="preview", description="Show what a rare-find animation looks like (doesn't give the ore)")
    @admin_only
    async def preview(self, interaction: discord.Interaction, ore: str):
        o = ORE_BY_KEY.get(ore)
        if not o:
            await interaction.response.send_message("Unknown ore.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        gif = images.reveal_gif(o, interaction.user.display_name)
        await interaction.followup.send("Preview: " + shout_line(interaction.user.display_name, o),
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

    @group.command(name="updatechannel", description="Choose the channel where update notes get posted")
    @admin_only
    async def updatechannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        try:
            hook = await channel.create_webhook(name="Astro Tweakin Updates", reason="Update notes channel set with /admin updatechannel")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I need the **Manage Webhooks** permission in that channel. Give the bot's role that permission and try again.",
                ephemeral=True)
            return
        updates.set_webhook_url(hook.url)
        await interaction.response.send_message(f"✅ Update notes will now post in {channel.mention}. "
                                                "(This replaces the webhook in .env.)", ephemeral=True)

    @group.command(name="settings", description="Show where the bot posts and which channels it works in")
    @admin_only
    async def settings(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        chans = self.db.bot_channels(gid)
        ann = self.db.announce_channel(gid) or self.bot.default_announce
        hook = "set" if updates.webhook_url() else "not set (use /admin updatechannel)"
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

    async def cog_app_command_error(self, interaction, error):
        if not isinstance(error, app_commands.CheckFailure):
            original = getattr(error, "original", error)
            logging.getLogger("astro-tweakin.admin").error(
                "/admin command failed for %s", interaction.user.display_name,
                exc_info=(type(original), original, original.__traceback__))
        msg = "You need Manage Server to use that." if isinstance(error, app_commands.CheckFailure) else f"Error: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
