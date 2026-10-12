"""Automatic backups of the save file.

- A backup is taken when the bot starts, every BACKUP_EVERY_MINUTES (default 30), and when it shuts down.
- Uses SQLite's online backup, so it's a clean copy even while players are mining.
- Keeps the newest 48 backups plus one per day for the last 30 days.
- Set BACKUP_DIR in .env to also keep them somewhere else (a OneDrive or Google Drive folder is a good
  off-PC copy in case the drive dies).
"""
import datetime
import glob
import logging
import os
import sqlite3

log = logging.getLogger("astro-tweakin.backup")

KEEP_RECENT = 48
KEEP_DAILY_DAYS = 30


def backup_dir():
    return os.getenv("BACKUP_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backups")


def make_backup(db, reason="auto"):
    folder = backup_dir()
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(folder, f"astro_tweakin_{stamp}_{reason}.db")
    tmp = path + ".partial"
    # Reads through its own connection: WAL lets it copy every finished save while the game keeps going.
    # (It used to hold the game's lock, which froze every button for the whole backup.)
    src = sqlite3.connect(db.path, timeout=30)
    dest = sqlite3.connect(tmp)
    try:
        src.backup(dest)
        ok = dest.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        dest.close()
        src.close()
    if not ok:
        os.remove(tmp)
        raise RuntimeError("Backup failed its integrity check, so it was thrown away.")
    os.replace(tmp, path)
    prune(folder)
    log.info("Backup saved: %s", path)
    return path


def list_backups(folder=None):
    return sorted(glob.glob(os.path.join(folder or backup_dir(), "astro_tweakin_*.db")), reverse=True)


def prune(folder):
    files = list_backups(folder)
    keep = set(files[:KEEP_RECENT])
    days = {}
    cutoff = datetime.datetime.now() - datetime.timedelta(days=KEEP_DAILY_DAYS)
    for f in files:
        try:
            when = datetime.datetime.strptime(os.path.basename(f)[14:33], "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            continue
        if when >= cutoff and when.date() not in days:
            days[when.date()] = f
    keep.update(days.values())
    for f in files:
        if f not in keep:
            try:
                os.remove(f)
            except OSError:
                pass
