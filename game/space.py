"""v0.3.0 Smuggler's Run rules: Space Authority cargo checks, spaceship upgrades, raids, duels,
Space Snacks, the bounty board, achievements, meteor strikes and Tweaker Tim.

Like engine.py, nothing in here touches Discord, and every action that changes a save is atomic.
"""
import datetime
import json
import random
import time
from dataclasses import dataclass, field

from . import data, engine
from .data import ORE_BY_KEY, LAYERS
from .engine import atomic, rng


def scale(user):
    return data.scale(user["max_layer"])


# ================================================================ hot cargo and cargo checks

def hot_items(db, uid):
    """Legendary-or-rarer ores you're carrying, cheapest first."""
    floor = data.TIER_ORDER.index(data.HOT_TIER)
    out = []
    for item, qty in db.items(uid, "ore:").items():
        ore = ORE_BY_KEY.get(item[4:])
        if ore and data.TIER_ORDER.index(ore.tier) >= floor:
            out.append(ore)
    return sorted(out, key=lambda o: o.value)


def carrying_contraband(hot):
    return any(o.tier == "contraband" for o in hot)


def check_chance(user, hot):
    if not hot:
        return 0.0
    base = data.CHECK_CHANCE_CONTRABAND if carrying_contraband(hot) else data.CHECK_CHANCE_HOT
    return base * max(0.1, 1 - data.STASH_CHECK_CUT * user["ship_stash"])


def should_check(db, uid, travel=False):
    """Rolls whether the Space Authority pulls this player over right now."""
    user = db.user(uid)
    if time.time() - user["last_check"] < data.CHECK_COOLDOWN or db.buffs(uid).get("nocops"):
        return False
    hot = hot_items(db, uid)
    if travel:
        p = data.CHECK_CHANCE_TRAVEL if carrying_contraband(hot) else 0.0
    else:
        p = check_chance(user, hot)
    if p and rng.random() < p:
        db.set(uid, last_check=time.time())
        return True
    return False


@dataclass
class Check:
    """One cargo check in progress. Lives in memory; if the bot restarts mid-check the cops just leave."""
    uid: int
    name: str
    officer: str
    contraband: bool
    deadline: float
    step: str = "choose"            # choose, stash, talk, sniff
    spots: list = field(default_factory=list)
    searched: set = field(default_factory=set)
    mood: tuple = None
    excuses: list = field(default_factory=list)
    treat: tuple = None
    treats: list = field(default_factory=list)
    message: object = None
    guild: object = None
    done: bool = False


def new_check(db, uid, name):
    hot = hot_items(db, uid)
    c = Check(uid=uid, name=name, officer=rng.choice(data.OFFICER_NAMES), contraband=carrying_contraband(hot),
              deadline=time.time() + data.CHECK_STEP_SECONDS)
    c.spots = rng.sample(data.STASH_SPOTS, 3)
    c.searched = set(rng.sample(range(3), 2 if c.contraband else 1))
    c.mood = rng.choice(data.OFFICER_MOODS)
    others = [m[2] for m in data.OFFICER_MOODS if m != c.mood]
    c.excuses = rng.sample(others, 2) + [c.mood[2]]
    rng.shuffle(c.excuses)
    c.treat = rng.choice(data.SNIFFLES_TREATS)
    c.treats = rng.sample([t[0] for t in data.SNIFFLES_TREATS if t != c.treat], 3) + [c.treat[0]]
    rng.shuffle(c.treats)
    return c


def bribe_cost(user):
    return max(data.BRIBE_MIN, int(user["coins"] * data.BRIBE_SHARE))


@atomic
def check_clean(db, uid):
    db.bump(uid, "checks_survived")


@atomic
def check_bust(db, uid):
    """Fine plus one hot ore seized (the cheapest one). Returns (fine, seized ore or None)."""
    user = db.user(uid)
    hot = hot_items(db, uid)
    share = data.FINE_CONTRABAND if carrying_contraband(hot) else data.FINE_HOT
    fine = int(user["coins"] * share)
    if fine:
        db.add_coins(uid, -fine)
    seized = hot[0] if hot else None
    if seized:
        db.add_item(uid, "ore:" + seized.key, -1)
    db.bump(uid, "checks_busted")
    return fine, seized


@atomic
def check_jettison(db, uid):
    """Dump your cheapest hot ore out the airlock. No fine, no record."""
    hot = hot_items(db, uid)
    if not hot:
        return None
    db.add_item(uid, "ore:" + hot[0].key, -1)
    return hot[0]


