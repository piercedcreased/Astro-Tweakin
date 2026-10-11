"""Error reports: every error the bot logs gets written to logs/errors.log and DM'd to the bot owner
as a troubleshooting file, so it can be sent back for a fix.

Who gets the DMs: ERROR_DM_USER_IDS in .env (comma separated), or whoever owns the bot application
in the Discord Developer Portal if that's empty.
"""
import asyncio
import datetime
import io
import logging
import logging.handlers
import os
import platform
import sys
import time
import traceback

import discord

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
REPEAT_WINDOW = 15 * 60      # the same error is only DM'd once per 15 minutes (repeats are counted)
MAX_DMS_PER_HOUR = 20

log = logging.getLogger("astro-tweakin.reporter")


def version():
    notes = os.path.join(ROOT, "updates")
    try:
        names = sorted((f[:-3] for f in os.listdir(notes) if f.endswith(".md")),
                       key=lambda v: [int(x) if x.isdigit() else x for x in v.split(".")])
        return names[-1] if names else "?"
    except OSError:
        return "?"


class ErrorReporter(logging.Handler):
    """Logging handler that catches ERROR records from anywhere (buttons, commands, background loops,
    discord.py itself, other threads) and hands them to the bot to DM."""

    def __init__(self, bot):
        super().__init__(level=logging.ERROR)
        self.bot = bot
        self.loop = None
        self.queue = None
        self.seen = {}           # signature -> [first_time, count_since_last_dm]
        self.sent = []           # times of recent DMs
        self.recipients = []
        os.makedirs(LOG_DIR, exist_ok=True)
        self.file = logging.handlers.RotatingFileHandler(os.path.join(LOG_DIR, "errors.log"), maxBytes=2_000_000,
                                                         backupCount=3, encoding="utf-8")
        self.file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def start(self, loop):
        self.loop = loop
        self.queue = asyncio.Queue()
        loop.create_task(self._run())

    def emit(self, record):
        if record.name.startswith("astro-tweakin.reporter"):
            return
        try:
            self.file.emit(record)
        except Exception:
            pass
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, record)
        except RuntimeError:
            pass

    @staticmethod
    def signature(record):
        if record.exc_info and record.exc_info[1] is not None:
            tb = traceback.extract_tb(record.exc_info[2])
            where = f"{tb[-1].filename}:{tb[-1].lineno}" if tb else ""
            return f"{type(record.exc_info[1]).__name__}@{where}"
        return f"{record.name}:{record.msg}"

    async def _recipients(self):
        if self.recipients:
            return self.recipients
        ids = [int(x) for x in (os.getenv("ERROR_DM_USER_IDS") or "").replace(" ", "").split(",") if x.isdigit()]
        if not ids:
            app = await self.bot.application_info()
            ids = [m.id for m in app.team.members] if app.team else [app.owner.id]
        self.recipients = ids
        return ids

    def report_text(self, record, repeats):
        when = datetime.datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "ASTRO TWEAKIN ERROR REPORT",
            f"Time:      {when}",
            f"Version:   {version()}",
            f"Where:     {record.name} ({record.pathname}:{record.lineno})",
            f"What:      {record.getMessage()}",
            f"Python {sys.version.split()[0]} · discord.py {discord.__version__} · {platform.platform()}",
        ]
        if repeats:
            lines.append(f"Repeats:   happened {repeats} more time(s) since the last report")
        lines.append("")
        if record.exc_info:
            lines.append("".join(traceback.format_exception(*record.exc_info)))
        recent = self.recent_log()
        if recent:
            lines += ["", "---- last lines of logs/errors.log ----", recent]
        return "\n".join(lines)

    @staticmethod
    def recent_log(n=40):
        try:
            with open(os.path.join(LOG_DIR, "errors.log"), encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except OSError:
            return ""

    async def _run(self):
        await self.bot.wait_until_ready()
        while True:
            record = await self.queue.get()
            try:
                await self._handle(record)
            except Exception as e:
                log.warning("Couldn't DM an error report: %s", e)

    async def _handle(self, record):
        now = time.time()
        sig = self.signature(record)
        entry = self.seen.get(sig)
        if entry and now - entry[0] < REPEAT_WINDOW:
            entry[1] += 1
            return
        repeats = entry[1] if entry else 0
        self.seen[sig] = [now, 0]
        self.sent = [t for t in self.sent if now - t < 3600]
        if len(self.sent) >= MAX_DMS_PER_HOUR:
            return
        self.sent.append(now)
        text = self.report_text(record, repeats)
        short = record.getMessage().splitlines()[0][:300]
        err = f"{type(record.exc_info[1]).__name__}: {str(record.exc_info[1])[:300]}" if record.exc_info and record.exc_info[1] else ""
        msg = (f"⚠️ **Astro Tweakin hit an error** (v{version()})\n> {short}\n" + (f"`{err}`\n" if err else "") +
               "If a player hit this, they got a \"something went wrong\" message and their save wasn't changed. "
               "Send the attached file to Claude to get it fixed.")
        stamp = datetime.datetime.fromtimestamp(record.created).strftime("%Y%m%d-%H%M%S")
        for uid in await self._recipients():
            user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
            await user.send(msg[:2000], file=discord.File(io.BytesIO(text.encode("utf-8")), filename=f"error-{stamp}.txt"))
