"""v0.7.0 rules: pickaxe enchanting, Enchanted Crack Rocks, crates, slugling prestige, mining minigames
and Space Jack. Like engine.py, nothing here touches Discord, and every action that changes a save is atomic."""
from dataclasses import dataclass, field

from . import data, engine
from .data import LAYERS, PICKAXE_BY_KEY
from .engine import atomic, rng


def scale(user):
    return data.scale(user["max_layer"])


# ================================================================ enchanting

def enchant_cost(user):
    return data.enchant_cost(PICKAXE_BY_KEY[user["pickaxe"]].price)


def roll_enchant():
    e = rng.choices(data.ENCHANTS, weights=[x.weight for x in data.ENCHANTS])[0]
    level = rng.choices([1, 2, 3], weights=data.ENCHANT_LEVEL_WEIGHTS)[0]
    return e.key, level


@atomic
def enchant_slot(db, uid, slot):
    """Puts a random enchant in an empty slot. Returns (ok, message)."""
    if not 0 <= slot < data.ENCHANT_SLOTS:
        return False, "That slot doesn't exist."
    user = db.user(uid)
    if slot in engine.enchants(db, uid):
        return False, "That slot is already enchanted. Empty it with an Enchanted Crack Rock first."
    cost = enchant_cost(user)
    if user["coins"] < cost:
        return False, f"Enchanting costs **{cost:,}** coins. You have {user['coins']:,}."
    db.add_coins(uid, -cost)
    key, level = roll_enchant()
    db.run("INSERT INTO enchants (user_id, slot, ench, level, rerolled) VALUES (?, ?, ?, ?, 0)", uid, slot, key, level)
    db.bump(uid, "enchants")
    if len(engine.enchants(db, uid)) >= data.ENCHANT_SLOTS:
        db.bump(uid, "enchants_full")
    return True, f"✨ Slot {slot + 1} got {engine.enchant_text(key, level)}!"


@atomic
def reroll_slot(db, uid, slot):
    """The one reroll a freshly enchanted slot gets, for a flat fee."""
    row = engine.enchants(db, uid).get(slot)
    if row is None:
        return False, "That slot is empty."
    if row["rerolled"]:
        return False, "You already used this slot's reroll. Only an Enchanted Crack Rock can clear it now."
    user = db.user(uid)
    if user["coins"] < data.ENCHANT_REROLL_COST:
        return False, f"A reroll costs **{data.ENCHANT_REROLL_COST:,}** coins."
    db.add_coins(uid, -data.ENCHANT_REROLL_COST)
    key, level = roll_enchant()
    db.run("UPDATE enchants SET ench=?, level=?, rerolled=1 WHERE user_id=? AND slot=?", key, level, uid, slot)
    return True, f"🎲 Rerolled slot {slot + 1}: {engine.enchant_text(key, level)}"


@atomic
def crack_slots(db, uid, slots):
    """Uses one Enchanted Crack Rock to empty the chosen slots."""
    have = engine.enchants(db, uid)
    slots = sorted({s for s in slots if s in have})
    if not slots:
        return False, "Pick at least one enchanted slot to empty."
    db.add_item(uid, data.CRACK_ROCK, -1)
    for s in slots:
        db.run("DELETE FROM enchants WHERE user_id=? AND slot=?", uid, s)
    names = ", ".join(f"slot {s + 1}" for s in slots)
    return True, f"💎 The Crack Rock shatters. Emptied {names}. Enchant them again whenever you like."


# ================================================================ crates

def crate_price(user, crate):
    return crate.price * scale(user)


@atomic
def buy_crate(db, uid, key):
    crate = data.CRATE_BY_KEY[key]
    user = db.user(uid)
    if not crate.price:
        return False, f"{crate.name}s can't be bought. Find them in other crates, bounties and the daily calendar."
    price = crate_price(user, crate)
    if user["coins"] < price:
        return False, f"A {crate.name} costs **{price:,}** coins. You have {user['coins']:,}."
    db.add_coins(uid, -price)
    db.add_item(uid, "crate:" + key, 1)
    return True, f"You bought a {crate.emoji} **{crate.name}**."


