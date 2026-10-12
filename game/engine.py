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
fast_rng = random.Random()     # for bulk rolls (slugling collects): SystemRandom is ~10x slower per draw


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


def layer_chance(ore, layer):
    """1 in N for this ore on this layer: Uncommon to Epic ores get easier the deeper below their first layer you are."""
    first = min(ore.layers)
    if ore.tier in data.DEPTH_EASE_TIERS and layer > first:
        return max(2.0, ore.chance / data.DEPTH_EASE ** (layer - first))
    return ore.chance


_POOLS = {l.num: ores_for_layer(l.num) for l in LAYERS}
# what roll_ore actually walks: (ore, 1-in-N on that layer, luck applies?), rarest first, the base ore last
_ROLLS = {n: sorted([(o, layer_chance(o, n), o.chance >= 50) for o in pool[:-1]], key=lambda t: -t[1]) + [(pool[-1], 1, False)]
          for n, pool in _POOLS.items()}


def _true_odds():
    """What players actually see for common ores. Rarer ores get checked first, so a common's real
    odds are lower than its raw number, and a layer's base ore (raw "1 in 1") is really the leftover.
    Uses each ore's first layer at 1x luck. Rare-and-up ores keep their raw number (it's already right)."""
    out = {}
    for ore in data.ORES:
        if ore.chance >= 50:
            continue
        pool = _POOLS[min(ore.layers)]
        miss = 1.0
        for o in pool[:-1]:
            if o is ore:
                break
            miss *= 1 - 1 / o.chance
        p = miss if ore is pool[-1] else miss / ore.chance
        out[ore.key] = 1 / p
    return out


SHOWN_ODDS = _true_odds()


def roll_ore(layer, luck, rand=None):
    """Roblox-RNG style roll: try each ore from rarest to most common.

    Each ore hits with chance luck / N, so a "1 in 10,000,000" ore really is
    1 in 10M at 1x luck and 1 in 1M at 10x luck.
    """
    pool = _ROLLS[layer]
    rand = (rand or rng).random
    for ore, chance, lucky in pool[:-1]:
        # luck only boosts uncommon-and-up, so commons keep their normal mix
        if rand() * chance < (luck if lucky else 1.0):
            return ore
    return pool[-1][0]


# ---------------------------------------------------------------- v0.7.0 weather and enchants

