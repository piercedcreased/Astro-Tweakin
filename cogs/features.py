"""v0.7.0 "Ultra Super Mega Crack Rock Edition": pickaxe enchanting, crates, the daily calendar, the Slug Den,
mining minigames, Space Jack and planet weather. Panel buttons come through Game.handle (owner check + lock)."""
import asyncio
import io
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from game import data, engine, features, images
from game.data import LAYERS, PICKAXE_BY_KEY, PLANETS
from game.engine import fmt_time
from cogs.game import B, Pic, as_files, color_of, ore_line, mut_line

log = logging.getLogger("astro-tweakin.features")
PURPLE = discord.Color.from_rgb(170, 110, 255)


def weather_line(user):
    w = engine.user_weather(user)
    planet = LAYERS[user["layer"] - 1].planet_info
    return (f"{w.emoji} Weather on {planet.name}: **{w.name}**" + (f" ({w.blurb})" if w.key != "clear" else "")
            + f" · changes in {fmt_time(engine.weather_ends() - time.time())}")


class Features(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = bot.db
        self.cracking = {}        # uid -> set of slots picked to empty with a Crack Rock
        self.minigames = {}       # uid -> features.Minigame waiting for an answer
        self.hands = {}           # uid -> features.JackHand in progress
        n = features.jack_refund_all(self.db)
        if n:
            log.info("Refunded %d Space Jack hand(s) left unfinished by a restart", n)

    @property
    def game(self):
        return self.bot.get_cog("Game")

    async def show(self, interaction, panel):
        await self.game.show(interaction, panel)

    # ================================================================ routing from Game.handle

    async def dispatch(self, interaction, action, uid):
        if action == "en" or action.startswith("en_"):
            await self.enchant_action(interaction, action[3:], uid)
        elif action == "cr" or action.startswith("cr_"):
            await self.crate_action(interaction, action[3:], uid)
        elif action == "sl" or action.startswith("sl_"):
            await self.slug_action(interaction, action[3:], uid)
        elif action == "arcade":
            await self.show(interaction, self.arcade_panel(uid))
        elif action.startswith("bj_"):
            await self.jack_action(interaction, action[3:], uid)
        elif action.startswith("mg_"):
            await self.minigame_answer(interaction, action[3:], uid)
        elif action in ("cal", "cal_claim"):
            note = None
            if action == "cal_claim":
                ok, note = engine.claim_daily(self.db, uid)
                note = note if ok else "⏳ " + note
            await self.show(interaction, self.calendar_panel(uid, note))
        else:
            return False
        return True

    # ================================================================ enchanting

    def enchant_panel(self, uid, note=None):
        user = self.db.user(uid)
        pick = PICKAXE_BY_KEY[user["pickaxe"]]
        slots = engine.enchants(self.db, uid)
        rocks = self.db.qty(uid, data.CRACK_ROCK)
        cracking = self.cracking.get(uid)
        cost = features.enchant_cost(user)
        e = discord.Embed(title=f"✨ Enchant your {pick.name}", color=PURPLE)
        lines = [note + "\n"] if note else []
        if cracking is not None:
            lines.append("💎 **Pick the slots to empty**, then press **Done**. The Crack Rock is only used up when you press Done.")
        else:
            lines.append(f"Each pickaxe has **{data.ENCHANT_SLOTS} slots**. Enchanting a slot costs **{cost:,}** coins and "
                         f"gives a random enchant. Right after, you can reroll that slot **once** for "
                         f"**{data.ENCHANT_REROLL_COST:,}** coins. After that it's locked in until you empty it with an "
                         f"💎 **Enchanted Crack Rock** (1 in {data.CRACK_ROCK_CHANCE:,} per Mine).\n"
                         f"⚠️ Enchants don't carry over when you craft your next pickaxe.")
        e.description = "\n".join(lines)
        for s in range(data.ENCHANT_SLOTS):
            row = slots.get(s)
            mark = ("☑️ " if cracking is not None and s in cracking else "") + f"Slot {s + 1}"
            if row:
                extra = "" if row["rerolled"] else "\n🎲 reroll available"
                e.add_field(name=mark, value=engine.enchant_text(row["ench"], row["level"]) + extra, inline=False)
            else:
                e.add_field(name=mark, value="*empty*", inline=False)
        e.set_footer(text=f"💎 Crack Rocks: {rocks} · You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        if cracking is not None:
            for s in range(data.ENCHANT_SLOTS):
                on = s in cracking
                v.add_item(B(f"en_pick_{s}", uid, f"Slot {s + 1}", "☑️" if on else "⬜",
                             discord.ButtonStyle.danger if on else discord.ButtonStyle.secondary, disabled=s not in slots))
            v.add_item(B("en_all", uid, "All", "💥", row=1, disabled=not slots))
            v.add_item(B("en_done", uid, "Done", "💎", discord.ButtonStyle.danger, row=1, disabled=not cracking))
            v.add_item(B("en_cancel", uid, "Cancel", "↩️", row=1))
            return e, [], v
        for s in range(data.ENCHANT_SLOTS):
            row = slots.get(s)
            if row is None:
                v.add_item(B(f"en_do_{s}", uid, f"Enchant slot {s + 1}", "✨", discord.ButtonStyle.success,
                             disabled=user["coins"] < cost))
            else:
                v.add_item(B(f"en_re_{s}", uid, f"Reroll slot {s + 1}", "🎲", discord.ButtonStyle.primary,
                             disabled=bool(row["rerolled"]) or user["coins"] < data.ENCHANT_REROLL_COST))
        v.add_item(B("en_crack", uid, f"Use Crack Rock ({rocks})", "💎", discord.ButtonStyle.danger, row=1,
                     disabled=rocks <= 0 or not slots))
        v.add_item(B("shop", uid, "Back to shop", "↩️", row=1))
        return e, [], v

    async def enchant_action(self, interaction, what, uid):
        note = None
        if what.startswith("do_"):
            ok, note = features.enchant_slot(self.db, uid, int(what[3:]))
            note = note if ok else "❌ " + note
        elif what.startswith("re_"):
            ok, note = features.reroll_slot(self.db, uid, int(what[3:]))
            note = note if ok else "❌ " + note
        elif what == "crack":
            if self.db.qty(uid, data.CRACK_ROCK) <= 0:
                note = "❌ You don't have an Enchanted Crack Rock. They drop 1 in 2,500 Mines (more in Crystal Fog)."
            else:
                self.cracking[uid] = set()
        elif what.startswith("pick_") and uid in self.cracking:
            s = int(what[5:])
            self.cracking[uid] ^= {s}
        elif what == "all" and uid in self.cracking:
            self.cracking[uid] = set(engine.enchants(self.db, uid))
        elif what == "done" and uid in self.cracking:
            ok, note = features.crack_slots(self.db, uid, self.cracking[uid])
            if ok:
                self.cracking.pop(uid, None)
            else:
                note = "❌ " + note
        elif what == "cancel":
            self.cracking.pop(uid, None)
        await self.show(interaction, self.enchant_panel(uid, note))

    # ================================================================ crates

    def crates_panel(self, uid, note=None):
        user = self.db.user(uid)
        owned = {c.key: self.db.qty(uid, "crate:" + c.key) for c in data.CRATES}
        e = discord.Embed(title="📦 Crates", color=discord.Color.from_rgb(200, 150, 90), description=(note + "\n\n" if note else "") +
                          "Crack them open for coins, snacks, ore hauls, mutated ores, Lucky Roll recharges, "
                          "Enchanted Crack Rocks and even better crates.")
        for c in data.CRATES:
            where = f"Buy for **{features.crate_price(user, c):,}** coins" if c.price else "Can't be bought"
            e.add_field(name=f"{c.emoji} {c.name}  ·  you have {owned[c.key]}", value=where, inline=False)
        e.set_footer(text="Find crates in mining minigames, bounties and the daily calendar · You have "
                          f"{user['coins']:,} coins")
        e.set_image(url="attachment://crates.jpg")
        key = "crates:" + ",".join(str(owned[c.key]) for c in data.CRATES)
        files = [Pic("crates.jpg", key, lambda: images.crate_card(owned))]
        v = discord.ui.View(timeout=None)
        for c in data.CRATES:
            v.add_item(B(f"cr_open_{c.key}", uid, f"Open {c.name}", c.emoji, discord.ButtonStyle.success,
                         disabled=owned[c.key] <= 0))
        for c in data.CRATES:
            if c.price:
                v.add_item(B(f"cr_buy_{c.key}", uid, f"Buy {c.name}", "🛒", row=1,
                             disabled=user["coins"] < features.crate_price(user, c)))
        v.add_item(B("inv", uid, "Back", "↩️", row=1))
        return e, files, v

    async def crate_action(self, interaction, what, uid):
        if what.startswith("buy_") and what[4:] in data.CRATE_BY_KEY:
            ok, msg = features.buy_crate(self.db, uid, what[4:])
            await self.show(interaction, self.crates_panel(uid, ("✅ " if ok else "❌ ") + msg))
            return
        if not (what.startswith("open_") and what[5:] in data.CRATE_BY_KEY):
            await self.show(interaction, self.crates_panel(uid))
            return
        crate = data.CRATE_BY_KEY[what[5:]]
        ok, res = features.open_crate(self.db, uid, crate.key)
        if not ok:
            await self.show(interaction, self.crates_panel(uid, "❌ " + res))
            return
        gif = await asyncio.to_thread(images.crate_gif, crate, res.kind)
        e = discord.Embed(title=f"{crate.emoji} Opening a {crate.name}...", color=color_of(crate.color))
        e.set_image(url="attachment://crate.gif")
        await self.game.edit_panel(interaction, embed=e, view=None,
                                   attachments=[discord.File(io.BytesIO(gif), filename="crate.gif")])
        mid = interaction.message.id if interaction.message else None
        if mid:
            self.game.attached[mid] = {"crate.gif": "crate"}
        task = asyncio.create_task(self._finish_crate(interaction, uid, crate, res))
        self.game.bg_tasks.add(task)
        task.add_done_callback(self.game.bg_tasks.discard)

    async def _finish_crate(self, interaction, uid, crate, res):
        await asyncio.sleep(self.game.ROLL_HOLD)
        try:
            async with self.game.locks[uid]:
                text = f"{crate.emoji} Your **{crate.name}** had: {res.text}"
                if res.muts:
                    text += "\n" + "\n".join(mut_line(o, m) for o, m in res.muts)
                if res.ores and res.kind == "ores":
                    text += "\n" + "\n".join(ore_line(o) for o in sorted(res.ores, key=lambda o: -o.chance)[:6])
                e, _, v = self.crates_panel(uid, text)
                e.set_image(url="attachment://crate.gif")
                msg = await interaction.original_response()
                keep = [a for a in msg.attachments if a.filename == "crate.gif"]
                await interaction.edit_original_response(embed=e, view=v, attachments=keep)
            if res.best is not None:
                await self.game.celebrate(interaction, res.best, res.muts)
        except discord.HTTPException as ex:
            log.warning("Couldn't finish a crate opening for %s: %s", uid, ex)

    # ================================================================ daily calendar

    def calendar_panel(self, uid, note=None):
        user = self.db.user(uid)
        day, wait, missed = engine.calendar_state(user)
        ready = wait <= 0
        done_upto = (day - 1) if ready else user["cal_day"]
        labels = []
        for kind, amount in data.CALENDAR:
            labels.append({"coins": "COINS", "snack": "SNACK", "roll": "LUCKY ROLL"}.get(kind, "CRATE"))
        e = discord.Embed(title="📅 Daily Calendar", color=discord.Color.green() if ready else discord.Color.dark_grey())
        lines = [note] if note else []
        if ready:
            lines.append(f"🎁 **Day {day} is ready to claim!**" + (" (You missed a day, so it's a redo of this day.)" if missed else ""))
        else:
            lines.append(f"⏳ Day {day} unlocks in **{fmt_time(wait)}**.")
        lines.append("Claim once a day. Day 7 is a 🌌 **Cosmic Crate**. Miss a day and you only drop back one step.")
        e.description = "\n".join(lines)
        e.set_footer(text=f"🔥 {user['daily_streak']} day streak")
        e.set_image(url="attachment://calendar.jpg")
        key = f"cal:{day}:{done_upto}:{ready}"
        files = [Pic("calendar.jpg", key, lambda: images.calendar_card(day, done_upto, ready, labels))]
        v = discord.ui.View(timeout=None)
        v.add_item(B("cal_claim", uid, f"Claim day {day}" if ready else f"Day {day} in {fmt_time(wait)}", "🎁",
                     discord.ButtonStyle.success, disabled=not ready))
        v.add_item(B("player", uid, "Back", "↩️"))
        return e, files, v

    # ================================================================ Slug Den

    def slug_panel(self, uid, note=None):
        user = self.db.user(uid)
        name, emoji, speed, luck = data.SLUG_RANKS[user["slug_rank"]]
        per_hour = engine.slug_output(self.db, user, 3600)
        e = discord.Embed(title=f"{emoji} Slug Den", color=discord.Color.from_rgb(130, 230, 80), description=(
            (note + "\n\n" if note else "") +
            f"Your crew: **{user['sluglings']} {name}** · dig x{speed:g} speed at x{luck:g} luck\n"
            f"⛏️ About **{per_hour:,}** ores an hour · they dig for up to **{engine.collect_cap(user)}h** while you're away"))
        nxt = user["slug_rank"] + 1
        if nxt < len(data.SLUG_RANKS):
            n2, e2, s2, l2 = data.SLUG_RANKS[nxt]
            e.add_field(name=f"🌟 Prestige to {e2} {n2}", inline=False, value=(
                f"Needs **{data.SLUG_PRESTIGE_NEED}** sluglings + **{features.prestige_cost(user):,}** coins. "
                f"Your crew becomes 1 {n2[:-1]} (x{s2:g} speed, x{l2:g} luck), and hiring starts cheap again."))
        else:
            e.add_field(name="🌟 Prestige", value="Your slugs are the best in the universe.", inline=False)
        if user["slug_pack"] < len(data.SLUG_PACKS) - 1:
            e.add_field(name=f"🎒 Bigger Backpacks ({data.SLUG_PACKS[user['slug_pack']]}h → {data.SLUG_PACKS[user['slug_pack'] + 1]}h)",
                        value=f"{features.pack_cost(user):,} coins", inline=False)
        e.add_field(name=f"🐌 Hire #{user['sluglings'] + 1}", value=f"{engine.slugling_cost(user['sluglings']):,} coins", inline=False)
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        v.add_item(B("sl_hire", uid, "Hire slugling", "🐌", discord.ButtonStyle.success))
        v.add_item(B("sl_prestige", uid, "Prestige", "🌟", discord.ButtonStyle.primary,
                     disabled=nxt >= len(data.SLUG_RANKS) or user["sluglings"] < data.SLUG_PRESTIGE_NEED))
        v.add_item(B("sl_pack", uid, "Bigger Backpacks", "🎒", disabled=user["slug_pack"] >= len(data.SLUG_PACKS) - 1))
        v.add_item(B("shop", uid, "Back to shop", "↩️"))
        return e, [], v

    async def slug_action(self, interaction, what, uid):
        note = None
        if what in ("hire", "prestige", "pack"):
            fn = {"hire": engine.hire_slugling, "prestige": features.slug_prestige, "pack": features.upgrade_pack}[what]
            ok, msg = fn(self.db, uid)
            note = ("✅ " if ok else "❌ ") + msg
        await self.show(interaction, self.slug_panel(uid, note))

    # ================================================================ arcade and Space Jack

    def arcade_panel(self, uid, note=None):
        user = self.db.user(uid)
        e = discord.Embed(title="🎮 Arcade", color=discord.Color.from_rgb(255, 120, 200), description=(
            (note + "\n\n" if note else "") +
            "🃏 **Space Jack**: blackjack against Tweaker Tim. Get closer to 21 than him without going over. "
            "Blackjack pays 3:2.\n"
            "☄️ **Asteroid Run**: steer through falling rocks to Tim's station (also in 🚀 Ship)."))
        e.set_footer(text=f"You have {user['coins']:,} coins")
        v = discord.ui.View(timeout=None)
        for m in data.JACK_BETS:
            bet = features.jack_bet(user, m)
            v.add_item(B(f"bj_bet_{m}", uid, f"Space Jack: bet {bet:,}", "🃏", discord.ButtonStyle.success,
                         disabled=user["coins"] < bet))
        v.add_item(B("ar_start", uid, "Asteroid Run", "☄️", discord.ButtonStyle.primary, row=1))
        v.add_item(B("back", uid, "Back", "↩️", row=1))
        return e, [], v

    @staticmethod
    def _cards(hand, hide_second=False):
        return "  ".join(("🂠" if hide_second and i == 1 else f"`{c}`") for i, c in enumerate(hand))

    def jack_panel(self, uid, h, note=None):
        user = self.db.user(uid)
        hide = not h.done
        dealer_total = "?" if hide else str(features.hand_total(h.dealer))
        result = {"won": f"🎉 **You win {h.bet * 2:,} coins!**", "blackjack": f"🃏 **BLACKJACK!** You win {h.bet * 5 // 2:,} coins!",
                  "push": "🤝 **Push.** You get your bet back.", "lost": f"💸 **Tim wins.** You lost {h.bet:,} coins."}
        color = {"won": discord.Color.green(), "blackjack": discord.Color.gold(), "push": discord.Color.light_grey(),
                 "lost": discord.Color.red()}.get(h.state, discord.Color.dark_green())
        e = discord.Embed(title="🃏 Space Jack", color=color)
        e.add_field(name=f"👽 Tim ({dealer_total})", value=self._cards(h.dealer, hide), inline=False)
        e.add_field(name=f"🧑‍🚀 You ({features.hand_total(h.player)})", value=self._cards(h.player), inline=False)
        lines = [note] if note else []
        lines.append(result.get(h.state, f"Bet: **{h.bet:,}** coins. Hit, stand or double?"))
        e.description = "\n".join(lines)
        e.set_footer(text=f"You have {user['coins']:,} coins · Tim stands on 17")
        v = discord.ui.View(timeout=None)
        if h.done:
            mult = next((m for m in data.JACK_BETS if features.jack_bet(user, m) * (2 if h.doubled else 1) == h.bet), data.JACK_BETS[0])
            v.add_item(B(f"bj_bet_{mult}", uid, "Deal again", "🃏", discord.ButtonStyle.success,
                         disabled=user["coins"] < features.jack_bet(user, mult)))
            v.add_item(B("arcade", uid, "Arcade", "🎮"))
            v.add_item(B("back", uid, "Back to mine", "↩️"))
        else:
            v.add_item(B("bj_hit", uid, "Hit", "👆", discord.ButtonStyle.primary))
            v.add_item(B("bj_stand", uid, "Stand", "✋", discord.ButtonStyle.secondary))
            v.add_item(B("bj_double", uid, "Double", "💰", discord.ButtonStyle.success,
                         disabled=len(h.player) != 2 or user["coins"] < h.bet))
        return e, [], v

    async def jack_action(self, interaction, what, uid):
        note = None
        if what.startswith("bet_"):
            old = self.hands.get(uid)
            if old and not old.done:
                await self.show(interaction, self.jack_panel(uid, old, "Finish this hand first."))
                return
            h, why = features.jack_deal(self.db, uid, int(what[4:]))
            if h is None:
                await self.show(interaction, self.arcade_panel(uid, "❌ " + why))
                return
            self.hands[uid] = h
        else:
            h = self.hands.get(uid)
            if h is None or h.done:
                await self.show(interaction, self.arcade_panel(uid, "That hand is over. Deal a new one."))
                return
            if what == "hit":
                features.jack_hit(self.db, h)
            elif what == "stand":
                features.jack_stand(self.db, h)
            elif what == "double":
                ok, why = features.jack_double(self.db, h)
                if not ok:
                    note = "❌ " + why
        features.jack_save(self.db, h)
        if h.done:
            self.hands.pop(uid, None)
        await self.show(interaction, self.jack_panel(uid, h, note))

    # ================================================================ mining minigames

    def minigame_panel(self, uid, game, head=None):
        e = discord.Embed(title="🎲 Mining minigame!", color=discord.Color.from_rgb(255, 200, 60),
                          description=(head + "\n\n" if head else "") + game.prompt +
                          "\n\n*Everyone gets something. Get it right for a 📦 Supply Crate.*")
        v = discord.ui.View(timeout=None)
        for i, label in enumerate(game.options):
            emoji = label if not label.isdigit() else None
            v.add_item(B(f"mg_{i}", uid, label if emoji is None else None, emoji, discord.ButtonStyle.primary))
        return e, [], v

    async def start_minigame(self, interaction, uid, head=None):
        game = self.minigames.get(uid) or features.new_minigame()
        self.minigames[uid] = game
        await self.show(interaction, self.minigame_panel(uid, game, head))

    async def minigame_answer(self, interaction, what, uid):
        game = self.minigames.pop(uid, None)
        if game is None or not what.isdigit():
            await self.show(interaction, self.game.main_panel(uid, headline="That minigame already ended."))
            return
        right, msg = features.finish_minigame(self.db, uid, game, int(what))
        if not right and game.kind != "rock":
            msg += f" (It was {game.options[game.answer]}.)"
        elif not right:
            msg += f" (The ore was in rock {game.answer + 1}.)"
        await self.show(interaction, self.game.main_panel(uid, headline="🎲 " + msg))

    # ================================================================ slash commands

    @app_commands.command(name="weather", description="The weather on every planet right now")
    async def weather_cmd(self, interaction: discord.Interaction):
        user = self.db.user(interaction.user.id, interaction.user.display_name)
        here = LAYERS[user["layer"] - 1].planet
        lines = []
        for key, p in PLANETS.items():
            w = engine.weather_for(key)
            lines.append(f"{p.emoji} **{p.name}**{' (you)' if key == here else ''}: {w.emoji} {w.name}"
                         + (f" · {w.blurb}" if w.key != "clear" else ""))
        e = discord.Embed(title="🌦️ Planet weather", color=discord.Color.blue(), description="\n".join(lines))
        e.set_footer(text=f"Weather changes every {data.WEATHER_HOURS}h · next change in "
                          f"{fmt_time(engine.weather_ends() - time.time())}")
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def _open(self, interaction, panel):
        e, files, v = panel
        await interaction.response.send_message(embed=e, files=as_files(files), view=v)

    @app_commands.command(name="crates", description="Open and buy crates")
    async def crates_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.crates_panel(interaction.user.id))

    @app_commands.command(name="enchant", description="Enchant your pickaxe")
    async def enchant_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.enchant_panel(interaction.user.id))

    @app_commands.command(name="arcade", description="Space Jack and Asteroid Run")
    async def arcade_cmd(self, interaction: discord.Interaction):
        self.db.user(interaction.user.id, interaction.user.display_name)
        await self._open(interaction, self.arcade_panel(interaction.user.id))


async def setup(bot):
    await bot.add_cog(Features(bot))