@dataclass
class CrateResult:
    kind: str
    text: str
    ores: list = field(default_factory=list)
    best: object = None
    muts: list = field(default_factory=list)


@atomic
def open_crate(db, uid, key):
    """Opens one crate. Returns (ok, CrateResult or message)."""
    crate = data.CRATE_BY_KEY.get(key)
    if crate is None:
        return False, "That crate doesn't exist."
    if db.qty(uid, "crate:" + key) <= 0:
        return False, f"You don't have a {crate.name}."
    db.add_item(uid, "crate:" + key, -1)
    db.bump(uid, "crates_opened")
    user = db.user(uid)
    kind, _, amount = rng.choices(crate.loot, weights=[l[1] for l in crate.loot])[0]
    if kind == "coins":
        coins = int(1_000 * amount * scale(user) * rng.uniform(0.7, 1.5))
        db.add_coins(uid, coins)
        return True, CrateResult(kind, f"💰 **{coins:,} coins**")
    if kind == "snack":
        got = [rng.choice(data.SNACKS) for _ in range(int(amount))]
        for s in got:
            db.add_item(uid, "snack:" + s.key, 1)
        return True, CrateResult(kind, "🍬 " + ", ".join(f"{s.emoji} **{s.name}**" for s in got))
    if kind == "roll":
        db.set(uid, last_roll=0)
        return True, CrateResult(kind, "🎰 a **Lucky Roll recharge** (ready now)")
    if kind == "crack":
        db.add_item(uid, data.CRACK_ROCK, 1)
        return True, CrateResult(kind, "💎 an **Enchanted Crack Rock**!")
    if kind == "tag":
        from . import multi
        tag = multi.random_tag()
        db.add_item(uid, "tag:" + tag.key, 1)
        return True, CrateResult(kind, f"{tag.emoji} a **{tag.name}**! Use it from 🎒 Inventory → 🏷️ Tags, or sell it on the 🏪 Market.")
    if kind.startswith("crate:"):
        c = data.CRATE_BY_KEY[kind[6:]]
        db.add_item(uid, kind, 1)
        return True, CrateResult(kind, f"{c.emoji} a **{c.name}** inside the crate!")
    if kind == "mut":
        # a guaranteed mutated ore from your layer, rolled with good luck
        luck = engine.luck_for(db, user).total * 10
        ore = engine.roll_ore(user["layer"], luck)
        mut = rng.choices(data.MUTATIONS[2:], weights=[1 / m.chance for m in data.MUTATIONS[2:]])[0]
        db.discover(uid, ore.key, 1)
        db.add_item(uid, engine.mut_item(ore, mut), 1)
        db.bump(uid, "mut_" + mut.key)
        db.bump(uid, "mutations")
        score = engine.find_score(ore, mut)
        if score > (user["best_score"] or 0):
            db.set(uid, best_find=engine.mut_item(ore, mut), best_score=score)
        return True, CrateResult(kind, f"{mut.emoji} a **{mut.name} {ore.name}**!", [ore], ore, [(ore, mut)])
    # ores: a handful of rolls at boosted luck
    luck = engine.luck_for(db, user).total * amount
    ores = [engine.roll_ore(user["layer"], luck) for _ in range(rng.randint(3, 6))]
    muts = []
    best = engine.record_finds(db, uid, ores, muts)
    return True, CrateResult(kind, f"⛏️ **{len(ores)} ores** rolled at x{luck:,.1f} luck", ores, best, muts)


# ================================================================ slugling prestige and backpacks

def prestige_cost(user):
    return data.SLUG_PRESTIGE_COST * scale(user) * (user["slug_rank"] + 1)