@atomic
def check_bribe(db, uid):
    """Returns (paid, worked). Bribe money is gone either way."""
    user = db.user(uid)
    cost = bribe_cost(user)
    db.add_coins(uid, -cost)
    return cost, rng.random() < data.BRIBE_SUCCESS


def stash_outcome(check, spot, stash_level):
    """True if the stash spot wasn't found."""
    if spot not in check.searched:
        return True
    return rng.random() < data.STASH_MISS * stash_level


def talk_outcome(check, choice):
    right = check.excuses[choice] == check.mood[2]
    return rng.random() < (data.TALK_RIGHT if right else data.TALK_WRONG), right


def sniff_outcome(check, choice):
    return check.treats[choice] == check.treat[0]


# ================================================================ spaceship

def upgrade_cost(user, up):
    level = user["ship_" + up.key] + 1
    return up.base * scale(user) * level * level, {f"bar:{up.bar}": 10 * level}


@atomic
def upgrade_ship(db, uid, key):
    up = data.SHIP_BY_KEY[key]
    user = db.user(uid)
    level = user["ship_" + key]
    if level >= up.max_level:
        return False, f"Your {up.name} is already maxed out."
    coins, mats = upgrade_cost(user, up)
    missing = engine.missing_for(db, uid, coins, mats)
    if missing:
        return False, f"You need {', '.join(missing)} for {up.name} level {level + 1}."
    engine.pay(db, uid, coins, mats)
    db.set(uid, **{"ship_" + key: level + 1})
    return True, f"{up.emoji} **{up.name}** is now level **{level + 1}**!"


def paint_cost(user):
    return data.PAINT_COST * scale(user)


@atomic
def repaint(db, uid):
    user = db.user(uid)
    cost = paint_cost(user)
    if user["coins"] < cost:
        return False, f"A paint job costs {cost:,} coins."
    db.add_coins(uid, -cost)
    paint = (user["ship_paint"] + 1) % len(data.PAINTS)
    db.set(uid, ship_paint=paint)
    return True, f"🎨 Fresh paint: **{data.PAINTS[paint][0]}**."


# ================================================================ raids

def raid_chance(attacker, target):
    p = data.RAID_BASE + data.RAID_ENGINE * attacker["ship_engine"] - data.RAID_HULL * target["ship_hull"]
    return min(data.RAID_MAX, max(data.RAID_MIN, p))


@dataclass
class RaidResult:
    ok: bool
    message: str = ""
    success: bool = False
    chance: float = 0.0
    ores: list = field(default_factory=list)
    best: object = None


@atomic
def raid(db, uid, target_id):
    """A luck check for stuff the target "forgot" on their ship. The target never loses anything."""
    if target_id == uid:
        return RaidResult(False, "You can't raid your own ship. Well, you can, but you'd just find your own socks.")
    target = db.one("SELECT * FROM users WHERE id=?", target_id)
    if target is None:
        return RaidResult(False, "They don't have a ship yet. They need to `/mine` at least once.")
    user = db.user(uid)
    now = time.time()
    wait = user["last_raid"] + data.RAID_COOLDOWN - now
    if wait > 0:
        return RaidResult(False, f"Your crew is still hungover from the last raid. Try again in {engine.fmt_time(wait)}.")
    last = db.one("SELECT MAX(at) AS at FROM raids WHERE attacker=? AND target=?", uid, target_id)["at"]
    if last and now - last < data.RAID_PROTECT:
        return RaidResult(False, f"{target['name']}'s ship is on high alert after your last raid. "
                                 f"Try again in {engine.fmt_time(last + data.RAID_PROTECT - now)}, or raid someone else.")
    db.set(uid, last_raid=now)
    db.run("INSERT INTO raids (attacker, target, at) VALUES (?, ?, ?)", uid, target_id, now)
    db.bump(uid, "raids")
    chance = raid_chance(user, target)
    if rng.random() >= chance:
        db.bump(target_id, "raids_defended")
        return RaidResult(True, success=False, chance=chance)
    layer = min(user["max_layer"], target["layer"])
    luck = engine.luck_for(db, user).total * data.RAID_LUCK
    ores = [engine.roll_ore(layer, luck) for _ in range(rng.randint(*data.RAID_LOOT))]
    best = engine.record_finds(db, uid, ores)
    db.bump(uid, "raids_won")
    return RaidResult(True, success=True, chance=chance, ores=ores, best=best)


# ================================================================ duels

def duel_power(user):
    return 1 + 0.1 * user["ship_engine"]


