"""Game rules, kept separate from Discord so they're easy to test and tweak."""
import collections
import functools
import math
import random
import time
from dataclasses import dataclass, field

from . import data
from .data import ORE_BY_KEY, PICKAXE_BY_KEY, PICKAXES, LAYERS, PRIZES, PART_BY_KEY, PARTS

rng = random.SystemRandom()


def atomic(fn):
    """Runs a game action as one transaction: it fully happens or not at all."""
    @functools.wraps(fn)
    def wrapper(db, *args, **kwargs):
        with db.tx():
            return fn(db, *args, **kwargs)
    return wrapper


def ores_for_layer(layer):
    """Ores that can drop in a layer, rarest first. The last one is the layer's base ore."""
    pool = [o for o in data.ORES if layer in o.layers]
    return sorted(pool, key=lambda o: o.chance, reverse=True)


_POOLS = {l.num: ores_for_layer(l.num) for l in LAYERS}


def roll_ore(layer, luck):
    """Roblox-RNG style roll: try each ore from rarest to most common.

    Each ore hits with chance luck / N, so a "1 in 10,000,000" ore really is
    1 in 10M at 1x luck and 1 in 1M at 10x luck.
    """
    pool = _POOLS[layer]
    for ore in pool[:-1]:
        # luck only boosts uncommon-and-up, so commons keep their normal mix
        boost = luck if ore.chance >= 50 else 1.0
        if rng.random() * ore.chance < boost:
            return ore
    return pool[-1]


@dataclass
class Luck:
    total: float
    parts: list = field(default_factory=list)   # (label, multiplier)


def luck_for(db, user, base=1.0):
    pick = PICKAXE_BY_KEY[user["pickaxe"]]
    layer = LAYERS[user["layer"] - 1]
    parts = [(pick.name, pick.luck * base), (layer.name, layer.luck)]
    if user["rebirths"]:
        parts.append((f"Rebirth x{user['rebirths']}", 1 + data.REBIRTH_LUCK * user["rebirths"]))
    buff = db.buffs(user["id"]).get("luck")
    if buff:
        parts.append(("Lucky Roll", buff[0]))
    ev = db.event()
    if ev:
        parts.append((ev["label"] or "Server Event", ev["luck"]))
    total = 1.0
    for _, m in parts:
        total *= m
    return Luck(total, parts)


def cooldown_for(db, user):
    cd = PICKAXE_BY_KEY[user["pickaxe"]].cooldown
    haste = db.buffs(user["id"]).get("haste")
    if haste:
        cd *= haste[0]
    return cd


def rolls_for(db, user):
    r = PICKAXE_BY_KEY[user["pickaxe"]].rolls
    dbl = db.buffs(user["id"]).get("rolls")
    if dbl:
        r = int(r * dbl[0])
    return r


def sell_mult(user):
    return 1 + data.REBIRTH_SELL * user["rebirths"]


@dataclass
class SwingResult:
    ores: list            # list of Ore
    best: object
    luck: Luck
    autosold: int = 0     # coins from auto-sell


@atomic
def record_finds(db, uid, ores):
    counts = {}
    for o in ores:
        counts[o.key] = counts.get(o.key, 0) + 1
    for key, n in counts.items():
        db.add_item(uid, "ore:" + key, n)
        db.discover(uid, key, n)
    best = max(ores, key=lambda o: o.chance)
    user = db.user(uid)
    prev = ORE_BY_KEY.get(user["best_ore"]) if user["best_ore"] else None
    fields = {"total_mined": user["total_mined"] + len(ores)}
    if prev is None or best.chance > prev.chance:
        fields["best_ore"] = best.key
    db.set(uid, **fields)
    for o in ores:
        if o.chance >= 10_000:
            db.run("INSERT INTO finds (user_id, ore, at) VALUES (?, ?, ?)", uid, o.key, time.time())
    return best


@atomic
def swing(db, uid):
    user = db.user(uid)
    luck = luck_for(db, user)
    ores = [roll_ore(user["layer"], luck.total) for _ in range(rolls_for(db, user))]
    best = record_finds(db, uid, ores)
    coins = autosell(db, uid)
    return SwingResult(ores, best, luck, coins)


def autosell(db, uid):
    """If the player has auto-sell on, sells their common/uncommon ores. Returns coins made."""
    if not db.user(uid)["autosell"]:
        return 0
    return sell_all(db, uid, max_tier=data.AUTOSELL_MAX_TIER)[1]


