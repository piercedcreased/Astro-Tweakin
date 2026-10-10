"""Posts update notes to your Discord update channel through a webhook, with your custom GIF.

    python tools/post_update.py updates/0.1.0.md
    python tools/post_update.py               -> posts the newest file in updates/

The first "# " line of the file becomes the title, the rest is the message.
Set UPDATE_WEBHOOK_URL in .env. The image is assets/update.gif, .png or .jpg (first one found)
unless you set UPDATE_GIF to another file.
Add --dry-run to print what would be sent without posting.
"""
import glob
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
        notes = sorted(glob.glob(os.path.join(ROOT, "updates", "*.md")), key=os.path.getmtime)
        if not notes:
            raise SystemExit("No update notes found in the updates folder.")
        args = [notes[-1]]
        print("Posting the newest notes:", os.path.basename(notes[-1]))
    with open(args[0], encoding="utf-8") as f:
        text = f.read().strip()
    title, body = "Astro Tweakin Update", text
    if text.startswith("# "):
        title, _, body = text.partition("\n")
        title = title[2:].strip()
    url = os.getenv("UPDATE_WEBHOOK_URL")
    gif = os.getenv("UPDATE_GIF")
    if not gif:
        for ext in ("gif", "png", "jpg", "jpeg", "webp"):
            gif = os.path.join(ROOT, "assets", "update." + ext)
            if os.path.exists(gif):
                break
    has_gif = os.path.exists(gif)
    ext = os.path.splitext(gif)[1].lower().lstrip(".") or "gif"
    fname = "update." + ext
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext, "image/gif")

    embed = {"title": title, "description": body.strip()[:4000], "color": 0x7CE650}
    if has_gif:
        embed["image"] = {"url": f"attachment://{fname}"}
    payload = {"username": "Astro Tweakin Updates", "embeds": [embed]}
    mention = os.getenv("UPDATE_PING")          # e.g. @everyone or <@&ROLE_ID>
    if mention:
        payload["content"] = mention
        payload["allowed_mentions"] = {"parse": ["everyone", "roles"]}

    if dry:
        print(json.dumps(payload, indent=2))
        print("Image:", gif if has_gif else "(none found, posting without one)")
        return
    if not url:
        raise SystemExit("Set UPDATE_WEBHOOK_URL in .env first (see README).")
    files = {"files[0]": (fname, open(gif, "rb"), mime)} if has_gif else None
    r = requests.post(url, data={"payload_json": json.dumps(payload)}, files=files, timeout=30)
    if r.status_code >= 300:
        raise SystemExit(f"Discord said {r.status_code}: {r.text}")
    print("Posted! Check your update channel:", title)


if __name__ == "__main__":
    main()
