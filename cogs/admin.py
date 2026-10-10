"""Owner/admin commands: announce channel, server luck events, giving coins, testing reveals."""
import io

import discord
from discord import app_commands
from discord.ext import commands

import asyncio

from game import backup, images
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

    async def cog_app_command_error(self, interaction, error):
        msg = "You need Manage Server to use that." if isinstance(error, app_commands.CheckFailure) else f"Error: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Admin(bot))
