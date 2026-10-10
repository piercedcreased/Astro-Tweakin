"""Update notes -> Discord webhook.

Drop a new file in updates/ (e.g. updates/0.2.0.md, first "# " line is the title).
The next time the bot starts it posts every notes file it hasn't posted yet, with a random
image or GIF from the update_images/ folder. Posted files are remembered in updates/posted.txt.
"""
import glob
import json
import logging
import os
import random

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPDATES = os.path.join(ROOT, "updates")
POSTED = os.path.join(UPDATES, "posted.txt")
IMAGES = os.path.join(ROOT, "update_images")
IMAGE_TYPES = (".gif", ".png", ".jpg", ".jpeg", ".webp")
MAX_BYTES = 10 * 1024 * 1024       # Discord's upload limit for webhooks
log = logging.getLogger("astro-tweakin.updates")


def all_notes():
    return sorted(glob.glob(os.path.join(UPDATES, "*.md")), key=os.path.getmtime)


def posted():
    if not os.path.exists(POSTED):
        return None
    with open(POSTED, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def mark_posted(path):
    with open(POSTED, "a", encoding="utf-8") as f:
        f.write(os.path.basename(path) + "\n")


def image():
    """A random image/GIF from update_images/ (skipping files too big for Discord)."""
    gif = os.getenv("UPDATE_GIF")
    if gif:
        return gif if os.path.exists(gif) else None
    if os.path.isdir(IMAGES):
        pool = [os.path.join(IMAGES, f) for f in os.listdir(IMAGES)
                if f.lower().endswith(IMAGE_TYPES) and os.path.getsize(os.path.join(IMAGES, f)) <= MAX_BYTES]
        if pool:
            return random.choice(pool)
    for ext in ("gif", "png", "jpg", "jpeg", "webp"):     # older single-image setup
        p = os.path.join(ROOT, "assets", "update." + ext)
        if os.path.exists(p):
            return p
    return None


def build(path):
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    title, body = "Astro Tweakin Update", text
    if text.startswith("# "):
        title, _, body = text.partition("\n")
        title = title[2:].strip()
    img = image()
    embed = {"title": title, "description": body.strip()[:4000], "color": 0x7CE650}
    fname = None
    if img:
        fname = "update" + os.path.splitext(img)[1].lower()
        embed["image"] = {"url": f"attachment://{fname}"}
    payload = {"username": "Astro Tweakin Updates", "embeds": [embed]}
    mention = os.getenv("UPDATE_PING")          # e.g. @everyone or <@&ROLE_ID>
    if mention:
        payload["content"] = mention
        payload["allowed_mentions"] = {"parse": ["everyone", "roles"]}
    return title, payload, img, fname


def post(path):
    """Posts one notes file. Returns its title. Raises RuntimeError on failure."""
    url = os.getenv("UPDATE_WEBHOOK_URL")
    if not url:
        raise RuntimeError("Set UPDATE_WEBHOOK_URL in .env first (see README).")
    title, payload, img, fname = build(path)
    files = None
    if img:
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(
            os.path.splitext(img)[1].lower(), "image/gif")
        files = {"files[0]": (fname, open(img, "rb"), mime)}
    r = requests.post(url, data={"payload_json": json.dumps(payload)}, files=files, timeout=30)
    if r.status_code >= 300:
        raise RuntimeError(f"Discord said {r.status_code}: {r.text}")
    mark_posted(path)
    return title


def post_pending():
    """Posts notes that haven't been posted yet. The very first run only remembers what's
    already there (those were posted by hand), so nothing gets double-posted."""
    done = posted()
    notes = all_notes()
    if done is None:
        for p in notes:
            mark_posted(p)
        log.info("Update notes: remembered %d existing file(s) as already posted", len(notes))
        return []
    if not os.getenv("UPDATE_WEBHOOK_URL"):
        return []
    out = []
    for p in notes:
        if os.path.basename(p) not in done:
            out.append(post(p))
            log.info("Posted update notes: %s", os.path.basename(p))
    return out
