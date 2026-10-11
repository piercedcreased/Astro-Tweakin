"""Astro Tweakin: a space mining incremental Discord bot.

Run with start.bat on Windows, or: python bot.py
"""
import asyncio
import glob
import logging
import os
import time

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
        self.code_stamp = self.code_files_stamp()
        self.code_changed_at = None
        if (os.getenv("AUTO_RESTART") or "on").lower() not in ("off", "0", "false", "no"):
            self.watch_code.start()
        await self.load_extension("cogs.game")
        await self.load_extension("cogs.space")
        await self.load_extension("cogs.admin")

    async def sync_guild(self, guild):
        """Loads the slash commands into one server. Per-server syncing shows them instantly."""
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d slash commands to %s", len(synced), getattr(guild, "name", guild.id))
        except discord.HTTPException as e:
            log.warning("Couldn't sync commands to %s: %s (was the bot invited with applications.commands?)",
                        getattr(guild, "name", guild.id), e)

    async def on_guild_join(self, guild):
        log.info("Joined a new server: %s", guild.name)
        await self.sync_guild(guild)

    @tasks.loop(minutes=30)
    async def auto_backup(self):
        try:
            await asyncio.to_thread(backup.make_backup, self.db, "auto")
        except Exception:
            log.exception("Automatic backup failed")

    # ------------------------------------------------------------ auto restart on new code

    @staticmethod
    def code_files():
        files = [os.path.join(ROOT, "bot.py")]
        for pattern in ("game/*.py", "cogs/*.py", "updates/*.md", "requirements.txt"):
            files += glob.glob(os.path.join(ROOT, pattern))
        return sorted(files)

    def code_files_stamp(self):
        out = {}
        for f in self.code_files():
            try:
                st = os.stat(f)
                out[f] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
        return out

    @tasks.loop(seconds=5)
    async def watch_code(self):
        """When new bot files land in the folder (an update), restart to load them. Waits until the files
        stop changing for 15 seconds and checks the new code has no syntax errors first."""
        try:
            stamp = await asyncio.to_thread(self.code_files_stamp)
            if stamp != self.code_stamp:
                self.code_stamp = stamp
                self.code_changed_at = time.time()
                log.info("New bot files detected, restarting once they stop changing...")
                return
            if self.code_changed_at and time.time() - self.code_changed_at >= 15:
                self.code_changed_at = None
                bad = await asyncio.to_thread(self.broken_files)
                if bad:
                    log.error("New update has errors, NOT restarting (the old version keeps running):\n%s", "\n".join(bad))
                    return
                log.info("Restarting to load the new update")
                await self.close()      # start.bat starts it again with the new code
        except Exception:
            log.exception("Update watcher failed")

    def broken_files(self):
        bad = []
        for f in self.code_files():
            if f.endswith(".py"):
                try:
                    with open(f, encoding="utf-8") as fh:
                        compile(fh.read(), f, "exec")
                except (SyntaxError, ValueError, UnicodeDecodeError) as e:
                    bad.append(f"{os.path.relpath(f, ROOT)}: {e}")
        return bad

    @auto_backup.before_loop
    async def _skip_first(self):
        await asyncio.sleep(60)

    async def close(self):
        self.auto_backup.cancel()
        if self.watch_code.is_running():
            self.watch_code.cancel()
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
        if not getattr(self, "_synced", False):
            self._synced = True
            for guild in self.guilds:          # every server the bot is in, not just GUILD_ID
                await self.sync_guild(guild)
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
