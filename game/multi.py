"""v0.8.0 multiplayer rules: Rock Paper Scissors, the Market (player shop), tags and /pay.
Kept apart from Discord so it's easy to test. Every coin or item move is one transaction (@atomic)."""
import re
import time
import unicodedata
from dataclasses import dataclass, field

from . import data, engine
from .db import NotEnough
from .engine import atomic, rng


# ================================================================ tags

def random_tag():
    keys = list(data.TAG_WEIGHTS)
    return data.TAG_BY_KEY[rng.choices(keys, weights=[data.TAG_WEIGHTS[k] for k in keys])[0]]


def clean_text(text, limit):
    """Player-written names and titles: one line, no markdown, mentions or links. Returns None if nothing's left."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")      # control chars, newlines
    text = re.sub(r"https?://\S+|discord\.gg/\S+", "", text, flags=re.I)
    text = re.sub(r"[*_~`|>#@\\[\]<]", "", text)
    text = re.sub(r"\s+", " ", text).strip()[:limit].strip()
    return text if sum(ch.isalnum() for ch in text) >= 1 else None


def pick_name(user):
    """The pickaxe's display name: its custom name (from a Rename Tag) if it has one."""
    pick = data.PICKAXE_BY_KEY[user["pickaxe"]]
    return f"“{user['pick_name']}” ({pick.name})" if user["pick_name"] else pick.name


@atomic
def use_rename(db, uid, name):
    name = clean_text(name, data.PICK_NAME_MAX)
    if not name:
        return False, "That name is empty once the weird characters are taken out. Try plain letters."
    db.add_item(uid, "tag:rename", -1)
    db.set(uid, pick_name=name)
    return True, f"🏷️ Your pickaxe is now called **{name}**."


@atomic
def use_title(db, uid, title):
    title = clean_text(title, data.TITLE_MAX)
    if not title:
        return False, "That title is empty once the weird characters are taken out. Try plain letters."
    db.add_item(uid, "tag:title", -1)
    db.set(uid, title="custom:" + title)
    return True, f"📛 You're now known as **{title}**. (Picking an achievement title later replaces it.)"


@atomic
def use_enchreset(db, uid):
    used = db.one("SELECT COUNT(*) AS n FROM enchants WHERE user_id=? AND rerolled=1", uid)["n"]
    if not used:
        return False, "None of your enchant slots have used their reroll, so this would do nothing. Saved it for later."
    db.add_item(uid, "tag:enchreset", -1)
    db.run("UPDATE enchants SET rerolled=0 WHERE user_id=?", uid)
    return True, f"🔄 {used} enchant slot{'s' if used != 1 else ''} got a fresh reroll."


# ================================================================ /pay

@atomic
def pay(db, uid, to, amount):
    if amount <= 0:
        return False, "Send at least 1 coin."
    if uid == to:
        return False, "You can't pay yourself."
    db.user(to)
    try:
        db.add_coins(uid, -amount)
    except NotEnough:
        return False, f"You only have {db.user(uid)['coins']:,} coins."
    db.add_coins(to, amount)
    db.bump(uid, "coins_given", amount)
    return True, None


# ================================================================ Rock Paper Scissors

@dataclass
class RPSGame:
    gid: int
    a: int                          # challenger
    b: int                          # opponent
    bet: int
    names: dict                     # uid -> display name
    avatars: dict = field(default_factory=dict)   # uid -> PNG bytes (or None)
    picks: dict = field(default_factory=dict)     # uid -> "r"/"p"/"s"
    round: int = 1
    ties: list = field(default_factory=list)      # [(pick a, pick b)] of earlier tied rounds
    expires: float = 0.0
    done: bool = False


def rps_winner(pa, pb):
    """0 = player a wins, 1 = player b wins, None = tie."""
    if pa == pb:
        return None
    return 0 if data.RPS_BEATS[pa] == pb else 1


@atomic
def settle_rps(db, winner, loser, bet):
    """Moves the bet and records the result. Raises NotEnough (nothing moves) if the loser spent it."""
    if bet:
        db.add_coins(loser, -bet)
        db.add_coins(winner, bet)
    db.bump(winner, "rps_wins")
    db.bump(loser, "rps_losses")


# ================================================================ Market

CATEGORIES = {
    "all": ("All", "🌐", None),
    "ore": ("Ores & bars", "⛏️", ("ore:", "bar:")),
    "mut": ("Mutations", "🧬", ("mut:",)),
    "tag": ("Tags", "🏷️", ("tag:",)),
    "oth": ("Other", "📦", ("crate:", "rock:", "snack:", "part:")),
}


@dataclass
class Listing:
    id: int
    seller: int
    item: str
    qty: int
    price: int
    expires: float
    seller_name: str = "?"

    @property
    def each(self):
        return self.price // max(1, self.qty)


def _listing(row):
    return Listing(row["id"], row["seller"], row["item"], row["qty"], row["price"], row["expires"],
                   row["name"] if "name" in row.keys() and row["name"] else "?")


def tradeable(item):
    try:
        engine.item_name(item)
    except (KeyError, ValueError):
        return False
    return item.split(":", 1)[0] in ("ore", "bar", "mut", "tag", "crate", "rock", "snack", "part")


def tax(price):
    return int(price * data.MARKET_TAX)