@atomic
def slug_prestige(db, uid):
    user = db.user(uid)
    if user["slug_rank"] >= len(data.SLUG_RANKS) - 1:
        return False, "Your sluglings are already the best slugs in the universe."
    if user["sluglings"] < data.SLUG_PRESTIGE_NEED:
        return False, f"You need **{data.SLUG_PRESTIGE_NEED}** sluglings to prestige (you have {user['sluglings']})."
    cost = prestige_cost(user)
    if user["coins"] < cost:
        return False, f"Prestige costs **{cost:,}** coins. You have {user['coins']:,}."
    engine.collect_sluglings(db, uid)          # bank what the old crew dug first
    db.add_coins(uid, -cost)
    rank = user["slug_rank"] + 1
    db.set(uid, slug_rank=rank, sluglings=1)
    name, emoji, speed, _ = data.SLUG_RANKS[rank]
    return True, (f"{emoji} Your crew evolved into **{name}**! You keep 1 of them. Each one digs x{speed:g} as fast "
                  f"with better luck. Hiring starts cheap again.")


def pack_cost(user):
    return data.SLUG_PACK_COST * scale(user) * (user["slug_pack"] + 1)


@atomic
def upgrade_pack(db, uid):
    user = db.user(uid)
    if user["slug_pack"] >= len(data.SLUG_PACKS) - 1:
        return False, "Your sluglings already have the biggest backpacks."
    cost = pack_cost(user)
    if user["coins"] < cost:
        return False, f"Bigger Backpacks cost **{cost:,}** coins. You have {user['coins']:,}."
    engine.collect_sluglings(db, uid)
    db.add_coins(uid, -cost)
    db.set(uid, slug_pack=user["slug_pack"] + 1)
    return True, f"🎒 Bigger Backpacks! Your sluglings now dig for up to **{data.SLUG_PACKS[user['slug_pack'] + 1]}h** while you're away."


# ================================================================ mining minigames

@dataclass
class Minigame:
    kind: str             # "rock", "math", "color"
    prompt: str
    options: list         # button labels/emoji
    answer: int


COLORS = [("🟥", "red"), ("🟦", "blue"), ("🟩", "green"), ("🟨", "yellow"), ("🟪", "purple"), ("🟧", "orange")]


def new_minigame():
    kind = rng.choice(["rock", "math", "color"])
    if kind == "rock":
        return Minigame(kind, "⛏️ **A vein cracked open!** One of these rocks is full of ore. Pick one!",
                        ["🪨", "🪨", "🪨"], rng.randrange(3))
    if kind == "math":
        a, b = rng.randint(2, 12), rng.randint(2, 12)
        op = rng.choice(["+", "x"])
        right = a + b if op == "+" else a * b
        wrong = set()
        while len(wrong) < 3:
            w = right + rng.choice([-3, -2, -1, 1, 2, 3, 10, -10])
            if w > 0 and w != right:
                wrong.add(w)
        opts = [right] + list(wrong)
        rng.shuffle(opts)
        return Minigame(kind, f"👽 **Tim's counting his cash and lost track.** What's **{a} {op} {b}**?",
                        [str(o) for o in opts], opts.index(right))
    picks = rng.sample(COLORS, 4)
    ans = rng.randrange(4)
    return Minigame(kind, f"💎 **Grab the {picks[ans][1]} gem** before it rolls away!", [p[0] for p in picks], ans)


@atomic
def finish_minigame(db, uid, game, choice):
    """Everyone gets something; a right answer gets a lot more plus a Supply Crate."""
    user = db.user(uid)
    right = choice == game.answer
    coins = int(2_000 * scale(user) * (rng.uniform(1.0, 2.0) if right else rng.uniform(0.2, 0.4)))
    db.add_coins(uid, coins)
    db.bump(uid, "minigames")
    if right:
        db.add_item(uid, "crate:supply", 1)
        db.bump(uid, "minigames_won")
        return True, f"✅ **Nice!** You got **{coins:,} coins** and a 📦 **Supply Crate**."
    return False, f"So close! Have **{coins:,} coins** for trying."


# ================================================================ Space Jack

SUITS = "♠♥♦♣"
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def card_value(card):
    r = card[:-1]
    return 11 if r == "A" else 10 if r in ("J", "Q", "K") else int(r)


