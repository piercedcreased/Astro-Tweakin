"""Posts update notes to your Discord update channel through a webhook, with your custom GIF.

    python tools/post_update.py updates/0.1.0.md

The first "# " line of the file becomes the title, the rest is the message.
Set UPDATE_WEBHOOK_URL in .env. The GIF is assets/update.gif unless you set UPDATE_GIF.
Add --dry-run to print what would be sent without posting.
"""
import json
import os
import sys

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        raise SystemExit("Usage: python tools/post_update.py updates/<version>.md [--dry-run]")
    with open(args[0], encoding="utf-8") as f:
        text = f.read().strip()
    title, body = "Astro Tweakin Update", text
    if text.startswith("# "):
        title, _, body = text.partition("\n")
        title = title[2:].strip()
    url = os.getenv("UPDATE_WEBHOOK_URL")
    gif = os.getenv("UPDATE_GIF") or os.path.join(ROOT, "assets", "update.gif")
    has_gif = os.path.exists(gif)

    embed = {"title": title, "description": body.strip()[:4000], "color": 0x7CE650}
    if has_gif:
        embed["image"] = {"url": "attachment://update.gif"}
    payload = {"username": "Astro Tweakin Updates", "embeds": [embed]}
    mention = os.getenv("UPDATE_PING")          # e.g. @everyone or <@&ROLE_ID>
    if mention:
        payload["content"] = mention
        payload["allowed_mentions"] = {"parse": ["everyone", "roles"]}

    if dry:
        print(json.dumps(payload, indent=2))
        print("GIF:", gif if has_gif else "(none found, posting without one)")
        return
    if not url:
        raise SystemExit("Set UPDATE_WEBHOOK_URL in .env first (see README).")
    files = {"files[0]": ("update.gif", open(gif, "rb"), "image/gif")} if has_gif else None
    r = requests.post(url, data={"payload_json": json.dumps(payload)}, files=files, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"Discord said {r.status_code}: {r.text}")
    print("Posted update:", title)


if __name__ == "__main__":
    main()