@atomic
def list_item(db, uid, item, qty, price):
    if not tradeable(item):
        return False, "That can't be sold on the Market."
    if qty <= 0:
        return False, "List at least 1."
    if not 1 <= price <= data.MARKET_MAX_PRICE:
        return False, "Pick a price of at least 1 coin."
    have = db.qty(uid, item)
    if have < qty:
        return False, f"You only have {have:,}x {engine.item_name(item)}."
    n = db.one("SELECT COUNT(*) AS n FROM market WHERE seller=? AND status='open'", uid)["n"]
    if n >= data.MARKET_MAX_LISTINGS:
        return False, f"You already have {n} things listed (max {data.MARKET_MAX_LISTINGS}). Cancel one or wait for a sale."
    db.add_item(uid, item, -qty)            # held by the Market until it sells, you cancel, or it expires
    now = time.time()
    cur = db.run("INSERT INTO market (seller, item, qty, price, listed, expires) VALUES (?, ?, ?, ?, ?, ?)",
                 uid, item, qty, price, now, now + data.MARKET_HOURS * 3600)
    return True, (f"📤 Listed **{qty:,}x {engine.item_name(item)}** for **{price:,}** coins (listing #{cur.lastrowid}). "
                  f"It comes back to you if nobody buys it in {data.MARKET_HOURS}h.")


@atomic
def buy(db, uid, lid):
    """Returns (ok, message, listing or None)."""
    row = db.one("SELECT * FROM market WHERE id=?", lid)
    if row is None or row["status"] != "open" or row["expires"] < time.time():
        return False, "Someone beat you to it: that listing is gone.", None
    if row["seller"] == uid:
        return False, "That's your own listing. Cancel it from 📋 My listings instead.", None
    try:
        db.add_coins(uid, -row["price"])
    except NotEnough:
        return False, f"That costs **{row['price']:,}** coins. You have {db.user(uid)['coins']:,}.", None
    cut = tax(row["price"])
    db.add_coins(row["seller"], row["price"] - cut)
    db.add_item(uid, row["item"], row["qty"])
    db.run("UPDATE market SET status='sold', buyer=?, done_at=? WHERE id=?", uid, time.time(), lid)
    db.bump(row["seller"], "market_sales")
    db.bump(uid, "market_buys")
    return True, (f"🛍️ You bought **{row['qty']:,}x {engine.item_name(row['item'])}** for **{row['price']:,}** coins."), _listing(row)


@atomic
def cancel(db, uid, lid):
    row = db.one("SELECT * FROM market WHERE id=?", lid)
    if row is None or row["seller"] != uid or row["status"] != "open":
        return False, "That listing isn't open anymore."
    db.add_item(uid, row["item"], row["qty"])
    db.run("UPDATE market SET status='cancelled', done_at=? WHERE id=?", time.time(), lid)
    return True, f"↩️ Took back **{row['qty']:,}x {engine.item_name(row['item'])}**."


@atomic
def expire(db, now=None):
    """Returns expired listings' items to their sellers. Returns how many came back."""
    now = now or time.time()
    rows = db.q("SELECT * FROM market WHERE status='open' AND expires < ?", now)
    for row in rows:
        db.add_item(row["seller"], row["item"], row["qty"])
        db.run("UPDATE market SET status='expired', done_at=? WHERE id=?", now, row["id"])
    return len(rows)


def browse(db, cat="all", page=0):
    """One page of open listings. Returns (listings, total pages)."""
    prefixes = CATEGORIES.get(cat, CATEGORIES["all"])[2]
    where, args = "m.status='open' AND m.expires >= ?", [time.time()]
    if prefixes:
        where += " AND (" + " OR ".join("m.item LIKE ?" for _ in prefixes) + ")"
        args += [p + "%" for p in prefixes]
    total = db.one(f"SELECT COUNT(*) AS n FROM market m WHERE {where}", *args)["n"]
    pages = max(1, -(-total // data.MARKET_PAGE))
    page = max(0, min(page, pages - 1))
    rows = db.q(f"SELECT m.*, u.name FROM market m LEFT JOIN users u ON u.id = m.seller WHERE {where} "
                f"ORDER BY m.id DESC LIMIT ? OFFSET ?", *args, data.MARKET_PAGE, page * data.MARKET_PAGE)
    return [_listing(r) for r in rows], pages, page


def get(db, lid):
    row = db.one("SELECT m.*, u.name FROM market m LEFT JOIN users u ON u.id = m.seller WHERE m.id=?", lid)
    return (_listing(row), row["status"]) if row else (None, None)


def my_listings(db, uid):
    rows = db.q("SELECT m.*, u.name FROM market m LEFT JOIN users u ON u.id = m.seller "
                "WHERE m.seller=? AND m.status='open' ORDER BY m.id", uid)
    return [_listing(r) for r in rows]


def recent_sales(db, uid, limit=5):
    """Your latest sales. Marks them seen (they stop counting as 'new')."""
    rows = db.q("SELECT m.*, u.name AS buyer_name FROM market m LEFT JOIN users u ON u.id = m.buyer "
                "WHERE m.seller=? AND m.status IN ('sold', 'expired') ORDER BY m.done_at DESC LIMIT ?", uid, limit)
    db.run("UPDATE market SET seen=1 WHERE seller=? AND seen=0 AND status IN ('sold', 'expired')", uid)
    return rows


def unseen_sales(db, uid):
    return db.one("SELECT COUNT(*) AS n FROM market WHERE seller=? AND seen=0 AND status='sold'", uid)["n"]
