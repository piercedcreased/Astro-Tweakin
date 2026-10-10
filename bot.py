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

    async def setup_hook(self):
        await asyncio.to_thread(backup.make_backup, self.db, "startup")
        self.auto_backup.change_interval(minutes=float(os.getenv("BACKUP_EVERY_MINUTES") or 30))
        self.auto_backup.start()
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