@atomic
def toggle_autosell(db, uid):
    on = not db.user(uid)["autosell"]
    db.set(uid, autosell=int(on))
    return on


def _progress(db, uid, coins, materials):
    """0..1 of how close you are to affording coins + materials (each requirement weighs the same)."""
    user = db.user(uid)
    parts = []
    if coins:
        parts.append(min(1.0, user["coins"] / coins))
    for item, qty in materials.items():
        parts.append(min(1.0, db.qty(uid, item) / qty))
    return sum(parts) / len(parts) if parts else 1.0


def goals(db, uid):
    """[(label, fraction)] for the next pickaxe and the next layer/planet."""
    user = db.user(uid)
    out = []
    nxt = next_pickaxe(user)
    if nxt:
        out.append((f"⛏️ {nxt.name}", _progress(db, uid, nxt.price, nxt.materials)))
    if user["max_layer"] < len(LAYERS):
        L = LAYERS[user["max_layer"]]
        icon = "🌀" if is_new_realm(L) else "🚀" if is_new_planet(L) else "🕳️"
        name = L.planet_info.name if is_new_planet(L) else L.name
        out.append((f"{icon} {name}", _progress(db, uid, L.cost, L.materials)))
    return out


def bar(frac, width=10):
    full = int(round(frac * width))
    return "▰" * full + "▱" * (width - full)


@atomic
def claim_daily(db, uid):
    user = db.user(uid)
    now = time.time()
    since = now - user["daily_last"]
    if since < data.DAILY_COOLDOWN:
        return False, f"Your next daily is ready in {fmt_time(data.DAILY_COOLDOWN - since)}."
    streak = user["daily_streak"] + 1 if since <= data.DAILY_GRACE else 1
    mult = 1 + min(streak - 1, 10) * 0.1
    coins = int(500 * (3 ** (user["max_layer"] - 1)) * mult)
    db.add_coins(uid, coins)
    db.set(uid, daily_last=now, daily_streak=streak)
    msg = f"📅 Day **{streak}** streak! You got **{coins:,} coins** (x{mult:.1f} streak bonus)."
    if streak % 7 == 0:
        db.set(uid, last_roll=0)
        db.add_buff(uid, "luck", 2, 30 * 60)
        msg += "\n🎁 **Weekly bonus:** your Lucky Roll is recharged and you have **x2 luck for 30 minutes**!"
    elif since > data.DAILY_GRACE and user["daily_streak"]:
        msg += f"\n(Your old {user['daily_streak']}-day streak ran out. Come back within 48h to keep it going.)"
    return True, msg


@atomic
def sell_all(db, uid, keep_rare=True, max_tier=None):
    """Sells ores. Epic+ ores and bars are kept unless keep_rare is False.
    max_tier (used by auto-sell) only sells ores up to that tier and never bars."""
    user = db.user(uid)
    mult = sell_mult(user)
    total = 0
    sold = 0
    cap = data.TIER_ORDER.index(max_tier) if max_tier else None
    for item, qty in db.items(uid).items():
        kind, key = item.split(":", 1)
        if kind == "ore":
            ore = ORE_BY_KEY[key]
            if keep_rare and data.TIER_ORDER.index(ore.tier) >= data.TIER_ORDER.index("epic"):
                continue
            if cap is not None and data.TIER_ORDER.index(ore.tier) > cap:
                continue
            # keep materials needed for the next pickaxe
            keep = next_pick_needs(user).get(item, 0)
            n = qty - keep
            if n <= 0:
                continue
            total += ore.value * n
        elif kind == "bar":
            if keep_rare or cap is not None:
                continue                        # bars are crafting material, only "sell everything" sells them
            keep = next_pick_needs(user).get(item, 0)
            n = qty - keep
            if n <= 0:
                continue
            total += data.bar_value(key) * n
        else:
            continue
        db.add_item(uid, item, -n)
        sold += n
    total = int(total * mult)
    db.run("UPDATE users SET coins = coins + ?, total_earned = total_earned + ? WHERE id=?", total, total, uid)
    return sold, total


def next_pickaxe(user):
    idx = [p.key for p in PICKAXES].index(user["pickaxe"])
    return PICKAXES[idx + 1] if idx + 1 < len(PICKAXES) else None


