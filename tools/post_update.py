"""Posts update notes to your Discord update channel through the webhook, with the update image.

You usually don't need this: the bot posts new notes by itself when it starts.
Use it to post (or re-post) one by hand:

    python tools/post_update.py                    -> posts the newest file in updates/
    python tools/post_update.py updates/0.1.0.md   -> posts that file
    add --dry-run to see what would be sent without posting
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(ROOT, ".env"))
from game import updates  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        notes = updates.all_notes()
        if not notes:
            raise SystemExit("No update notes found in the updates folder.")
        args = [notes[-1]]
        print("Posting the newest notes:", os.path.basename(notes[-1]))
    if "--dry-run" in sys.argv:
        title, payload, img, _ = updates.build(args[0])
        print(json.dumps(payload, indent=2))
        print("Image:", img or "(none found, posting without one)")
        return
    if updates.posted() is None:
        # first use: remember the existing notes so the bot doesn't post them again on startup
        for p in updates.all_notes():
            if p != args[0]:
                updates.mark_posted(p)
    try:
        title = updates.post(args[0])
    except RuntimeError as e:
        raise SystemExit(str(e))
    print("Posted! Check your update channel:", title)


if __name__ == "__main__":
    main()