def fight(a, b):
    """Best of 3 blaster rounds. Returns (winner index 0/1, [(roll_a, roll_b), ...])."""
    pa, pb = duel_power(a), duel_power(b)
    wins = [0, 0]
    rounds = []
    while max(wins) < 2:
        ra, rb = rng.randint(1, 100) * pa, rng.randint(1, 100) * pb
        if ra == rb:
            continue
        rounds.append((int(ra), int(rb)))
        wins[0 if ra > rb else 1] += 1
    return (0 if wins[0] == 2 else 1), rounds


@atomic
def settle_duel(db, winner, loser, wager):
    if wager:
        db.add_coins(loser, -wager)          # NotEnough if they spent it while the challenge was open
        db.add_coins(winner, wager)
    db.bump(winner, "duels_won")
    db.bump(winner, "duels")
    db.bump(loser, "duels")


# ================================================================ space snacks

def snack_cost(user, snack):
    base = engine.ores_for_layer(user["layer"])[-1]
    return snack.coins * scale(user), {"ore:" + base.key: snack.ore_qty}


@atomic
def make_snack(db, uid, key):
    snack = data.SNACK_BY_KEY[key]
    user = db.user(uid)
    coins, mats = snack_cost(user, snack)
    missing = engine.missing_for(db, uid, coins, mats)
    if missing:
        return False, f"You need {', '.join(missing)} to make {snack.emoji} {snack.name}."
    engine.pay(db, uid, coins, mats)
    db.add_item(uid, "snack:" + key, 1)
    db.bump(uid, "snacks_made")
    return True, f"You cooked up {snack.emoji} **{snack.name}**. You have {db.qty(uid, 'snack:' + key)}."


@atomic
def eat_snack(db, uid, key):
    snack = data.SNACK_BY_KEY[key]
    if db.qty(uid, "snack:" + key) <= 0:
        return False, f"You don't have any {snack.name}. Make some first."
    db.add_item(uid, "snack:" + key, -1)
    db.add_buff(uid, snack.buff, snack.amount, snack.minutes * 60)
    db.bump(uid, "snacks_eaten")
    return True, f"{snack.emoji} You ate **{snack.name}**. {snack.blurb}"


# ================================================================ bounty board

def today():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def seconds_to_reset():
    now = datetime.datetime.now(datetime.timezone.utc)
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()


@atomic
def bounties(db, uid):
    """Today's 3 bounties for this player, made the first time they look."""
    day = today()
    rows = db.q("SELECT * FROM bounties WHERE user_id=? AND day=? ORDER BY idx", uid, day)
    if rows:
        return rows
    user = db.user(uid)
    kinds = [k for k in data.BOUNTY_KINDS
             if not (k.key == "collect" and user["sluglings"] == 0)
             and not (k.key == "smelt" and user["layer"] > 5)]
    for idx, kind in enumerate(rng.sample(kinds, data.BOUNTIES_PER_DAY)):
        need = rng.randint(*kind.need)
        if kind.scale_by == "rolls":
            need *= engine.rolls_for(db, user)
        elif kind.scale_by == "layer":
            need *= scale(user)
        reward = int(kind.reward * scale(user) * rng.uniform(0.9, 1.3))
        db.run("INSERT INTO bounties (user_id, day, idx, kind, need, base, reward) VALUES (?, ?, ?, ?, ?, ?, ?)",
               uid, day, idx, kind.key, need, db.stat(uid, kind.stat), reward)
    return db.q("SELECT * FROM bounties WHERE user_id=? AND day=? ORDER BY idx", uid, day)


def bounty_progress(db, row):
    kind = data.BOUNTY_BY_KEY[row["kind"]]
    return min(row["need"], db.stat(row["user_id"], kind.stat) - row["base"])


def bounty_text(row):
    return data.BOUNTY_BY_KEY[row["kind"]].text.format(n=f"{row['need']:,}")


@atomic
def claim_bounty(db, uid, idx):
    row = db.one("SELECT * FROM bounties WHERE user_id=? AND day=? AND idx=?", uid, today(), idx)
    if row is None:
        return False, "That bounty expired. Check the board for today's jobs."
    if row["claimed"]:
        return False, "You already cashed that one in."
    if bounty_progress(db, row) < row["need"]:
        return False, "That job isn't finished yet."
    db.run("UPDATE bounties SET claimed=1 WHERE user_id=? AND day=? AND idx=?", uid, today(), idx)
    db.add_coins(uid, row["reward"])
    db.bump(uid, "bounties_done")
    msg = f"📜 Bounty done: **{bounty_text(row)}**. +**{row['reward']:,}** coins."
    left = db.one("SELECT COUNT(*) AS n FROM bounties WHERE user_id=? AND day=? AND claimed=0", uid, today())["n"]
    if left == 0:
        snack = rng.choice(data.SNACKS)
        db.add_item(uid, "snack:" + snack.key, 1)
        msg += f"\n🎁 **All 3 done!** Bonus: a free {snack.emoji} **{snack.name}**."
    return True, msg


