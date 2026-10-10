"""Restore player data from a backup. Stop the bot first (close its window).

    python tools/restore_backup.py           -> lists backups and asks which one
    python tools/restore_backup.py latest    -> restores the newest one

Your current save is never deleted: it's renamed to astro_tweakin.db.before-restore-<time> first.
"""
import datetime
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))
from game import backup  # noqa: E402

DB = os.path.join(ROOT, os.getenv("DB_PATH", "astro_tweakin.db"))


def players(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        c.close()
        return f"{n} players" + ("" if ok else ", DAMAGED")
    except Exception as e:
        return f"unreadable ({e})"


def main():
    files = backup.list_backups()
    if not files:
        raise SystemExit(f"No backups found in {backup.backup_dir()}")
    if len(sys.argv) > 1 and sys.argv[1] == "latest":
        pick = files[0]
    else:
        for i, f in enumerate(files[:30], 1):
            print(f"{i:>2}. {os.path.basename(f)}  ({players(f)})")
        choice = input("Number to restore (Enter to cancel): ").strip()
        if not choice:
            return
        pick = files[int(choice) - 1]
    if input(f"Restore {os.path.basename(pick)}? Type yes: ").strip().lower() != "yes":
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(DB + ext):
            os.replace(DB + ext, f"{DB}.before-restore-{stamp}{ext}")
    shutil.copy2(pick, DB)
    print(f"Restored. Your old save was kept as {os.path.basename(DB)}.before-restore-{stamp}. Start the bot again.")


if __name__ == "__main__":
    main()