def hand_total(hand):
    total = sum(card_value(c) for c in hand)
    aces = sum(1 for c in hand if c.startswith("A"))
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def draw(r=None):
    r = r or rng
    return r.choice(RANKS) + r.choice(SUITS)


@dataclass
class JackHand:
    uid: int
    bet: int
    player: list
    dealer: list
    state: str = "playing"   # playing, won, lost, push, blackjack
    doubled: bool = False

    @property
    def done(self):
        return self.state != "playing"


def jack_bet(user, mult=1):
    """A suggested bet for where you are (the default in the bet box)."""
    return data.JACK_BASE * scale(user) * mult


@atomic
def jack_deal(db, uid, bet):
    """Deals a hand for any bet you can afford."""
    if bet < data.JACK_MIN_BET:
        return None, f"The smallest bet is {data.JACK_MIN_BET:,} coins."
    user = db.user(uid)
    if user["coins"] < bet:
        return None, f"That bet is **{bet:,}** coins. You have {user['coins']:,}."
    db.add_coins(uid, -bet)
    db.bump(uid, "jack_hands")
    h = JackHand(uid, bet, [draw(), draw()], [draw(), draw()])
    if hand_total(h.player) == 21:
        _settle(db, h, "blackjack" if hand_total(h.dealer) != 21 else "push")
    elif hand_total(h.dealer) == 21:
        # Tim peeks like a real dealer: his blackjack ends the hand right away, before you can double into it
        _settle(db, h, "lost")
    return h, None


def _settle(db, h, state):
    h.state = state
    pay = {"won": 2 * h.bet, "blackjack": h.bet * 5 // 2, "push": h.bet, "lost": 0}[state]
    if pay:
        db.add_coins(h.uid, pay)
    if state in ("won", "blackjack"):
        db.bump(h.uid, "jack_wins")
    return pay


def _dealer_plays(db, h):
    while hand_total(h.dealer) < 17:
        h.dealer.append(draw())
    p, d = hand_total(h.player), hand_total(h.dealer)
    _settle(db, h, "won" if d > 21 or p > d else "push" if p == d else "lost")


@atomic
def jack_hit(db, h):
    if h.done:
        return
    h.player.append(draw())
    if hand_total(h.player) > 21:
        _settle(db, h, "lost")
    elif hand_total(h.player) == 21:
        _dealer_plays(db, h)


@atomic
def jack_stand(db, h):
    if not h.done:
        _dealer_plays(db, h)


@atomic
def jack_double(db, h):
    """Double the bet, take exactly one more card, then stand."""
    if h.done or len(h.player) != 2:
        return False, "You can only double on your first two cards."
    user = db.user(h.uid)
    if user["coins"] < h.bet:
        return False, f"Doubling needs another **{h.bet:,}** coins."
    db.add_coins(h.uid, -h.bet)
    h.bet *= 2
    h.doubled = True
    h.player.append(draw())
    if hand_total(h.player) > 21:
        _settle(db, h, "lost")
    else:
        _dealer_plays(db, h)
    return True, None


@atomic
def jack_refund(db, h):
    """A hand left unfinished (bot restarted, or abandoned for too long) gets its bet back."""
    if not h.done:
        _settle(db, h, "push")


def layer_of(user):
    return LAYERS[user["layer"] - 1]


def jack_save(db, h):
    """Keeps an unfinished hand in the save, so a restart refunds the bet instead of eating it."""
    if h.done:
        db.run("DELETE FROM meta WHERE key=?", f"jack:{h.uid}")
    else:
        db.set_meta(f"jack:{h.uid}", h.bet)


@atomic
def jack_refund_all(db):
    """On startup: give back bets from hands that were still being played when the bot stopped."""
    n = 0
    for r in db.q("SELECT key, value FROM meta WHERE key LIKE 'jack:%'"):
        try:
            db.add_coins(int(r["key"][5:]), int(float(r["value"])))
            n += 1
        except Exception:
            pass
        db.run("DELETE FROM meta WHERE key=?", r["key"])
    return n