# ================================================================ achievements and titles

def _achieved(key, user, stats, tiers, found):
    s = stats.get
    m = user["max_layer"]
    return {
        "first_ore": user["total_mined"] >= 1,
        "mined_1k": user["total_mined"] >= 1_000,
        "mined_100k": user["total_mined"] >= 100_000,
        "mined_1m": user["total_mined"] >= 1_000_000,
        "moon": m >= 6, "mars": m >= 8, "trippy": m >= 10, "candy": m >= 12, "void": m >= 14, "nothing": m >= 15,
        "legendary": "legendary" in tiers, "mythic": "mythic" in tiers, "celestial": "celestial" in tiers,
        "divine": "divine" in tiers, "contraband": "contraband" in tiers,
        "kingpin": all(o.key in found for o in data.ORES if o.tier == "contraband"),
        "smooth": s("checks_survived", 0) >= 5,
        "busted": s("checks_busted", 0) >= 1,
        "pirate": s("raids_won", 0) >= 10,
        "topgun": s("duels_won", 0) >= 10,
        "meteors": s("meteors", 0) >= 5,
        "tim": s("tim_deals", 0) >= 5,
        "munchies": s("snacks_eaten", 0) >= 20,
        "bounty": s("bounties_done", 0) >= 15,
        "rebirth": user["rebirths"] >= 1,
        "pimped": all(user["ship_" + u.key] >= u.max_level for u in data.SHIP_UPGRADES),
        "million": user["coins"] >= 1_000_000,
        "billion": user["coins"] >= 1_000_000_000,
        "trillion": user["coins"] >= 1_000_000_000_000,
    }[key]


def unlocked(db, uid):
    return {r["key"]: r["at"] for r in db.q("SELECT key, at FROM achievements WHERE user_id=?", uid)}


@atomic
def check_achievements(db, uid):
    """Unlocks anything newly earned. Returns the new Achievements."""
    have = unlocked(db, uid)
    todo = [a for a in data.ACHIEVEMENTS if a.key not in have]
    if not todo:
        return []
    user = db.user(uid)
    stats = db.stats(uid)
    found = db.discovered(uid)
    tiers = {ORE_BY_KEY[k].tier for k in found if k in ORE_BY_KEY}
    new = [a for a in todo if _achieved(a.key, user, stats, tiers, found)]
    for a in new:
        db.run("INSERT OR IGNORE INTO achievements (user_id, key, at) VALUES (?, ?, ?)", uid, a.key, time.time())
    return new


@atomic
def set_title(db, uid, key):
    if key is None:
        db.set(uid, title=None)
        return True, "Title removed."
    a = data.ACH_BY_KEY.get(key)
    if not a or key not in unlocked(db, uid):
        return False, "You haven't unlocked that title yet. See `/achievements`."
    db.set(uid, title=key)
    return True, f"You're now known as **{a.name}**."


def title_of(user):
    a = data.ACH_BY_KEY.get(user["title"] or "")
    return a.name if a else None


# ================================================================ meteors

def next_meteor_time(now=None, today_ok=True):
    """A random moment in the rest of today (UTC) if there's enough of it left, otherwise tomorrow."""
    now = now or time.time()
    day = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day.timestamp()
    if today_ok and start + 86400 - now > 3600:
        return rng.uniform(now + 600, start + 86400 - 300)
    return rng.uniform(start + 86400, start + 2 * 86400 - 300)


@atomic
def mine_meteor(db, uid, event):
    """First METEOR_CLAIMS players get a burst of super-lucky rolls plus coins."""
    expires = float(db.meta(f"meteor:{event}", 0) or 0)
    if time.time() > expires:
        return False, "This meteor already cooled off. Wait for the next one!"
    claimed = db.claims("meteor", event)
    if uid in claimed:
        return False, "You already mined this meteor."
    if len(claimed) >= data.METEOR_CLAIMS:
        return False, "Too slow! This meteor's been picked clean."
    db.claim("meteor", event, uid)
    user = db.user(uid)
    luck = engine.luck_for(db, user).total * data.METEOR_LUCK
    ores = [engine.roll_ore(user["layer"], luck) for _ in range(data.METEOR_ROLLS)]
    best = engine.record_finds(db, uid, ores)
    coins = 300 * scale(user)
    db.add_coins(uid, coins)
    db.bump(uid, "meteors")
    return True, (ores, best, coins, len(claimed) + 1)