def next_pick_needs(user):
    """Items Sell won't touch: what the next pickaxe and next planet need, plus the ores for those parts."""
    needs = collections.Counter()
    nxt = next_pickaxe(user)
    if nxt:
        needs.update(nxt.materials)
    if user["max_layer"] < len(LAYERS):
        for item, qty in LAYERS[user["max_layer"]].materials.items():
            needs[item] += qty
            if item.startswith("part:"):
                for sub, q in PART_BY_KEY[item[5:]].materials.items():
                    needs[sub] += q * qty
    return dict(needs)


def item_name(item):
    kind, key = item.split(":", 1)
    if kind == "ore":
        return ORE_BY_KEY[key].name
    if kind == "part":
        return PART_BY_KEY[key].name
    return data.BARS[key][0]


def missing_for(db, uid, coins, materials):
    """What's still needed for a coin price plus materials, as a list of readable strings."""
    user = db.user(uid)
    missing = []
    if user["coins"] < coins:
        missing.append(f"{coins - user['coins']:,} more coins")
    for item, qty in materials.items():
        have = db.qty(uid, item)
        if have < qty:
            missing.append(f"{qty - have}x {item_name(item)}")
    return missing


def pay(db, uid, coins, materials):
    db.add_coins(uid, -coins)
    for item, qty in materials.items():
        db.add_item(uid, item, -qty)


@atomic
def craft_part(db, uid, key):
    part = PART_BY_KEY[key]
    missing = missing_for(db, uid, part.price, part.materials)
    if missing:
        return False, f"You need {', '.join(missing)} for **{part.name}**."
    pay(db, uid, part.price, part.materials)
    db.add_item(uid, "part:" + key, 1)
    return True, f"You built **{part.name}**. You have {db.qty(uid, 'part:' + key)} now."


@atomic
def buy_pickaxe(db, uid):
    user = db.user(uid)
    nxt = next_pickaxe(user)
    if not nxt:
        return False, "You already own the best pickaxe in the game."
    missing = missing_for(db, uid, nxt.price, nxt.materials)
    if missing:
        return False, f"You need {', '.join(missing)} for the **{nxt.name}**."
    pay(db, uid, nxt.price, nxt.materials)
    db.set(uid, pickaxe=nxt.key)
    return True, f"You crafted the **{nxt.name}**! Luck x{nxt.luck}, {nxt.cooldown}s cooldown, {nxt.rolls} ore per swing."


def layer_label(layer):
    return f"{layer.planet_info.emoji} {layer.planet_info.name} · {layer.name}"


def is_new_planet(layer):
    return layer.num > 1 and LAYERS[layer.num - 2].planet != layer.planet


def is_new_realm(layer):
    return layer.num > 1 and LAYERS[layer.num - 2].realm_info.key != layer.realm_info.key


@atomic
def unlock_next(db, uid):
    """Dig, launch or warp to the next locked layer, paying coins and materials."""
    user = db.user(uid)
    if user["max_layer"] >= len(LAYERS):
        return False, "You've reached the end of the universe. There's nowhere left to go."
    nxt = LAYERS[user["max_layer"]]
    verb = "warp" if is_new_realm(nxt) else "fly" if is_new_planet(nxt) else "dig"
    missing = missing_for(db, uid, nxt.cost, nxt.materials)
    if missing:
        return False, f"To {verb} to **{layer_label(nxt)}** you still need {', '.join(missing)}."
    pay(db, uid, nxt.cost, nxt.materials)
    db.set(uid, layer=nxt.num, max_layer=nxt.num)
    if verb == "warp":
        head = f"🌀 You warped into **{nxt.realm_info.name}** and landed on **{nxt.planet_info.name}**!"
    elif verb == "fly":
        head = f"🚀 You launched to **{nxt.planet_info.name}**!"
    else:
        head = f"You dug through to **{nxt.name}**!"
    return True, f"{head} {nxt.blurb}"


@atomic
def travel(db, uid, num):
    user = db.user(uid)
    if not 1 <= num <= user["max_layer"]:
        return False, "You haven't unlocked that place yet."
    db.set(uid, layer=num)
    return True, f"You travelled to **{layer_label(LAYERS[num - 1])}**."


@atomic
def descend(db, uid):
    user = db.user(uid)
    if user["layer"] < user["max_layer"]:
        return travel(db, uid, user["layer"] + 1)
    return unlock_next(db, uid)


@atomic
def ascend(db, uid):
    user = db.user(uid)
    if user["layer"] <= 1:
        return False, "You're already at the surface of Slug Earth."
    return travel(db, uid, user["layer"] - 1)


