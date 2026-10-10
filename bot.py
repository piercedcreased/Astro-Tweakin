"""Astro Tweakin: a space mining incremental Discord bot.

Run with start.bat on Windows, or: python bot.py
"""
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from game.db import DB

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("astro-tweakin")


def _ids(value):
    return {int(x) for x in (value or "").replace(" ", "").split(",") if x}


class AstroTweakin(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.db = DB(os.getenv("DB_PATH", "astro_tweakin.db"))
        self.admin_ids = _ids(os.getenv("ADMIN_USER_IDS"))
        self.default_announce = int(os.getenv("ANNOUNCE_CHANNEL_ID") or 0) or None
        self.guild_id = int(os.getenv("GUILD_ID") or 0) or None

    async def setup_hook(self):
        await self.load_extension("cogs.game")
        await self.load_extension("cogs.admin")
        if self.guild_id:
            # syncing to one server makes new commands show up instantly
            guild = discord.Object(self.guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        log.info("Synced %d slash commands", len(synced))

    async def on_ready(self):
        log.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(activity=discord.Game("/mine"))


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "paste-your-bot-token-here":
        raise SystemExit("Put your bot token in the .env file first (see README).")
    AstroTweakin().run(token, log_handler=None)