# ================================================================ Tweaker Tim

TIM_DEALS = {
    "box": ("📦", "Mystery Box", "Could be anything. Tim won't say. Tim doesn't know."),
    "snack": ("🍪", "Discount Snack", "Half price, no ores needed. Do not ask where it's been."),
    "rock": ("🪨", "Weird Rock", "\"Trust me bro.\" Tiny chance it's this planet's contraband."),
    "fence": ("💼", "Fence Hot Cargo", f"Tim buys your cheapest Legendary+ ore (not contraband) for x{data.TIM_FENCE_MULT} its value. No cops asked."),
}


def new_tim_visit():
    return {"deals": rng.sample(list(TIM_DEALS), 3), "snack": rng.randrange(len(data.SNACKS))}


def save_visit(db, visit_id, visit, expires):
    db.set_meta(f"tim:{visit_id}", json.dumps({**visit, "expires": expires}))


def load_visit(db, visit_id):
    raw = db.meta(f"tim:{visit_id}")
    return json.loads(raw) if raw else None


def tim_price(user, deal, visit):
    s = scale(user)
    if deal == "box":
        return 2_000 * s
    if deal == "snack":
        return data.SNACKS[visit["snack"]].coins * s // 2
    if deal == "rock":
        return 20_000 * s
    return 0


def fence_target(db, uid):
    hot = [o for o in hot_items(db, uid) if o.tier != "contraband"]
    return hot[0] if hot else None


@atomic
def tim_buy(db, uid, visit_id, idx):
    """Returns (ok, message, ore_to_celebrate_or_None)."""
    visit = load_visit(db, visit_id)
    if not visit or time.time() > visit["expires"]:
        return False, "Tim already left. He said something about \"a guy on Mars\".", None
    deal = visit["deals"][idx]
    user = db.user(uid)
    price = tim_price(user, deal, visit)
    if deal == "fence":
        target = fence_target(db, uid)
        if not target:
            return False, "Tim looks through your stuff. \"You got nothing hot, man.\" (Needs a non-contraband Legendary+ ore.)", None
    if user["coins"] < price:
        return False, f"Tim wants **{price:,}** coins for that. You have {user['coins']:,}.", None
    if not db.claim(f"tim{idx}", visit_id, uid):
        return False, "Tim only does that deal once per customer per visit.", None
    db.add_coins(uid, -price)
    db.bump(uid, "tim_deals")
    if deal == "fence":
        coins = int(target.value * data.TIM_FENCE_MULT * engine.sell_mult(user))
        db.add_item(uid, "ore:" + target.key, -1)
        db.add_coins(uid, coins)
        return True, f"💼 Tim pockets your **{target.name}** and hands you **{coins:,}** coins. \"Pleasure.\"", None
    if deal == "snack":
        snack = data.SNACKS[visit["snack"]]
        db.add_item(uid, "snack:" + snack.key, 1)
        return True, f"🍪 You got {snack.emoji} **{snack.name}** for {price:,} coins. It's a little warm.", None
    if deal == "rock":
        if rng.random() < data.TIM_WEIRD_ROCK_CHANCE:
            ore = next((o for o in data.ORES if o.tier == "contraband" and user["layer"] in o.layers), None)
            if ore:
                engine.record_finds(db, uid, [ore])
                return True, f"🪨 You crack the rock open and... **IT'S {ore.name.upper()}!** Tim had no idea.", ore
        return True, f"🪨 It's a rock. Just a rock. Tim is already gone with your {price:,} coins.", None
    # mystery box
    roll = rng.random()
    if roll < 0.40:
        coins = int(price * rng.uniform(0.5, 3.0))
        db.add_coins(uid, coins)
        verdict = "Profit!" if coins > price else "Tim wins this one."
        return True, f"📦 The box is full of loose change: **{coins:,}** coins. {verdict}", None
    if roll < 0.65:
        snack = rng.choice(data.SNACKS)
        db.add_item(uid, "snack:" + snack.key, 1)
        return True, f"📦 Inside: a {snack.emoji} **{snack.name}**. Still sealed. Mostly.", None
    if roll < 0.80:
        db.set(uid, last_roll=0)
        return True, "📦 Inside: a **Lucky Roll recharge**. Your Lucky Roll is ready right now.", None
    luck = engine.luck_for(db, user).total * 25
    ores = [engine.roll_ore(user["layer"], luck) for _ in range(3)]
    best = engine.record_finds(db, uid, ores)
    names = ", ".join(o.name for o in ores)
    return True, f"📦 Inside: three shiny rocks (rolled at x25 luck): **{names}**.", best