@atomic
def smelt_all(db, uid):
    user = db.user(uid)
    made = {}
    fee = 0
    for bar, (name, cost) in data.BARS.items():
        have = db.qty(uid, "ore:" + bar)
        n = have // data.ORE_PER_BAR
        if n <= 0:
            continue
        coins_left = user["coins"] - fee
        n = min(n, coins_left // cost) if cost else n
        if n <= 0:
            continue
        db.add_item(uid, "ore:" + bar, -n * data.ORE_PER_BAR)
        db.add_item(uid, "bar:" + bar, n)
        fee += n * cost
        made[name] = n
    db.add_coins(uid, -fee)
    return made, fee


def slugling_cost(owned):
    return int(data.SLUGLING_BASE_COST * data.SLUGLING_COST_GROWTH ** owned)


@atomic
def hire_slugling(db, uid):
    user = db.user(uid)
    collect_sluglings(db, uid)   # bank what the current crew dug first
    cost = slugling_cost(user["sluglings"])
    if user["coins"] < cost:
        return False, f"A slugling costs {cost:,} coins. You have {user['coins']:,}."
    db.add_coins(uid, -cost)
    db.set(uid, sluglings=user["sluglings"] + 1)
    return True, f"You hired slugling #{user['sluglings'] + 1}! It digs 1 ore a minute, even while you're offline."


@atomic
def collect_sluglings(db, uid):
    user = db.user(uid)
    now = time.time()
    elapsed = min(now - user["last_collect"], data.COLLECT_CAP_HOURS * 3600)
    db.set(uid, last_collect=now)
    if user["sluglings"] <= 0 or elapsed < data.SLUGLING_SECONDS_PER_ROLL:
        return [], elapsed
    n = int(elapsed // data.SLUGLING_SECONDS_PER_ROLL) * user["sluglings"]
    n = min(n, 50_000)
    luck = luck_for(db, user, base=data.SLUGLING_LUCK).total
    ores = [roll_ore(user["layer"], luck) for _ in range(n)]
    record_finds(db, uid, ores)
    return ores, elapsed


def lucky_roll_ready(user):
    return max(0, user["last_roll"] + data.LUCKY_ROLL_COOLDOWN - time.time())


def pick_prize():
    return rng.choices(PRIZES, weights=[p.weight for p in PRIZES])[0]


@atomic
def apply_prize(db, uid, prize):
    user = db.user(uid)
    db.set(uid, last_roll=time.time())
    if prize.kind == "coins":
        amount = int(150 * (3 ** (user["max_layer"] - 1)) * (1 + 0.5 * user["sluglings"] ** 0.5) * rng.uniform(0.7, 1.6))
        db.add_coins(uid, amount)
        return f"You found a pouch with **{amount:,} coins**!"
    db.add_buff(uid, prize.kind, prize.amount, prize.minutes * 60)
    return f"**{prize.name}** for {prize.minutes} minutes!"


def can_rebirth(user):
    return user["max_layer"] >= data.REBIRTH_LAYER and user["coins"] >= data.REBIRTH_COST


@atomic
def rebirth(db, uid):
    user = db.user(uid)
    if not can_rebirth(user):
        return False, (f"Rebirth needs **{LAYERS[data.REBIRTH_LAYER - 1].name}** unlocked and "
                       f"{data.REBIRTH_COST:,} coins.")
    keep = {k: v for k, v in db.items(uid).items()
            if k.startswith("ore:") and data.TIER_ORDER.index(ORE_BY_KEY[k[4:]].tier) >= data.TIER_ORDER.index("legendary")}
    db.run("DELETE FROM items WHERE user_id=?", uid)
    for k, v in keep.items():
        db.add_item(uid, k, v)
    db.run("DELETE FROM buffs WHERE user_id=?", uid)
    db.set(uid, coins=0, pickaxe="wood", layer=1, max_layer=1, sluglings=0,
           rebirths=user["rebirths"] + 1, last_collect=time.time())
    n = user["rebirths"] + 1
    return True, (f"**You burned the lab down. Rebirth {n}!** You now have +{int(data.REBIRTH_LUCK * n * 100)}% luck and "
                  f"+{int(data.REBIRTH_SELL * n * 100)}% sell value forever. Your legendary-and-up ores came with you.")


def fmt_chance(n):
    return f"1 in {n:,}"


def fmt_time(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    return f"{sec // 3600}h {(sec % 3600) // 60}m"