def weather_window(t=None):
    return int((t or time.time()) // (data.WEATHER_HOURS * 3600))


def weather_for(planet, t=None):
    """This planet's weather right now. The same for everyone: it's seeded by the planet and the time window."""
    r = random.Random(f"{planet}:{weather_window(t)}")
    return r.choices(data.WEATHERS, weights=[w.weight for w in data.WEATHERS])[0]


def weather_ends(t=None):
    return (weather_window(t) + 1) * data.WEATHER_HOURS * 3600


def user_weather(user):
    return weather_for(LAYERS[user["layer"] - 1].planet)


def enchants(db, uid):
    """{slot: row} of the enchants on your current pickaxe."""
    return {r["slot"]: r for r in db.q("SELECT * FROM enchants WHERE user_id=? ORDER BY slot", uid)}


def enchant_value(ench_key, level):
    return data.ENCHANT_BY_KEY[ench_key].levels[level - 1]


def enchant_bonus(db, uid, kind):
    """Combined effect of one enchant kind across your slots (multipliers multiply, Echo chances add)."""
    rows = [r for r in enchants(db, uid).values() if r["ench"] == kind]
    if kind == "echo":
        return sum(enchant_value(kind, r["level"]) for r in rows)
    out = 1.0
    for r in rows:
        out *= enchant_value(kind, r["level"])
    return out


def enchant_text(ench_key, level):
    e = data.ENCHANT_BY_KEY[ench_key]
    v = enchant_value(ench_key, level)
    return f"{e.emoji} **{e.name} {'I' * level if level < 4 else level}**: " + e.text.format(v=f"{v:g}", p=f"{int(v * 100)}%")


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
    buffs = db.buffs(user["id"])
    if buffs.get("luck"):
        parts.append(("Lucky Roll", buffs["luck"][0]))
    if buffs.get("snack_luck"):
        parts.append(("Space Snack", buffs["snack_luck"][0]))
    ev = db.event()
    if ev:
        parts.append((ev["label"] or "Server Event", ev["luck"]))
    w = weather_for(layer.planet)
    if w.luck != 1.0:
        parts.append((w.name, w.luck))
    fortune = enchant_bonus(db, user["id"], "fortune")
    if fortune != 1.0:
        parts.append(("Fortune enchant", fortune))
    total = 1.0
    for _, m in parts:
        total *= m
    return Luck(total, parts)


def cooldown_for(db, user):
    return PICKAXE_BY_KEY[user["pickaxe"]].cooldown


def rolls_for(db, user, buffs=None):
    """Ores per Mine press: the pickaxe's rolls, x2 with Double Dig, x2 again with Echo Swing."""
    r = PICKAXE_BY_KEY[user["pickaxe"]].rolls
    buffs = db.buffs(user["id"]) if buffs is None else buffs
    for kind in ("rolls", "echo"):
        if buffs.get(kind):
            r = int(r * buffs[kind][0])
    return r


def sell_mult(user, db=None):
    """Rebirth sell bonus, plus Midas Touch from the Lucky Roll when db is given."""
    m = 1 + data.REBIRTH_SELL * user["rebirths"]
    if db is not None:
        midas = db.buffs(user["id"]).get("sell")
        if midas:
            m *= midas[0]
        m *= user_weather(user).sell * enchant_bonus(db, user["id"], "midas")
    return m


def mutation_mult(db, uid):
    b = db.buffs(uid).get("mutluck")
    m = b[0] if b else 1.0
    return m * user_weather(db.user(uid)).mutations * enchant_bonus(db, uid, "mutagen")


def find_score(ore, mut=None):
    """How rare a find is, as the N in 1-in-N. A mutated ore counts the ore's odds times the mutation's."""
    n = SHOWN_ODDS.get(ore.key, ore.chance)
    return n * mut.chance if mut else n


def find_label(key):
    """'ore:gold' or 'mut:shiny:gold' -> (Ore, Mutation or None)"""
    if key.startswith("mut:"):
        m, o = split_mut(key[4:])
        return o, m
    return ORE_BY_KEY[key.split(":", 1)[-1]], None


@dataclass
class SwingResult:
    ores: list            # list of Ore
    best: object
    luck: Luck
    autosold: int = 0     # coins from auto-sell
    mutations: list = field(default_factory=list)   # [(ore, mutation)]
    echoed: bool = False  # the Echo enchant doubled this swing
    crack: bool = False   # found an Enchanted Crack Rock
    minigame: bool = False  # time for a mining minigame


def roll_mutation(mult=1.0, rand=None):
    """Each found ore has a small chance to be mutated (rarest mutation checked first)."""
    rand = (rand or rng).random
    for m in data.MUTATIONS:
        if rand() * m.chance < mult:
            return m
    return None


def mut_item(ore, mut):
    return f"mut:{mut.key}:{ore.key}"


def split_mut(key):
    """'shiny:coal' -> (Mutation, Ore)"""
    m, o = key.split(":", 1)
    return data.MUT_BY_KEY[m], ORE_BY_KEY[o]


def record_finds(db, uid, ores, mutations=None, rand=None):
    """Adds found ores to the bag. Mutated ones are stored separately; they're appended to `mutations`
    as (ore, mutation) if a list is passed in."""
    counts = {}
    for o in ores:
        counts[o.key] = counts.get(o.key, 0) + 1
    for key, n in counts.items():
        db.discover(uid, key, n)
    found_muts = []
    mm = mutation_mult(db, uid)
    for o in ores:
        m = roll_mutation(mm, rand)
        if m:
            found_muts.append((o, m))
            counts[o.key] -= 1
            db.add_item(uid, mut_item(o, m), 1)
            db.bump(uid, "mut_" + m.key)
    if found_muts:
        db.bump(uid, "mutations", len(found_muts))
        if mutations is not None:
            mutations.extend(found_muts)
    for key, n in counts.items():
        if n:
            db.add_item(uid, "ore:" + key, n)
    best = max(ores, key=lambda o: o.chance)
    user = db.user(uid)
    prev = ORE_BY_KEY.get(user["best_ore"]) if user["best_ore"] else None
    fields = {"total_mined": user["total_mined"] + len(ores)}
    if prev is None or best.chance > prev.chance:
        fields["best_ore"] = best.key
    # rarest find counting mutations (the leaderboard and /profile use this one)
    top_key, top = "ore:" + best.key, find_score(best)
    for o, m in found_muts:
        if find_score(o, m) > top:
            top_key, top = "mut:" + mut_item(o, m)[4:], find_score(o, m)
    if top > (user["best_score"] or 0):
        fields["best_find"], fields["best_score"] = top_key, top
    db.set(uid, **fields)
    rare = data.TIER_ORDER.index("rare")
    db.bump(uid, "found_rare", sum(1 for o in ores if data.TIER_ORDER.index(o.tier) >= rare))
    for o in ores:
        if o.chance >= 10_000:
            db.run("INSERT INTO finds (user_id, ore, at) VALUES (?, ?, ?)", uid, o.key, time.time())
    return best


def crack_chance(db, user):
    """1 in N chance per Mine press of an Enchanted Crack Rock."""
    return data.CRACK_ROCK_CHANCE / (user_weather(user).crack * enchant_bonus(db, user["id"], "prospector"))


@atomic
def swing(db, uid):
    user = db.user(uid)
    luck = luck_for(db, user)
    n = rolls_for(db, user)
    echoed = rng.random() < enchant_bonus(db, uid, "echo")
    if echoed:
        n *= 2
    ores = [roll_ore(user["layer"], luck.total) for _ in range(n)]
    muts = []
    best = record_finds(db, uid, ores, muts)
    db.bump(uid, "mined", len(ores))
    db.bump(uid, "swings")
    coins = autosell(db, uid)
    crack = rng.random() * crack_chance(db, user) < 1
    if crack:
        db.add_item(uid, data.CRACK_ROCK, 1)
        db.bump(uid, "crack_rocks")
    count = user["mg_count"] + 1
    game = count >= data.MINIGAME_PITY or rng.random() * data.MINIGAME_CHANCE < 1
    db.set(uid, mg_count=0 if game else count)
    return SwingResult(ores, best, luck, coins, muts, echoed, crack, game)


def autosell(db, uid):
    """If the player has auto-sell on, sells their common/uncommon ores. Returns coins made."""
    if not db.user(uid)["autosell"]:
        return 0
    return sell_all(db, uid, max_tier=data.AUTOSELL_MAX_TIER)[1]


@atomic
def toggle_locks(db, uid, items):
    """Flips the lock on each item. Returns (locked names, unlocked names)."""
    have = db.locked(uid)
    on, off = [], []
    for item in items:
        if item.split(":", 1)[0] not in ("ore", "bar", "part", "snack", "mut"):
            continue
        try:
            name = item_name(item)
        except KeyError:
            continue
        lock = item not in have
        db.set_lock(uid, item, lock)
        (on if lock else off).append(name)
    return on, off


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


def short_num(n):
    """1234 -> 1.2k, 5_600_000 -> 5.6M, for button labels."""
    for div, suf in ((10 ** 12, "T"), (10 ** 9, "B"), (10 ** 6, "M"), (10 ** 3, "k")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suf
    return f"{n:,}"


def bar(frac, width=10):
    full = int(round(frac * width))
    return "▰" * full + "▱" * (width - full)


def calendar_state(user, now=None):
    """(next calendar day 1-7, seconds until it can be claimed, missed a day?)"""
    now = now or time.time()
    since = now - user["daily_last"]
    last = user["cal_day"]
    missed = bool(user["daily_last"]) and since > data.DAILY_GRACE
    if not user["daily_last"]:
        nxt = 1
    elif missed:
        nxt = max(1, last)            # missing a day only knocks you back one step (you redo that day)
    else:
        nxt = last % 7 + 1
    return nxt, max(0, data.DAILY_COOLDOWN - since), missed


@atomic
def claim_daily(db, uid):
    """The 7-day calendar. Returns (ok, message)."""
    user = db.user(uid)
    now = time.time()
    day, wait, missed = calendar_state(user, now)
    if wait > 0:
        return False, f"Your next daily (day {day}) is ready in {fmt_time(wait)}."
    kind, amount = data.CALENDAR[day - 1]
    base = int(500 * data.scale(user["max_layer"]))
    if kind == "coins":
        coins = int(base * amount)
        db.add_coins(uid, coins)
        got = f"**{coins:,} coins**"
    elif kind == "snack":
        snack = rng.choice(data.SNACKS)
        db.add_item(uid, "snack:" + snack.key, int(amount))
        got = f"a {snack.emoji} **{snack.name}**"
    elif kind == "roll":
        db.set(uid, last_roll=0)
        got = "a **Lucky Roll recharge** (it's ready now)"
    else:
        db.add_item(uid, kind, int(amount))
        c = data.CRATE_BY_KEY[kind.split(":", 1)[1]]
        got = f"a {c.emoji} **{c.name}**"
        db.bump(uid, "calendar_weeks")
    streak = user["daily_streak"] + 1 if not missed else 1
    db.set(uid, daily_last=now, daily_streak=streak, cal_day=day)
    msg = f"📅 **Day {day} of 7:** you got {got}!"
    if missed:
        msg += "\n(You missed a day, so you're redoing this day. Come back within 48h to keep moving.)"
    elif day == 7:
        msg += "\n🎉 You finished the week! The calendar starts over tomorrow."
    return True, msg


@atomic
def sell_all(db, uid, keep_rare=True, max_tier=None):
    """Sells ores. Epic+ ores and bars are kept unless keep_rare is False.
    max_tier (used by auto-sell) only sells ores up to that tier and never bars."""
    user = db.user(uid)
    mult = sell_mult(user, db)
    total = 0
    sold = 0
    cap = data.TIER_ORDER.index(max_tier) if max_tier else None
    locked = db.locked(uid)
    for item, qty in db.items(uid).items():
        if item in locked:
            continue                            # locked items are never sold
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
        elif kind == "mut":
            if keep_rare or cap is not None:
                continue                        # mutations are collectibles: only "sell everything" sells them
            m, ore = split_mut(key)
            n = qty
            total += ore.value * m.mult * n
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
    db.bump(uid, "sold_coins", total)
    return sold, total


@atomic
def sell_items(db, uid, items):
    """Sells every one of the picked items (the Inventory sell picker). Locked items are skipped,
    and so is whatever your next pickaxe or planet still needs. Returns (sold, coins, skipped names)."""
    user = db.user(uid)
    mult = sell_mult(user, db)
    locked = db.locked(uid)
    needs = next_pick_needs(user)
    total = sold = 0
    skipped = []
    for item in dict.fromkeys(items):
        kind, _, key = item.partition(":")
        qty = db.qty(uid, item)
        if qty <= 0 or kind not in ("ore", "mut", "bar"):
            continue
        try:
            name = item_name(item)
        except KeyError:
            continue
        if item in locked:
            skipped.append(f"🔒 {name}")
            continue
        n = qty - needs.get(item, 0)
        if n <= 0:
            skipped.append(f"{name} (needed for your next upgrade)")
            continue
        if kind == "ore":
            each = ORE_BY_KEY[key].value
        elif kind == "mut":
            m, ore = split_mut(key)
            each = ore.value * m.mult
        else:
            each = data.bar_value(key)
        db.add_item(uid, item, -n)
        total += each * n
        sold += n
    total = int(total * mult)
    db.run("UPDATE users SET coins = coins + ?, total_earned = total_earned + ? WHERE id=?", total, total, uid)
    db.bump(uid, "sold_coins", total)
    return sold, total, skipped


def item_value(item):
    kind, _, key = item.partition(":")
    if kind == "ore":
        return ORE_BY_KEY[key].value
    if kind == "mut":
        m, ore = split_mut(key)
        return ore.value * m.mult
    if kind == "bar":
        return data.bar_value(key)
    return 0


def backfill_best_finds(db):
    """v0.6.0: rarest find now counts mutations. Works it out once for existing players from their best ore
    and the mutated ores they still have."""
    if db.meta("best_find_backfilled"):
        return
    with db.tx():
        for u in db.q("SELECT id, best_ore FROM users"):
            cands = []
            if u["best_ore"] in ORE_BY_KEY:
                o = ORE_BY_KEY[u["best_ore"]]
                cands.append((find_score(o), "ore:" + o.key))
            for item in db.items(u["id"], "mut:"):
                try:
                    m, o = split_mut(item[4:])
                except KeyError:
                    continue
                cands.append((find_score(o, m), item))
            if cands:
                score, key = max(cands)
                db.set(u["id"], best_find=key, best_score=score)
        db.set_meta("best_find_backfilled", 1)


def retire_old_snacks(db):
    """Swaps snacks that no longer exist for their replacement, so nobody's bag has a broken item."""
    for old, new in data.RETIRED_SNACKS.items():
        rows = db.q("SELECT user_id, qty FROM items WHERE item=? AND qty>0", "snack:" + old)
        if not rows:
            continue
        with db.tx():
            for r in rows:
                db.add_item(r["user_id"], "snack:" + new, r["qty"])
            db.run("DELETE FROM items WHERE item=?", "snack:" + old)
            db.run("DELETE FROM locks WHERE item=?", "snack:" + old)


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
    if kind == "snack":
        return data.SNACK_BY_KEY[key].name
    if kind == "mut":
        m, ore = split_mut(key)
        return f"{m.name} {ore.name}"
    if kind == "crate":
        return data.CRATE_BY_KEY[key].name
    if kind == "rock":
        if key != "crack":
            raise KeyError(key)
        return "Enchanted Crack Rock"
    if kind == "tag":
        return data.TAG_BY_KEY[key].name
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
    db.run("DELETE FROM enchants WHERE user_id=?", uid)      # enchants don't carry over to the next pickaxe
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
def smelt_all(db, uid, only=None, limit=None):
    """Smelts every bar ore you have enough of. only=[ore keys] smelts just those (even if locked);
    otherwise locked ores are skipped. limit caps how many bars of each type get made."""
    user = db.user(uid)
    made = {}
    fee = 0
    locked = db.locked(uid)
    for bar, (name, cost) in data.BARS.items():
        if only is not None and bar not in only:
            continue
        if only is None and "ore:" + bar in locked:
            continue
        have = db.qty(uid, "ore:" + bar)
        n = have // data.ORE_PER_BAR
        if limit is not None:
            n = min(n, limit)
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
    db.bump(uid, "smelted", sum(made.values()))
    return made, fee


def collect_cap(user):
    return data.SLUG_PACKS[min(user["slug_pack"], len(data.SLUG_PACKS) - 1)]


def slugs_waiting(db, user, now=None):
    """(ores dug since the last collect, is the backpack full?)"""
    now = now or time.time()
    since = now - user["last_collect"]
    cap = collect_cap(user) * 3600
    return slug_output(db, user, min(since, cap)), since >= cap


def slug_output(db, user, elapsed):
    """Ores the crew digs in `elapsed` seconds: 1 a minute each, x their rank's speed and the Whisperer enchant."""
    per = data.SLUG_RANKS[user["slug_rank"]][2] * enchant_bonus(db, user["id"], "whisperer")
    return int(elapsed // data.SLUGLING_SECONDS_PER_ROLL * user["sluglings"] * per)


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
    cap = collect_cap(user) * 3600
    elapsed = min(now - user["last_collect"], cap)
    if user["sluglings"] <= 0:
        db.set(uid, last_collect=now)
        return [], elapsed, []
    if elapsed < data.SLUGLING_SECONDS_PER_ROLL:
        return [], elapsed, []          # too early: keep the clock running instead of throwing the time away
    # the unfinished minute carries over to the next collect (unless the backpack was already full)
    db.set(uid, last_collect=now if now - user["last_collect"] >= cap else now - elapsed % data.SLUGLING_SECONDS_PER_ROLL)
    n = slug_output(db, user, elapsed)
    n = min(n, 100_000)
    luck = luck_for(db, user, base=data.SLUG_RANKS[user["slug_rank"]][3]).total
    ores = [roll_ore(user["layer"], luck, fast_rng) for _ in range(n)]
    muts = []
    record_finds(db, uid, ores, muts, fast_rng if n > 100 else None)
    db.bump(uid, "collected")
    return ores, elapsed, muts


def lucky_roll_ready(user):
    return max(0, user["last_roll"] + data.LUCKY_ROLL_COOLDOWN - time.time())


def pick_prize():
    return rng.choices(PRIZES, weights=[p.weight for p in PRIZES])[0]


@atomic
def apply_prize(db, uid, prize):
    user = db.user(uid)
    db.set(uid, last_roll=time.time())
    db.bump(uid, "lucky_rolls")
    if prize.kind == "coins":
        amount = int(150 * (3 ** (user["max_layer"] - 1)) * (1 + 0.5 * user["sluglings"] ** 0.5) * rng.uniform(0.7, 1.6))
        db.add_coins(uid, amount)
        return f"You found a pouch with **{amount:,} coins**!"
    db.add_buff(uid, prize.kind, prize.amount, prize.minutes * 60)
    return f"**{prize.name.replace('  ', ': ')}** for {prize.minutes} minutes!"


def can_rebirth(user):
    return user["max_layer"] >= data.REBIRTH_LAYER and user["coins"] >= data.REBIRTH_COST


@atomic
def rebirth(db, uid):
    user = db.user(uid)
    if not can_rebirth(user):
        return False, (f"Rebirth needs **{LAYERS[data.REBIRTH_LAYER - 1].name}** unlocked and "
                       f"{data.REBIRTH_COST:,} coins.")
    keep = {k: v for k, v in db.items(uid).items()
            if k.startswith("mut:") or
            (k.startswith("ore:") and data.TIER_ORDER.index(ORE_BY_KEY[k[4:]].tier) >= data.TIER_ORDER.index("legendary"))}
    db.run("DELETE FROM items WHERE user_id=?", uid)
    for k, v in keep.items():
        db.add_item(uid, k, v)
    db.run("DELETE FROM buffs WHERE user_id=?", uid)
    db.run("DELETE FROM enchants WHERE user_id=?", uid)
    db.set(uid, coins=0, pickaxe="wood", layer=1, max_layer=1, sluglings=0,
           rebirths=user["rebirths"] + 1, last_collect=time.time())
    n = user["rebirths"] + 1
    return True, (f"**You burned the lab down. Rebirth {n}!** You now have +{int(data.REBIRTH_LUCK * n * 100)}% luck and "
                  f"+{int(data.REBIRTH_SELL * n * 100)}% sell value forever. Your legendary-and-up ores and mutated ores came with you.")


def fmt_chance(n):
    """Pass an Ore to show its real odds, or a plain number."""
    if isinstance(n, data.Ore):
        n = SHOWN_ODDS.get(n.key, n.chance)
    if n < 10:
        return f"1 in {n:.1f}".replace(".0", "")
    return f"1 in {int(round(n)):,}"


def fmt_time(sec):
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    if sec < 86400:
        return f"{sec // 3600}h {(sec % 3600) // 60}m"
    return f"{sec // 86400}d {(sec % 86400) // 3600}h"
