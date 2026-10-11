"""Astro Tweakin: a space mining incremental Discord bot.

Run with start.bat on Windows, or: python bot.py
"""
import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from discord.ext import tasks

from game import backup, updates
from game.reporter import ErrorReporter
from game.db import DB, CorruptDatabase

ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT, ".env"))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("astro-tweakin")


def _ids(value):
    return {int(x) for x in (value or "").replace(" ", "").split(",") if x}


class AstroTweakin(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        # always next to bot.py, so starting it from another folder can't create an empty new save
        self.db = DB(os.path.join(ROOT, os.getenv("DB_PATH", "astro_tweakin.db")))
        self.admin_ids = _ids(os.getenv("ADMIN_USER_IDS"))
        self.default_announce = int(os.getenv("ANNOUNCE_CHANNEL_ID") or 0) or None
        self.guild_id = int(os.getenv("GUILD_ID") or 0) or None
        # every logged error also gets DM'd to the bot owner (see game/reporter.py)
        self.reporter = ErrorReporter(self)
        logging.getLogger().addHandler(self.reporter)
        self.tree.on_error = self.on_app_command_error
        self.tree.interaction_check = self.channel_check

    async def setup_hook(self):
        self.reporter.start(asyncio.get_running_loop())
        await asyncio.to_thread(backup.make_backup, self.db, "startup")
        self.auto_backup.change_interval(minutes=float(os.getenv("BACKUP_EVERY_MINUTES") or 30))
        self.auto_backup.start()
        self.db.start_flusher()
        await self.load_extension("cogs.game")
        await self.load_extension("cogs.space")
        await self.load_extension("cogs.admin")
        if self.guild_id:
            # syncing to one server makes new commands show up instantly
            guild = discord.Object(self.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

    @tasks.loop(minutes=30)
    async def auto_backup(self):
        try:
            await asyncio.to_thread(backup.make_backup, self.db, "auto")
        except Exception:
            log.exception("Automatic backup failed")

    @auto_backup.before_loop
    async def _skip_first(self):
        await asyncio.sleep(60)

    async def close(self):
        self.auto_backup.cancel()
        try:
            backup.make_backup(self.db, "shutdown")
        except Exception:
            log.exception("Shutdown backup failed")
        self.db.close()
        await super().close()

    async def channel_check(self, interaction):
        """If /admin botchannel set some channels, game commands only work there (admin commands work anywhere)."""
        if interaction.guild is None or (interaction.command and interaction.command.qualified_name.startswith("admin")):
            return True
        allowed = self.db.bot_channels(interaction.guild.id)
        if not allowed or interaction.channel_id in allowed:
            return True
        where = " or ".join(f"<#{c}>" for c in allowed[:5])
        await interaction.response.send_message(f"🚀 Astro Tweakin only works in {where}. Head over there!", ephemeral=True)
        return False

    async def on_app_command_error(self, interaction, error):
        if isinstance(error, discord.app_commands.CheckFailure):
            if interaction.response.is_done():
                return                       # the check already told them where to go
            msg = "You can't use that command."
        else:
            original = getattr(error, "original", error)
            name = interaction.command.qualified_name if interaction.command else "?"
            log.error("Slash command /%s failed for %s (%s)", name, interaction.user.display_name, interaction.user.id,
                      exc_info=(type(original), original, original.__traceback__))
            msg = "Something went wrong. Your progress is safe, nothing was changed."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        if not getattr(self, "_posted_updates", False):
            self._posted_updates = True
            try:
                for title in await asyncio.to_thread(updates.post_pending):
                    log.info("Announced update: %s", title)
            except Exception:
                log.exception("Couldn't post update notes (the bot keeps running)")
        await self.change_presence(activity=discord.Game("/mine"))


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "paste-your-bot-token-here":
        raise SystemExit("Put your bot token in the .env file first (see README).")
    try:
        bot = AstroTweakin()
    except CorruptDatabase as e:
        raise SystemExit(str(e))
    bot.run(token, log_handler=None)
