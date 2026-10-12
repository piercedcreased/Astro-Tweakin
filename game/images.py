"""Every picture in the game is drawn here with Pillow, so no image files are needed.

- ore_icon(ore)              -> PNG of an ore (cached on disk)
- reveal_gif(ore, user_name) -> the special-effect GIF for rare finds
- reel_gif(result_prize)     -> the Lucky Roll spinning reel, landing on the prize
- prize_card(prize)          -> still image of the prize (shown after the reel stops)
- pickaxe_icon(pickaxe)      -> PNG for the shop

To use your own art for an ore, drop a PNG named <ore key>.png into assets/ores/
(for example assets/ores/sunshard.png) and it's used instead of the drawn one.
"""
import colorsys
import io
import math
import os
import random
import unicodedata

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .data import TIERS, PRIZES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "assets", "cache")
CUSTOM = os.path.join(ROOT, "assets", "ores")
os.makedirs(CACHE, exist_ok=True)

_FONTS = {}
_FONT_FILES = ["impact.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def font(size):
    if size not in _FONTS:
        f = None
        for name in _FONT_FILES:
            try:
                f = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        _FONTS[size] = f or ImageFont.load_default(size)
    return _FONTS[size]


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _dark(c, t=0.5):
    return _mix(c, (0, 0, 0), t)


def _light(c, t=0.5):
    return _mix(c, (255, 255, 255), t)


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- ore icons

def _draw_chunk(d, S, r, main, hi):
    cx, cy = S / 2, S * 0.55
    pts = []
    n = 9
    for i in range(n):
        a = 2 * math.pi * i / n + r.uniform(-0.2, 0.2)
        rad = S * r.uniform(0.30, 0.40)
        pts.append((cx + math.cos(a) * rad * 1.15, cy + math.sin(a) * rad * 0.85))
    rock = _mix((95, 92, 98), main, 0.25)
    d.polygon(pts, fill=_dark(rock, 0.35))
    # lit facets toward the top-left
    for i in range(n):
        p1, p2 = pts[i], pts[(i + 1) % n]
        mid = (cx - S * 0.05, cy - S * 0.08)
        shade = 0.5 + 0.5 * math.cos(math.atan2((p1[1] + p2[1]) / 2 - cy, (p1[0] + p2[0]) / 2 - cx) + 2.3)
        d.polygon([p1, p2, mid], fill=_mix(_dark(rock, 0.3), _light(rock, 0.25), shade))
    # ore flecks
    for _ in range(14):
        a = r.uniform(0, 2 * math.pi)
        rad = r.uniform(0, S * 0.26)
        x, y = cx + math.cos(a) * rad * 1.1, cy + math.sin(a) * rad * 0.75
        s = r.uniform(S * 0.025, S * 0.06)
        fl = [(x + math.cos(b) * s * r.uniform(0.6, 1.2), y + math.sin(b) * s * r.uniform(0.6, 1.2))
              for b in [k * math.pi / 3 for k in range(6)]]
        d.polygon(fl, fill=main)
        d.polygon(fl[:3] + [(x, y)], fill=hi)


def _crystal(d, base, angle, length, width, main, hi):
    bx, by = base
    dx, dy = math.sin(angle), -math.cos(angle)
    px, py = -dy, dx
    tipx, tipy = bx + dx * length, by + dy * length
    shx, shy = bx + dx * length * 0.75, by + dy * length * 0.75
    L = [(bx - px * width, by - py * width), (shx - px * width, shy - py * width), (tipx, tipy), (bx, by)]
    R = [(bx + px * width, by + py * width), (shx + px * width, shy + py * width), (tipx, tipy), (bx, by)]
    d.polygon(L, fill=main)
    d.polygon(R, fill=_dark(main, 0.35))
    d.line([(bx, by), (tipx, tipy)], fill=hi, width=max(2, int(width * 0.18)))
    d.polygon([(shx - px * width, shy - py * width), (tipx, tipy), (shx, shy)], fill=hi)


def _draw_crystal(d, S, r, main, hi):
    base = (S / 2, S * 0.8)
    d.ellipse([S * 0.22, S * 0.72, S * 0.78, S * 0.88], fill=(60, 55, 65))
    specs = [(-0.75, 0.45, 0.07), (0.7, 0.42, 0.07), (-0.35, 0.6, 0.09), (0.32, 0.58, 0.09), (0.0, 0.7, 0.11)]
    for ang, ln, w in specs:
        _crystal(d, (base[0] + ang * S * 0.08, base[1]), ang * 0.9 + r.uniform(-0.08, 0.08),
                 S * ln, S * w, _mix(main, hi, r.uniform(0, 0.25)), hi)


def _draw_gem(d, S, r, main, hi):
    cx, top, mid, bot = S / 2, S * 0.25, S * 0.42, S * 0.85
    w1, w2 = S * 0.2, S * 0.38
    table = [(cx - w1, top), (cx + w1, top)]
    girdle = [(cx - w2 + i * (2 * w2 / 6), mid) for i in range(7)]
    tl = [(cx - w1 + i * (2 * w1 / 3), top) for i in range(4)]
    d.polygon([girdle[0], tl[0], tl[3], girdle[6], (cx, bot)], fill=main)
    cols = [_light(main, 0.35), main, _light(main, 0.55), _dark(main, 0.15), _light(main, 0.25), _dark(main, 0.3)]
    for i in range(6):
        t = tl[min(3, (i + 1) // 2)]
        d.polygon([girdle[i], girdle[i + 1], t], fill=cols[i])
    d.polygon([tl[0], tl[3], (cx + w1 * 0.6, top + (mid - top) * 0.5), (cx - w1 * 0.6, top + (mid - top) * 0.5)],
              fill=_light(hi, 0.3))
    pcols = [_dark(main, 0.1), _dark(main, 0.35), _light(main, 0.2), _dark(main, 0.5), main, _dark(main, 0.25)]
    for i in range(6):
        d.polygon([girdle[i], girdle[i + 1], (cx, bot)], fill=pcols[i])
    d.line([girdle[0], girdle[6]], fill=_light(hi, 0.5), width=3)


def _draw_orb(img, d, S, r, main, hi):
    cx, cy, rad = S / 2, S / 2, S * 0.34
    for i in range(40, 0, -1):
        t = i / 40
        c = _mix(hi, _dark(main, 0.4), t)
        ox, oy = -rad * 0.3 * (1 - t), -rad * 0.3 * (1 - t)
        d.ellipse([cx - rad * t + ox, cy - rad * t + oy, cx + rad * t + ox, cy + rad * t + oy], fill=c)
    for k in range(3):
        a0 = r.uniform(0, 360)
        rr = rad * (0.5 + 0.15 * k)
        d.arc([cx - rr, cy - rr, cx + rr, cy + rr], a0, a0 + 120, fill=_light(hi, 0.4), width=max(2, int(S * 0.015)))
    d.ellipse([cx - rad * 0.55, cy - rad * 0.65, cx - rad * 0.15, cy - rad * 0.35], fill=(255, 255, 255))


def _star_pts(cx, cy, outer, inner, n=8, rot=0.0):
    pts = []
    for i in range(n * 2):
        a = rot + math.pi * i / n - math.pi / 2
        rr = outer if i % 2 == 0 else inner
        pts.append((cx + math.cos(a) * rr, cy + math.sin(a) * rr))
    return pts


def _draw_star(d, S, r, main, hi):
    cx, cy = S / 2, S / 2
    d.polygon(_star_pts(cx, cy, S * 0.42, S * 0.17, 8), fill=_dark(main, 0.15))
    d.polygon(_star_pts(cx, cy, S * 0.32, S * 0.14, 8, math.pi / 8), fill=main)
    d.polygon(_star_pts(cx, cy, S * 0.2, S * 0.1, 4), fill=hi)
    d.ellipse([cx - S * 0.07, cy - S * 0.07, cx + S * 0.07, cy + S * 0.07], fill=(255, 255, 255))


def _draw_crown(d, S, r, main, hi):
    l, rgt, top, bot = S * 0.18, S * 0.82, S * 0.25, S * 0.72
    pts = [(l, bot), (l, S * 0.38), (S * 0.32, S * 0.52), (S * 0.5, top), (S * 0.68, S * 0.52), (rgt, S * 0.38), (rgt, bot)]
    d.polygon(pts, fill=main)
    d.polygon([(l, bot), (l, S * 0.38), (S * 0.32, S * 0.52), (S * 0.5, top), (S * 0.5, bot)], fill=_light(main, 0.2))
    d.rectangle([l, bot - S * 0.1, rgt, bot], fill=_dark(main, 0.2))
    for x, y in [(l, S * 0.38), (S * 0.5, top), (rgt, S * 0.38)]:
        d.ellipse([x - S * 0.045, y - S * 0.045, x + S * 0.045, y + S * 0.045], fill=hi)
    gems = [(150, 40, 255), (230, 30, 60), (40, 200, 120)]
    for i, x in enumerate([S * 0.32, S * 0.5, S * 0.68]):
        y = bot - S * 0.05
        d.ellipse([x - S * 0.04, y - S * 0.035, x + S * 0.04, y + S * 0.035], fill=gems[i])
    # a little slime drip so it's the Slug King's
    d.ellipse([S * 0.6, bot - S * 0.02, S * 0.66, bot + S * 0.08], fill=(140, 230, 90))


def _draw_slug(d, S, r, main, hi):
    body = [S * 0.12, S * 0.5, S * 0.8, S * 0.78]
    d.ellipse(body, fill=_dark(main, 0.15))
    d.ellipse([S * 0.15, S * 0.48, S * 0.75, S * 0.68], fill=main)
    d.ellipse([S * 0.55, S * 0.38, S * 0.88, S * 0.72], fill=main)
    for ex, ey in [(S * 0.68, S * 0.18), (S * 0.82, S * 0.2)]:
        d.line([(S * 0.72, S * 0.45), (ex, ey)], fill=_dark(main, 0.1), width=int(S * 0.035))
        d.ellipse([ex - S * 0.04, ey - S * 0.04, ex + S * 0.04, ey + S * 0.04], fill=hi)
        d.ellipse([ex - S * 0.015, ey - S * 0.01, ex + S * 0.02, ey + S * 0.025], fill=(30, 20, 10))
    d.arc([S * 0.7, S * 0.5, S * 0.82, S * 0.62], 20, 160, fill=(80, 50, 10), width=int(S * 0.02))
    d.ellipse([S * 0.25, S * 0.52, S * 0.45, S * 0.58], fill=_light(hi, 0.5))


def _draw_baggie(d, S, r, main, hi):
    """A little zip baggie with crystals inside. Strictly for comedy."""
    l, rt, top, bot = S * 0.24, S * 0.76, S * 0.2, S * 0.84
    d.rounded_rectangle([l, top, rt, bot], radius=S * 0.04, fill=(235, 240, 245), outline=(200, 205, 215), width=4)
    d.rectangle([l, top + S * 0.04, rt, top + S * 0.075], fill=(220, 40, 50))   # red zip strip
    for _ in range(9):
        x = r.uniform(l + S * 0.08, rt - S * 0.08)
        y = r.uniform(S * 0.5, bot - S * 0.08)
        _crystal(d, (x, y + S * 0.05), r.uniform(-0.6, 0.6), S * r.uniform(0.12, 0.2), S * 0.035, main, hi)
    d.polygon([(l + S * 0.04, top + S * 0.12), (l + S * 0.1, top + S * 0.12), (l + S * 0.06, bot - S * 0.1),
               (l + S * 0.03, bot - S * 0.1)], fill=(255, 255, 255))


def _draw_pile(d, S, r, main, hi, card=False):
    cx, base = S / 2, S * 0.78
    d.ellipse([S * 0.12, base - S * 0.06, S * 0.88, base + S * 0.06], fill=_dark(main, 0.45))
    d.pieslice([S * 0.14, S * 0.38, S * 0.86, base + S * 0.32], 180, 360, fill=main)
    d.pieslice([S * 0.22, S * 0.42, S * 0.62, base + S * 0.1], 190, 300, fill=_light(main, 0.3))
    for _ in range(60):
        a = r.uniform(math.pi, 2 * math.pi)
        rr = r.uniform(0, 1) ** 0.5
        x = cx + math.cos(a) * rr * S * 0.34
        y = base + math.sin(a) * rr * S * 0.36
        s = r.uniform(2, 6)
        d.ellipse([x - s, y - s, x + s, y + s], fill=hi if r.random() < 0.6 else (255, 255, 255))
    if not card:
        return
    # a credit card for the lols
    d.rounded_rectangle([S * 0.6, S * 0.72, S * 0.9, S * 0.86], radius=8, fill=(40, 90, 200))
    d.rectangle([S * 0.6, S * 0.75, S * 0.9, S * 0.775], fill=(20, 20, 30))


def _draw_shroom(d, S, r, main, hi):
    d.rounded_rectangle([S * 0.4, S * 0.45, S * 0.6, S * 0.85], radius=S * 0.06, fill=(245, 235, 215))
    d.rectangle([S * 0.43, S * 0.45, S * 0.48, S * 0.82], fill=(255, 250, 240))
    d.pieslice([S * 0.12, S * 0.16, S * 0.88, S * 0.76], 180, 360, fill=main)
    d.pieslice([S * 0.2, S * 0.2, S * 0.6, S * 0.6], 190, 260, fill=_light(main, 0.25))
    for _ in range(7):
        a = r.uniform(math.pi * 1.1, math.pi * 1.9)
        rr = r.uniform(0.3, 0.8)
        x, y = S / 2 + math.cos(a) * rr * S * 0.34, S * 0.46 + math.sin(a) * rr * S * 0.28
        s = S * r.uniform(0.03, 0.055)
        d.ellipse([x - s, y - s, x + s, y + s], fill=hi)
    # derpy face
    d.ellipse([S * 0.44, S * 0.56, S * 0.48, S * 0.61], fill=(30, 20, 20))
    d.ellipse([S * 0.52, S * 0.56, S * 0.56, S * 0.61], fill=(30, 20, 20))
    d.arc([S * 0.45, S * 0.6, S * 0.55, S * 0.68], 20, 160, fill=(30, 20, 20), width=4)


def _draw_pill(d, S, r, main, hi):
    for i, (x, y, a) in enumerate([(0.35, 0.4, -30), (0.62, 0.55, 20), (0.42, 0.7, 70)]):
        pill = Image.new("RGBA", (int(S * 0.5), int(S * 0.22)), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pill)
        w, h = pill.size
        pd.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=hi if i % 2 else main)
        pd.rounded_rectangle([w // 2, 0, w - 1, h - 1], radius=h // 2, fill=(255, 255, 255))
        pd.rectangle([w // 2, 0, w // 2 + h // 2, h - 1], fill=(255, 255, 255))
        pd.ellipse([w * 0.1, h * 0.2, w * 0.3, h * 0.45], fill=(255, 255, 255))
        pill = pill.rotate(a, expand=True, resample=Image.BICUBIC)
        d._image.paste(pill, (int(S * x - pill.width / 2), int(S * y - pill.height / 2)), pill)


def ore_image(ore, size=160):
    """Returns an RGBA image of the ore."""
    custom = os.path.join(CUSTOM, ore.key + ".png")
    if os.path.exists(custom):
        return Image.open(custom).convert("RGBA").resize((size, size), Image.LANCZOS)
    S = 512
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = random.Random(ore.key)
    main, hi = ore.colors
    if ore.shape == "orb":
        _draw_orb(img, d, S, r, main, hi)
    elif ore.shape == "pile":
        _draw_pile(d, S, r, main, hi, card=ore.tier == "contraband")
    else:
        {"chunk": _draw_chunk, "crystal": _draw_crystal, "gem": _draw_gem, "star": _draw_star,
         "crown": _draw_crown, "slug": _draw_slug, "baggie": _draw_baggie, "pile": _draw_pile,
         "shroom": _draw_shroom, "pill": _draw_pill}[ore.shape](d, S, r, main, hi)
    effect = ore.tier_info.effect
    if effect >= 1:
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        mask = img.split()[3].filter(ImageFilter.GaussianBlur(18))
        glow.paste(Image.new("RGBA", (S, S), hi + (255,)), (0, 0), mask)
        img = Image.alpha_composite(glow, img)
    return img.resize((size, size), Image.LANCZOS)


def ore_icon(ore):
    """PNG bytes of the ore icon, cached in assets/cache."""
    path = os.path.join(CACHE, f"ore_{ore.key}.png")
    custom = os.path.join(CUSTOM, ore.key + ".png")
    if not os.path.exists(path) or (os.path.exists(custom) and os.path.getmtime(custom) > os.path.getmtime(path)):
        ore_image(ore).save(path)
    with open(path, "rb") as f:
        return f.read()


_THUMBS = {}


def ore_thumb(ore):
    """Small, compressed ore picture for embed thumbnails (about 3x smaller upload than ore_icon)."""
    if ore.key not in _THUMBS:
        img = Image.open(io.BytesIO(ore_icon(ore))).convert("RGBA").resize((96, 96), Image.LANCZOS)
        img = img.quantize(colors=96, method=Image.Quantize.FASTOCTREE)
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        _THUMBS[ore.key] = buf.getvalue()
    return _THUMBS[ore.key]


# ---------------------------------------------------------------- reveal GIF

def _outlined(d, xy, text, size, fill, anchor="mm", stroke=4):
    d.text(xy, text, font=font(size), fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(0, 0, 0))


def _fit(text, max_w, size):
    while size > 12 and font(size).getlength(text) > max_w:
        size -= 2
    return size


_REVEALS = {}   # (ore, name) -> GIF bytes, so finding the same ore again doesn't re-render it


def safe_name(name, fallback="SOMEONE"):
    """Display names with fancy Unicode letters (𝓣𝓲𝓶, ｔｉｍ, emoji) come out as boxes in the GIF font.
    NFKC turns most styled letters back into plain ones; anything still undrawable is dropped."""
    plain = unicodedata.normalize("NFKC", name or "")
    plain = "".join(ch for ch in plain if " " <= ch <= "\u017f").strip()
    if sum(ch.isalnum() for ch in plain) >= 2:
        return plain
    fb = unicodedata.normalize("NFKC", fallback or "")
    fb = "".join(ch for ch in fb if " " <= ch <= "\u017f").strip()
    return fb if sum(ch.isalnum() for ch in fb) >= 2 else "SOMEONE"


def reveal_gif(ore, user_name, frames=None, mut=None, fallback=None):
    """Big special-effect GIF: '<USER> DUG UP A <TIER> <ORE>'. Loops seamlessly. Cached in memory.
    mut = a Mutation: shows the mutated ore with that mutation's colours and title instead.
    fallback = a plain name to use if user_name can't be drawn (their account username)."""
    user_name = safe_name(user_name, fallback)
    key = (ore.key, user_name, frames, mut.key if mut else None)
    if key in _REVEALS:
        _REVEALS[key] = _REVEALS.pop(key)          # mark as recently used
        return _REVEALS[key]
    gif = _render_reveal(ore, user_name, frames, mut)
    _REVEALS[key] = gif
    while len(_REVEALS) > 40:
        _REVEALS.pop(next(iter(_REVEALS)))
    return gif


def _render_reveal(ore, user_name, frames=None, mut=None):
    tier = ore.tier_info
    effect = tier.effect
    if mut:
        effect = max(effect, 4 if mut.announce else 2)
    W, H = 480, 330
    N = frames or (24 if effect <= 1 else 32)
    color = mut.color if mut else tier.color
    main, hi = ore.colors
    if mut:
        main, hi = _mix(main, mut.color, 0.5), _light(mut.color, 0.3)
    rnd = random.Random(ore.key + user_name + (mut.key if mut else ""))

    # static background: dark radial gradient tinted with the ore colour
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    bd = ImageDraw.Draw(bg)
    for i in range(30, 0, -1):
        t = i / 30
        c = _mix(_dark(main, 0.55), (8, 6, 12), t)
        bd.ellipse([W / 2 - W * 0.75 * t, H * 0.48 - H * 1.1 * t, W / 2 + W * 0.75 * t, H * 0.48 + H * 1.1 * t], fill=c)

    icon = mutated_image(ore, mut.key, 150) if mut else ore_image(ore, 150)
    rays = 12 if effect >= 2 else 8
    particles = [(rnd.uniform(0, 2 * math.pi), rnd.uniform(0, 1), rnd.uniform(2, 5), rnd.random()) for _ in range(40 if effect >= 2 else 18)]
    title = f"{tier.name.upper()} FIND!" if effect < 4 else "DIVINE FIND!!"
    if effect >= 3:
        title = ("ULTRA RARE " if effect == 3 else "") + title
    siren = effect >= 5 and not mut
    if siren:
        title = "!! CONTRABAND !!"
    who = f"{user_name.upper()} DUG UP"
    name = ore.name.upper()
    chance = f"1 in {ore.chance:,}"
    if mut:
        title = f"{mut.name.upper()} MUTATION!" + ("!" if mut.announce else "")
        name = f"{mut.name} {ore.name}".upper()
        chance = f"1 in {mut.chance:,} mutation · x{mut.mult} value"
    cx, cy = W / 2, H * 0.42

    out = []
    for f in range(N):
        t = f / N
        img = bg.copy()
        # rotating light rays
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        rot = 2 * math.pi * t / rays
        for k in range(rays):
            a = rot + 2 * math.pi * k / rays
            spread = math.pi / rays * 0.55
            if siren:
                rc = (255, 40, 40) if (k + (f // 4)) % 2 else (40, 80, 255)
            elif effect >= 4:
                rc = tuple(int(v * 255) for v in colorsys.hsv_to_rgb((k / rays + t) % 1, 0.6, 1))
            else:
                rc = hi if k % 2 else color
            ld.polygon([(cx, cy), (cx + math.cos(a - spread) * 600, cy + math.sin(a - spread) * 600),
                        (cx + math.cos(a + spread) * 600, cy + math.sin(a + spread) * 600)], fill=rc + (70,))
        # pulsing glow
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        gr = 70 + 15 * pulse
        ld.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=hi + (110,))
        layer = layer.filter(ImageFilter.GaussianBlur(4))
        img.paste(layer, (0, 0), layer)
        d = ImageDraw.Draw(img)
        # expanding shockwave rings for mythic and up
        if effect >= 3:
            for k in range(2):
                p = (t + k / 2) % 1
                rr = 40 + p * 260
                d.ellipse([cx - rr, cy - rr * 0.8, cx + rr, cy + rr * 0.8], outline=_mix(hi, (0, 0, 0), p), width=3)
        # sparkles flying outward
        for ang, off, spd, tw in particles:
            p = (off + t) % 1
            dist = 50 + p * 230
            x, y = cx + math.cos(ang) * dist, cy + math.sin(ang) * dist * 0.75
            s = spd * (1 - p) + 1
            col = _light(hi, 0.4) if (f + int(tw * 10)) % 3 else (255, 255, 255)
            d.polygon([(x, y - s * 2), (x + s * 0.6, y), (x, y + s * 2), (x - s * 0.6, y)], fill=col)
            d.polygon([(x - s * 2, y), (x, y + s * 0.6), (x + s * 2, y), (x, y - s * 0.6)], fill=col)
        # bobbing, pulsing ore
        sc = 1 + 0.07 * math.sin(2 * math.pi * t)
        sz = int(150 * sc)
        ic = icon.resize((sz, sz), Image.LANCZOS)
        bob = int(4 * math.sin(2 * math.pi * t))
        img.paste(ic, (int(cx - sz / 2), int(cy - sz / 2 + bob)), ic)
        # text
        if siren:
            tcol = (255, 70, 70) if (f // 4) % 2 else (90, 140, 255)
            # police light bar
            for side, col in ((0, (255, 30, 30)), (1, (30, 70, 255))):
                on = ((f // 4) + side) % 2 == 0
                x0 = 8 if side == 0 else W - 58
                d.rounded_rectangle([x0, 8, x0 + 50, 40], radius=8, fill=col if on else _dark(col, 0.7))
        elif effect >= 4:
            tcol = tuple(int(v * 255) for v in colorsys.hsv_to_rgb(t, 0.5, 1))
        else:
            tcol = _light(color, 0.15)
        _outlined(d, (W / 2, 26), title, _fit(title, W - (130 if siren else 20), 34), tcol)
        _outlined(d, (W / 2, H - 76), who, _fit(who, W - 20, 22), (255, 255, 255), stroke=3)
        _outlined(d, (W / 2, H - 46), name, _fit(name, W - 20, 32), _light(hi, 0.2))
        _outlined(d, (W / 2, H - 15), chance, 18, (255, 255, 255), stroke=3)
        out.append(img)

    buf = io.BytesIO()
    out[0].save(buf, "GIF", save_all=True, append_images=out[1:], duration=60, loop=0, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- lucky roll reel

def _prize_icon(d, kind, box, col):
    x0, y0, x1, y1 = box
    cx, cy, s = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2
    white = (255, 255, 255)
    if kind == "coin":
        d.ellipse([cx - s * .7, cy - s * .7, cx + s * .7, cy + s * .7], fill=(255, 200, 40), outline=(160, 110, 0), width=4)
        d.ellipse([cx - s * .45, cy - s * .45, cx + s * .45, cy + s * .45], outline=(200, 140, 0), width=3)
        d.text((cx, cy), "C", font=font(int(s * .8)), fill=(160, 110, 0), anchor="mm")
    elif kind == "clover":
        g = (60, 200, 80)
        for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            d.ellipse([cx + dx * s * .3 - s * .3, cy + dy * s * .3 - s * .3, cx + dx * s * .3 + s * .3, cy + dy * s * .3 + s * .3], fill=g)
        d.line([(cx, cy), (cx + s * .3, cy + s * .8)], fill=(30, 120, 40), width=5)
    elif kind == "bolt":
        pts = [(cx + s * .15, cy - s * .8), (cx - s * .45, cy + s * .1), (cx - s * .02, cy + s * .1),
               (cx - s * .2, cy + s * .8), (cx + s * .45, cy - s * .15), (cx + s * .02, cy - s * .15)]
        d.polygon(pts, fill=(255, 230, 60), outline=(150, 110, 0))
    elif kind == "pick":
        _pickaxe(d, cx, cy, s * 1.6, (190, 140, 255))
        d.text((cx + s * .5, cy + s * .55), "x2", font=font(int(s * .5)), fill=white, anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0))
    elif kind == "cash":
        d.rounded_rectangle([cx - s * .8, cy - s * .45, cx + s * .8, cy + s * .45], radius=6, fill=(110, 200, 90), outline=(40, 110, 40), width=3)
        d.text((cx, cy), "$", font=font(int(s * .8)), fill=(255, 230, 90), anchor="mm", stroke_width=2, stroke_fill=(40, 90, 30))
    elif kind == "dna":
        for i in range(9):
            t = i / 8
            y = cy - s * .8 + t * s * 1.6
            w = math.sin(t * math.pi * 2) * s * .5
            d.line([(cx - w, y), (cx + w, y)], fill=(180, 240, 255), width=3)
            d.ellipse([cx - w - 4, y - 4, cx - w + 4, y + 4], fill=(120, 220, 255))
            d.ellipse([cx + w - 4, y - 4, cx + w + 4, y + 4], fill=(255, 140, 220))
    elif kind == "star":
        d.polygon(_star_pts(cx, cy, s * .8, s * .35, 5), fill=(80, 255, 140), outline=(20, 120, 50))
    elif kind == "slug":
        sub = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
        _draw_slug(ImageDraw.Draw(sub), 200, None, (255, 140, 210), (255, 230, 250))
        return sub.resize((int(s * 1.8), int(s * 1.8)), Image.LANCZOS)
    return None


def _pickaxe(d, cx, cy, size, head):
    s = size / 2
    d.line([(cx - s * .6, cy + s * .6), (cx + s * .35, cy - s * .35)], fill=(140, 90, 40), width=max(3, int(size * .08)))
    d.arc([cx - s * .55, cy - s * .95, cx + s * .95, cy + s * .55], 200, 340, fill=head, width=max(4, int(size * .14)))


def _cell(prize, size=96):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=14, fill=_dark(prize.color, 0.55), outline=prize.color, width=4)
    pad = size * 0.2
    sub = _prize_icon(d, prize.icon, (pad, pad, size - pad, size - pad), prize.color)
    if sub is not None:
        img.paste(sub, (int(size / 2 - sub.width / 2), int(size / 2 - sub.height / 2)), sub)
    return img


def reel_gif(result):
    """CS-case style reel that decelerates and stops on `result`."""
    W, H, C, GAP = 480, 170, 96, 8
    step = C + GAP
    r = random.Random()
    weights = [p.weight for p in PRIZES]
    strip = r.choices(PRIZES, weights=weights, k=34)
    land = 30
    strip[land] = result
    cells = {p.key: _cell(p, C) for p in PRIZES}
    target = land * step + C / 2 - W / 2 + r.uniform(-C * 0.35, C * 0.35)
    final = land * step + C / 2 - W / 2
    N = 44                      # about 2.6s of spinning, so it's still going when a slow client finishes loading it
    frames, durs = [], []
    for f in range(N + 1):
        t = f / N
        e = 1 - (1 - t) ** 3
        off = target * e if f < N else final
        img = Image.new("RGB", (W, H), (22, 20, 30))
        d = ImageDraw.Draw(img)
        first = int(off // step)
        for i in range(max(0, first), min(len(strip), first + W // step + 2)):
            x = int(i * step - off)
            c = cells[strip[i].key]
            img.paste(c, (x, (H - C) // 2 - 10), c)
        if f == N:
            d.rounded_rectangle([W / 2 - C / 2 - 6, (H - C) / 2 - 16, W / 2 + C / 2 + 6, (H + C) / 2 - 4],
                                radius=16, outline=(255, 255, 255), width=4)
        d.polygon([(W / 2 - 10, 0), (W / 2 + 10, 0), (W / 2, 16)], fill=(255, 215, 60))
        d.polygon([(W / 2 - 10, H - 22), (W / 2 + 10, H - 22), (W / 2, H - 38)], fill=(255, 215, 60))
        label = result.name.split("  ")[0] if f == N else "LUCKY ROLL..."
        _outlined(d, (W / 2, H - 11), label, 18, result.color if f == N else (255, 255, 255), stroke=3)
        frames.append(img)
        durs.append(60 if f < N else 3000)
    buf = io.BytesIO()
    # no loop extension: plays once and stays on the result
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=durs, optimize=True)
    return buf.getvalue()


def prize_card(prize):
    W, H = 480, 170
    img = Image.new("RGB", (W, H), (22, 20, 30))
    d = ImageDraw.Draw(img)
    c = _cell(prize, 120)
    img.paste(c, (30, 25), c)
    title, _, desc = prize.name.partition("  ")
    _outlined(d, (170, 70), title.upper(), _fit(title.upper(), 290, 34), prize.color, anchor="lm")
    if desc:
        _outlined(d, (170, 112), desc, 22, (255, 255, 255), anchor="lm", stroke=3)
    return _png(img)


def pickaxe_icon(pick):
    path = os.path.join(CACHE, f"pick_{pick.key}.png")
    if not os.path.exists(path):
        S = 320
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        _pickaxe(ImageDraw.Draw(img), S / 2, S / 2, S * 0.8, pick.color)
        img.resize((128, 128), Image.LANCZOS).save(path)
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------- shop banner

SHOP_BG = os.path.join(ROOT, "assets", "shop_bg.png")   # swap this file to change the shop picture


_SHOP_BG_CACHE = {}


def _shop_bg():
    """The shop picture resized once and kept in memory (reloaded if you swap the file)."""
    mtime = os.path.getmtime(SHOP_BG) if os.path.exists(SHOP_BG) else 0
    if _SHOP_BG_CACHE.get("mtime") != mtime or "img" not in _SHOP_BG_CACHE:
        if mtime:
            img = Image.open(SHOP_BG).convert("RGB")
            img = img.resize((500, int(img.height * 500 / img.width)), Image.LANCZOS)
        else:
            img = Image.new("RGB", (500, 400), (25, 20, 35))
        _SHOP_BG_CACHE.update(mtime=mtime, img=img)
    return _SHOP_BG_CACHE["img"]


def shop_banner(coins, goals, pick=None):
    """The shop picture with a title on top and your coins, next pickaxe and goal bars along the bottom.
    goals: [(label, fraction)] from engine.goals()."""
    img = _shop_bg().copy()
    W, H = img.size
    # dark fades at top and bottom so the text reads on any picture
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    band = 70 + 34 * len(goals)
    for y in range(H):
        a = 0
        if y < 70:
            a = int(170 * (1 - y / 70))
        elif y > H - band - 40:
            a = int(225 * min(1, (y - (H - band - 40)) / 40))
        sd.line([(0, y), (W, y)], fill=a)
    img = Image.composite(Image.new("RGB", (W, H), (8, 6, 14)), img, shade)
    d = ImageDraw.Draw(img)
    _outlined(d, (W / 2, 30), "ASTRO TWEAKIN SHOP", 34, (120, 255, 140))
    y = H - band + 8
    _outlined(d, (16, y), f"{coins:,} coins", 24, (255, 215, 60), anchor="lm", stroke=3)
    if pick:
        ic = Image.open(io.BytesIO(pickaxe_icon(pick))).convert("RGBA").resize((64, 64), Image.LANCZOS)
        img.paste(ic, (W - 76, y - 34), ic)
    y += 30
    for label, frac in goals:
        # emoji don't render in the image font, so strip the leading icon
        text = label.split(" ", 1)[1] if " " in label else label
        _outlined(d, (16, y + 8), text, 18, (255, 255, 255), anchor="lm", stroke=2)
        x0, x1 = 250, W - 70
        d.rounded_rectangle([x0, y, x1, y + 16], radius=8, fill=(40, 40, 55), outline=(90, 90, 110), width=2)
        if frac > 0:
            d.rounded_rectangle([x0 + 2, y + 2, x0 + 2 + int((x1 - x0 - 4) * min(frac, 1)), y + 14], radius=6,
                                fill=(90, 230, 120) if frac >= 1 else (240, 190, 60))
        _outlined(d, (W - 16, y + 8), f"{int(frac * 100)}%", 16, (255, 255, 255), anchor="rm", stroke=2)
        y += 34
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


# ================================================================ v0.3.0: ships, cops, meteors, duels, Tim

def _space_bg(W, H, seed, top=(10, 8, 22), bottom=(24, 14, 40), stars=70):
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix(top, bottom, y / H))
    r = random.Random(seed)
    for _ in range(stars):
        x, y, b = r.uniform(0, W), r.uniform(0, H), r.randint(120, 255)
        s = r.choice([1, 1, 1, 2])
        d.ellipse([x - s / 2, y - s / 2, x + s / 2, y + s / 2], fill=(b, b, min(255, b + 20)))
    return img


def _draw_ship(layer, cx, cy, s, paint, hull=0, engine=0, stash=0, flame=0.0, police=False, siren_phase=0):
    """Side-view rocket facing right, drawn onto an RGBA layer. s = scale (1.0 is about 300px long)."""
    d = ImageDraw.Draw(layer)
    P = lambda x, y: (cx + x * s, cy + y * s)
    dark, light = _dark(paint, 0.4), _light(paint, 0.35)
    # engine flame (longer with a better engine)
    if flame >= 0:
        length = (40 + 18 * engine) * (0.85 + 0.3 * flame)
        for k, col in enumerate([(255, 90, 20), (255, 170, 40), (255, 245, 180)]):
            w = 26 - k * 8
            d.polygon([P(-150, -w), P(-150 - length * (1 - k * 0.25), 0), P(-150, w)], fill=col)
    # fins
    d.polygon([P(-120, -40), P(-160, -85), P(-80, -40)], fill=dark)
    d.polygon([P(-120, 40), P(-160, 85), P(-80, 40)], fill=dark)
    # nozzle
    d.rectangle([P(-160, -26), P(-140, 26)], fill=(70, 70, 80))
    # body + nose
    d.rounded_rectangle([P(-145, -45), P(95, 45)], radius=int(40 * s), fill=paint)
    d.polygon([P(80, -45), P(165, 0), P(80, 45)], fill=paint)
    d.polygon([P(80, 10), P(165, 0), P(80, 45)], fill=dark)
    d.rounded_rectangle([P(-145, 15), P(95, 45)], radius=int(25 * s), fill=dark)
    d.line([P(-120, -30), P(70, -30)], fill=light, width=max(2, int(5 * s)))
    # reinforced hull plates
    for i in range(hull):
        x = -110 + i * 38
        d.rectangle([P(x, -40), P(x + 30, 40)], outline=(200, 200, 215), width=max(1, int(3 * s)))
        for yy in (-34, 34):
            d.ellipse([P(x + 3, yy - 3), P(x + 9, yy + 3)], fill=(220, 220, 230))
    # hidden compartments (dashed hatches under the hull)
    for i in range(stash):
        x = -100 + i * 36
        for k in range(4):
            d.line([P(x + k * 7, 30), P(x + k * 7 + 4, 30)], fill=(30, 30, 30), width=max(1, int(2 * s)))
            d.line([P(x + k * 7, 42), P(x + k * 7 + 4, 42)], fill=(30, 30, 30), width=max(1, int(2 * s)))
    # window
    d.ellipse([P(35, -28), P(75, 12)], fill=(30, 40, 60), outline=(220, 220, 235), width=max(2, int(5 * s)))
    d.ellipse([P(43, -22), P(55, -10)], fill=(170, 230, 255))
    if police:
        for k, col in enumerate([(255, 40, 40), (40, 90, 255)]):
            on = (siren_phase + k) % 2 == 0
            c = col if on else _dark(col, 0.65)
            d.rounded_rectangle([P(-40 + k * 40, -66), P(-5 + k * 40, -45)], radius=int(6 * s), fill=c)
            if on:
                d.ellipse([P(-60 + k * 40, -95), P(15 + k * 40, -20)], outline=col + (120,) if layer.mode == "RGBA" else col, width=2)


def _ship_png(name, paint, hull, engine, stash, stamp=None, stamp_color=(255, 60, 60)):
    W, H = 480, 260
    img = _space_bg(W, H, name).convert("RGBA")
    layer = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    _draw_ship(layer, W * 1.05, H * 1.1, 1.75, paint, hull, engine, stash, flame=0.5)
    layer = layer.resize((W, H), Image.LANCZOS)
    img.alpha_composite(layer)
    d = ImageDraw.Draw(img)
    _outlined(d, (W / 2, 24), f"{name.upper()}'S SHIP", _fit(f"{name.upper()}'S SHIP", W - 20, 28), _light(paint, 0.3))
    if stamp:
        st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(st)
        size = _fit(stamp, W - 80, 64)
        sd.text((W / 2, H / 2 + 10), stamp, font=font(size), fill=stamp_color + (235,), anchor="mm",
                stroke_width=5, stroke_fill=(0, 0, 0, 255))
        st = st.rotate(12, resample=Image.BICUBIC)
        img.alpha_composite(st)
    return _png(img.convert("RGB"))


_SHIPS = {}


def ship_image(name, user, stamp=None, stamp_color=(255, 60, 60)):
    from .data import PAINTS
    paint = PAINTS[user["ship_paint"] % len(PAINTS)][1]
    key = (name, paint, user["ship_hull"], user["ship_engine"], user["ship_stash"], stamp, stamp_color)
    if key not in _SHIPS:
        if len(_SHIPS) > 100:
            _SHIPS.clear()
        _SHIPS[key] = _ship_png(*key)
    return _SHIPS[key]


def police_gif(top, bottom):
    """Space Authority cruiser with its lights going. top/bottom are the caption lines."""
    W, H, N = 480, 270, 12
    bg = _space_bg(W, H, "cops")
    out = []
    for f in range(N):
        phase = (f // 3) % 2
        img = bg.copy().convert("RGBA")
        tint = Image.new("RGBA", (W, H), ((255, 30, 30, 45) if phase else (30, 70, 255, 45)))
        img.alpha_composite(tint)
        layer = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        bob = 6 * math.sin(2 * math.pi * f / N)
        _draw_ship(layer, W * 0.95, H * 1.08 + bob, 1.6, (235, 238, 245), hull=2, flame=0.3 + 0.4 * (f % 2), police=True,
                   siren_phase=phase)
        layer = layer.resize((W, H), Image.LANCZOS)
        img.alpha_composite(layer)
        d = ImageDraw.Draw(img)
        d.text((W * 0.47, H * 0.56), "SPACE COPS", font=font(16), fill=(30, 50, 140), anchor="mm")
        tcol = (255, 70, 70) if phase else (110, 160, 255)
        _outlined(d, (W / 2, 28), top, _fit(top, W - 30, 36), tcol)
        _outlined(d, (W / 2, H - 24), bottom, _fit(bottom, W - 30, 24), (255, 255, 255), stroke=3)
        out.append(img.convert("RGB"))
    buf = io.BytesIO()
    out[0].save(buf, "GIF", save_all=True, append_images=out[1:], duration=90, loop=0, optimize=True)
    return buf.getvalue()


def meteor_gif():
    """A meteor screams in, slams into the ground and sits there glowing."""
    W, H = 480, 270
    bg = _space_bg(W, H, "meteor", top=(14, 8, 30), bottom=(60, 25, 40))
    g = ImageDraw.Draw(bg)
    g.polygon([(0, H), (0, H - 50), (90, H - 62), (200, H - 48), (320, H - 66), (W, H - 52), (W, H)], fill=(55, 40, 50))
    gx, gy = W * 0.5, H - 58
    sx, sy = W + 60, -60
    out, durs = [], []
    r = random.Random(7)
    sparks = [(r.uniform(-math.pi, 0), r.uniform(40, 120), r.random()) for _ in range(26)]
    FALL, GLOW = 12, 14
    for f in range(FALL + GLOW):
        img = bg.copy().convert("RGBA")
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        if f < FALL:
            t = (f + 1) / FALL
            x, y = sx + (gx - sx) * t, sy + (gy - sy) * t
            for k in range(14):
                tt = max(0, t - k * 0.025)
                tx, ty = sx + (gx - sx) * tt, sy + (gy - sy) * tt
                rr = 18 - k
                d.ellipse([tx - rr, ty - rr, tx + rr, ty + rr], fill=(255, 140 + k * 7, 40, 200 - k * 13))
            d.ellipse([x - 20, y - 20, x + 20, y + 20], fill=(110, 70, 60, 255))
            d.ellipse([x - 12, y - 14, x + 4, y - 2], fill=(160, 110, 90, 255))
        else:
            p = (f - FALL) / GLOW
            if f - FALL < 3:
                fl = 1 - (f - FALL) / 3
                d.ellipse([gx - 220 * (1 - fl * 0.3), gy - 150, gx + 220 * (1 - fl * 0.3), gy + 150],
                          fill=(255, 230, 160, int(200 * fl)))
            pulse = 0.5 + 0.5 * math.sin(2 * math.pi * p * 2)
            d.ellipse([gx - 70 - 10 * pulse, gy - 30, gx + 70 + 10 * pulse, gy + 26], fill=(255, 120, 30, 90))
            d.ellipse([gx - 60, gy - 14, gx + 60, gy + 16], fill=(35, 25, 30, 255))
            d.ellipse([gx - 26, gy - 34, gx + 26, gy + 10], fill=(110, 70, 60, 255))
            for k, (a, dist, o) in enumerate(sparks):
                q = (p + o) % 1
                x2, y2 = gx + math.cos(a) * dist * q, gy - 10 + math.sin(a) * dist * q
                s = 3 * (1 - q) + 1
                d.ellipse([x2 - s, y2 - s, x2 + s, y2 + s], fill=(255, 200 + int(55 * (1 - q)), 80, 255))
        layer = layer.filter(ImageFilter.GaussianBlur(1))
        img.alpha_composite(layer)
        dd = ImageDraw.Draw(img)
        _outlined(dd, (W / 2, 30), "METEOR STRIKE!", 38, (255, 170, 50))
        if f >= FALL:
            _outlined(dd, (W / 2, H - 20), "QUICK, MINE IT!", 22, (255, 255, 255), stroke=3)
        out.append(img.convert("RGB"))
        durs.append(55 if f < FALL else 110)
    buf = io.BytesIO()
    out[0].save(buf, "GIF", save_all=True, append_images=out[1:], duration=durs, loop=0, optimize=True)
    return buf.getvalue()


def duel_gif(name_a, name_b, paint_a, paint_b, rounds, winner):
    """Two ships trade laser fire round by round, then the loser blows up. Plays once."""
    W, H = 480, 270
    bg = _space_bg(W, H, name_a + name_b)

    def ships(hide=None, boom=0.0):
        layer = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        left = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        if hide != 0:
            _draw_ship(left, W * 0.42, H * 1.1, 0.95, paint_a, flame=0.5)
        layer.alpha_composite(left)
        right = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        if hide != 1:
            _draw_ship(right, W * 0.42, H * 1.1, 0.95, paint_b, flame=0.5)
        layer.alpha_composite(right.transpose(Image.FLIP_LEFT_RIGHT))
        return layer.resize((W, H), Image.LANCZOS)

    base = ships()
    xa, xb, y = W * 0.36, W * 0.64, H * 0.55
    frames, durs = [], []

    def frame(layer_fx=None, caption=None, hide=None, dur=80, boom_at=None, boom=0.0):
        img = bg.copy().convert("RGBA")
        img.alpha_composite(base if hide is None else ships(hide))
        if layer_fx:
            img.alpha_composite(layer_fx)
        d = ImageDraw.Draw(img)
        if boom_at is not None:
            bx = boom_at
            for k, col in enumerate([(255, 80, 20), (255, 170, 40), (255, 240, 180)]):
                rr = (70 - k * 20) * boom
                d.ellipse([bx - rr, y - rr, bx + rr, y + rr], fill=col)
        _outlined(d, (16, 22), name_a.upper(), _fit(name_a.upper(), W / 2 - 30, 22), _light(paint_a, 0.3), anchor="lm", stroke=3)
        _outlined(d, (W - 16, 22), name_b.upper(), _fit(name_b.upper(), W / 2 - 30, 22), _light(paint_b, 0.3), anchor="rm", stroke=3)
        _outlined(d, (W / 2, 22), "VS", 22, (255, 220, 80), stroke=3)
        if caption:
            _outlined(d, (W / 2, H - 22), caption, _fit(caption, W - 20, 26), (255, 255, 255), stroke=3)
        frames.append(img.convert("RGB"))
        durs.append(dur)

    frame(caption="DUEL!", dur=600)
    score = [0, 0]
    for i, (ra, rb) in enumerate(rounds):
        shooter = 0 if ra > rb else 1
        score[shooter] += 1
        x0, x1 = (xa + 40, xb - 40) if shooter == 0 else (xb - 40, xa + 40)
        col = (90, 255, 120) if shooter == 0 else (255, 90, 200)
        for k in range(6):
            fx = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(fx)
            t = (k + 1) / 6
            head = x0 + (x1 - x0) * t
            tail = x0 + (x1 - x0) * max(0, t - 0.35)
            d.line([(tail, y), (head, y)], fill=col + (255,), width=6)
            d.line([(tail, y), (head, y)], fill=(255, 255, 255, 255), width=2)
            if k == 5:
                d.ellipse([x1 - 18, y - 18, x1 + 18, y + 18], fill=(255, 220, 120, 230))
            frame(fx, caption=f"ROUND {i + 1}:  {ra} vs {rb}", dur=60)
        frame(caption=f"ROUND {i + 1}:  {ra} vs {rb}   ({score[0]}-{score[1]})", dur=500)
    lose_x = xb if winner == 0 else xa
    for k in range(6):
        frame(caption="KABOOM!", boom_at=lose_x, boom=(k + 1) / 6, dur=70, hide=1 - winner if k >= 3 else None)
    win_name = (name_a if winner == 0 else name_b).upper()
    frame(caption=f"{win_name} WINS!", hide=1 - winner, dur=5000)
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=durs, optimize=True)
    return buf.getvalue()


TIM_PIC = os.path.join(ROOT, "assets", "tim.png")   # swap this file to change Tweaker Tim's face


def tim_card(leaving=False):
    W, H = 480, 280
    img = Image.new("RGB", (W, H), (20, 10, 30))
    d = ImageDraw.Draw(img)
    for y in range(H):
        d.line([(0, y), (W, y)], fill=_mix((40, 10, 60), (10, 60, 40), y / H))
    if os.path.exists(TIM_PIC):
        pic = Image.open(TIM_PIC).convert("RGB")
        side = min(pic.size)
        pic = pic.crop(((pic.width - side) // 2, (pic.height - side) // 2,
                        (pic.width + side) // 2, (pic.height + side) // 2)).resize((230, 230), Image.LANCZOS)
        mask = Image.new("L", (230, 230), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, 229, 229], radius=30, fill=255)
        if leaving:
            pic = pic.convert("L").convert("RGB")
        img.paste(pic, (20, 25), mask)
        d.rounded_rectangle([18, 23, 252, 257], radius=32, outline=(120, 255, 140), width=4)
    tx = 368
    _outlined(d, (tx, 70), "TWEAKER", _fit("TWEAKER", W - 270, 40), (120, 255, 140))
    _outlined(d, (tx, 115), "TIM", 52, (120, 255, 140))
    line = "HAS LEFT" if leaving else "IS IN TOWN"
    _outlined(d, (tx, 170), line, 26, (255, 220, 80) if not leaving else (180, 180, 180), stroke=3)
    if not leaving:
        _outlined(d, (tx, 205), "10 MINUTES ONLY", 18, (255, 255, 255), stroke=2)
        _outlined(d, (tx, 232), "NO REFUNDS", 16, (255, 120, 120), stroke=2)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


# ================================================================ v0.4.0: mutated ores

def _glow(img, color, radius=10, strength=1.6):
    """Coloured glow around an RGBA image's shape, drawn underneath it."""
    a = img.split()[3].filter(ImageFilter.GaussianBlur(radius))
    a = a.point(lambda v: min(255, int(v * strength)))
    g = Image.new("RGBA", img.size, color + (0,))
    g.putalpha(a)
    return Image.alpha_composite(g, img)


def _sparkles(img, color, n, seed, scale=1.0):
    d = ImageDraw.Draw(img)
    r = random.Random(seed)
    S = img.width
    for _ in range(n):
        x, y = r.uniform(S * 0.15, S * 0.85), r.uniform(S * 0.15, S * 0.85)
        s = r.uniform(S * 0.03, S * 0.07) * scale
        d.polygon([(x, y - s * 2), (x + s * 0.5, y), (x, y + s * 2), (x - s * 0.5, y)], fill=color + (255,))
        d.polygon([(x - s * 2, y), (x, y + s * 0.5), (x + s * 2, y), (x, y - s * 0.5)], fill=color + (255,))
    return img


def mutate_image(img, mut_key, seed=""):
    """Applies a mutation look to an RGBA ore picture."""
    S = img.width
    rgb, alpha = img.convert("RGB"), img.split()[3]
    pad = int(S * 0.08)
    if mut_key == "shiny":
        out = Image.eval(rgb, lambda v: min(255, int(v * 1.25 + 25)))
        out.putalpha(alpha)
        out = _glow(out, (255, 230, 120), S // 22)
        return _sparkles(out, (255, 255, 220), 4, seed + "s")
    if mut_key == "radioactive":
        lum = rgb.convert("L")
        out = Image.merge("RGB", (lum.point(lambda v: int(v * 0.45)), lum.point(lambda v: min(255, int(v * 1.1 + 70))),
                                  lum.point(lambda v: int(v * 0.25))))
        out.putalpha(alpha)
        return _glow(out, (110, 255, 40), S // 14, 2.2)
    if mut_key == "shadow":
        lum = rgb.convert("L")
        out = Image.merge("RGB", (lum.point(lambda v: int(v * 0.25 + 10)), lum.point(lambda v: int(v * 0.15)),
                                  lum.point(lambda v: int(v * 0.35 + 20))))
        out.putalpha(alpha)
        out = _glow(out, (150, 60, 255), S // 18, 2.0)
        d = ImageDraw.Draw(out)
        for ex in (0.42, 0.58):                     # two little glowing eyes
            cx, cy, r = S * ex, S * 0.5, S * 0.025
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 60, 90, 255))
        return out
    if mut_key == "holographic":
        hsv = rgb.convert("HSV")
        h, s, v = hsv.split()
        grad = Image.linear_gradient("L").rotate(45, expand=False).resize((S, S))
        h = Image.eval(grad, lambda g: (g * 2) % 256)
        s = s.point(lambda x: max(140, x))
        v = v.point(lambda x: min(255, int(x * 0.8 + 70)))
        out = Image.merge("HSV", (h, s, v)).convert("RGB")
        out.putalpha(alpha)
        shine = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(shine).polygon([(S * 0.2, 0), (S * 0.35, 0), (S * 0.75, S), (S * 0.6, S)], fill=(255, 255, 255, 90))
        shine.putalpha(Image.composite(shine.split()[3], Image.new("L", (S, S), 0), alpha))
        out = Image.alpha_composite(out, shine)
        return _glow(out, (140, 220, 255), S // 20)
    if mut_key == "galactic":
        sky = Image.new("RGB", (S, S))
        d = ImageDraw.Draw(sky)
        for y in range(S):
            d.line([(0, y), (S, y)], fill=_mix((20, 10, 60), (110, 30, 140), y / S))
        r = random.Random(seed + "g")
        for _ in range(S // 3):
            x, y, b = r.uniform(0, S), r.uniform(0, S), r.randint(150, 255)
            sz = r.choice([1, 1, 2]) * S / 160
            d.ellipse([x - sz, y - sz, x + sz, y + sz], fill=(b, b, 255))
        shade = rgb.convert("L").point(lambda v: int(120 + v * 0.55))
        out = Image.composite(sky, Image.new("RGB", (S, S)), shade)
        out = Image.blend(out, sky, 0.35)
        out.putalpha(alpha)
        return _sparkles(_glow(out, (130, 90, 255), S // 14, 2.0), (255, 255, 255), 3, seed + "gs", 0.7)
    if mut_key == "tweaked":
        inv = Image.eval(rgb, lambda v: 255 - v).convert("HSV")
        h, s, v = inv.split()
        h = h.point(lambda x: (x + 170) % 256)
        s = s.point(lambda x: max(170, x))
        out = Image.merge("HSV", (h, s, v)).convert("RGB")
        out.putalpha(alpha)
        out = out.rotate(-12, resample=Image.BICUBIC)
        out = _glow(out, (255, 40, 200), S // 12, 2.4)
        return _sparkles(out, (120, 255, 240), 5, seed + "t", 0.8)
    return img


def mutated_image(ore, mut_key, size=160):
    big = ore_image(ore, 320)
    return mutate_image(big, mut_key, ore.key).resize((size, size), Image.LANCZOS)


def mutated_icon(ore, mut_key):
    path = os.path.join(CACHE, f"ore_{ore.key}_{mut_key}.png")
    if not os.path.exists(path):
        mutated_image(ore, mut_key).save(path)
    with open(path, "rb") as f:
        return f.read()


def mutated_thumb(ore, mut_key):
    k = (ore.key, mut_key)
    if k not in _THUMBS:
        img = Image.open(io.BytesIO(mutated_icon(ore, mut_key))).convert("RGBA").resize((96, 96), Image.LANCZOS)
        buf = io.BytesIO()
        img.quantize(colors=128, method=Image.Quantize.FASTOCTREE).save(buf, "PNG", optimize=True)
        _THUMBS[k] = buf.getvalue()
    return _THUMBS[k]


# ================================================================ v0.5.0: Asteroid Run

def _rock(d, cx, cy, r, seed):
    rr = random.Random(seed)
    pts = []
    for k in range(9):
        a = 2 * math.pi * k / 9 + rr.uniform(-0.2, 0.2)
        rad = r * rr.uniform(0.75, 1.05)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=(105, 95, 90), outline=(40, 35, 35), width=3)
    for _ in range(3):
        x, y, s = cx + rr.uniform(-r / 2, r / 2), cy + rr.uniform(-r / 2, r / 2), rr.uniform(r * 0.12, r * 0.25)
        d.ellipse([x - s, y - s, x + s, y + s], fill=(75, 68, 65))


def _chunk(img, cx, cy, r, t):
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    g = r * (1.5 + 0.2 * math.sin(t))
    gd.ellipse([cx - g, cy - g, cx + g, cy + g], fill=(255, 210, 80, 90))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(img)
    d.polygon([(cx, cy - r), (cx + r * 0.7, cy - r * 0.1), (cx + r * 0.4, cy + r * 0.8), (cx - r * 0.4, cy + r * 0.8),
               (cx - r * 0.7, cy - r * 0.1)], fill=(255, 200, 60), outline=(120, 70, 0), width=3)
    d.polygon([(cx, cy - r), (cx + r * 0.7, cy - r * 0.1), (cx, cy + r * 0.1)], fill=(255, 240, 170))


def _rocket_top(d, cx, cy, s, paint, flame=True):
    """Small rocket seen from above, nose pointing up."""
    dark = _dark(paint, 0.4)
    if flame:
        d.polygon([(cx - 8 * s, cy + 22 * s), (cx, cy + 40 * s), (cx + 8 * s, cy + 22 * s)], fill=(255, 150, 40))
        d.polygon([(cx - 4 * s, cy + 22 * s), (cx, cy + 32 * s), (cx + 4 * s, cy + 22 * s)], fill=(255, 240, 170))
    d.polygon([(cx - 22 * s, cy + 22 * s), (cx - 10 * s, cy), (cx - 10 * s, cy + 22 * s)], fill=dark)
    d.polygon([(cx + 22 * s, cy + 22 * s), (cx + 10 * s, cy), (cx + 10 * s, cy + 22 * s)], fill=dark)
    d.rounded_rectangle([cx - 11 * s, cy - 12 * s, cx + 11 * s, cy + 24 * s], radius=int(9 * s), fill=paint,
                        outline=(20, 20, 30), width=2)
    d.polygon([(cx - 11 * s, cy - 8 * s), (cx, cy - 30 * s), (cx + 11 * s, cy - 8 * s)], fill=paint, outline=(20, 20, 30))
    d.ellipse([cx - 6 * s, cy - 8 * s, cx + 6 * s, cy + 4 * s], fill=(170, 230, 255), outline=(230, 230, 240), width=2)


def asteroid_board(run, paint=(220, 60, 60)):
    """The Asteroid Run screen: the falling field, your rocket at the bottom and a distance bar."""
    from .data import ASTEROID_LANES, ASTEROID_ROWS, ASTEROID_MOVES
    cell, top, side = 62, 46, 24
    W, H = side * 2 + cell * ASTEROID_LANES, top + cell * ASTEROID_ROWS + 10
    img = _space_bg(W, H, f"ar{run.seed}{run.moves}", stars=90).convert("RGBA")
    d = ImageDraw.Draw(img)
    for c in range(1, ASTEROID_LANES):
        x = side + c * cell
        for y in range(top, H - 10, 14):
            d.line([(x, y), (x, y + 5)], fill=(60, 55, 90), width=1)
    left = ASTEROID_MOVES - run.moves
    if left <= ASTEROID_ROWS - 1 and run.state != "crashed":
        # Tim's station comes into view near the end
        y = top + (ASTEROID_ROWS - 1 - left) * cell
        d.rounded_rectangle([side, y - 18, W - side, y + 6], radius=10, fill=(70, 200, 110), outline=(20, 80, 40), width=3)
        d.text((W / 2, y - 6), "TIM'S STATION", font=font(16), fill=(10, 40, 20), anchor="mm")
    for r, row in enumerate(run.grid):
        for c, what in enumerate(row):
            cx, cy = side + c * cell + cell / 2, top + r * cell + cell / 2
            if what == "rock":
                _rock(d, cx, cy, cell * 0.38, f"{run.seed}:{run.moves - r}:{c}")
            elif what == "chunk":
                _chunk(img, cx, cy, cell * 0.24, run.moves + c)
                d = ImageDraw.Draw(img)
    sx, sy = side + run.lane * cell + cell / 2, top + (ASTEROID_ROWS - 1) * cell + cell / 2 - 4
    if run.state == "crashed":
        for k, (col, rad) in enumerate([((255, 80, 20), 34), ((255, 170, 40), 24), ((255, 245, 180), 12)]):
            pts = [(sx + rad * (1.0 if i % 2 else 0.5) * math.cos(math.pi * i / 6),
                    sy + rad * (1.0 if i % 2 else 0.5) * math.sin(math.pi * i / 6)) for i in range(12)]
            d.polygon(pts, fill=col)
    else:
        _rocket_top(d, sx, sy, 1.0, paint, flame=True)
    # header: distance bar + haul
    d.rectangle([0, 0, W, top - 6], fill=(12, 10, 26))
    frac = run.moves / ASTEROID_MOVES
    d.rounded_rectangle([side, 12, W - 150, 30], radius=9, fill=(40, 40, 55), outline=(90, 90, 110), width=2)
    if frac > 0:
        d.rounded_rectangle([side + 2, 14, side + 2 + int((W - 150 - side - 4) * frac), 28], radius=7, fill=(90, 230, 120))
    _outlined(d, (W - 16, 21), f"ORE x{len(run.ores)}", 20, (255, 210, 80), anchor="rm", stroke=3)
    stamp = {"crashed": ("CRASHED!", (255, 70, 50)), "docked": ("DOCKED!", (90, 240, 130)),
             "bailed": ("BAILED OUT", (255, 220, 80))}.get(run.state)
    if stamp:
        st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(st)
        sd.text((W / 2, H / 2), stamp[0], font=font(_fit(stamp[0], W - 60, 64)), fill=stamp[1] + (240,), anchor="mm",
                stroke_width=5, stroke_fill=(0, 0, 0, 255))
        img.alpha_composite(st.rotate(10, resample=Image.BICUBIC))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


# ================================================================ v0.6.0: depth banner on the /mine panel

_DEPTH = {}


def depth_banner(layer_num):
    """The picture at the bottom of /mine: a slice through the planet you're on, with your layer lit up
    and a depth gauge for the whole universe. One JPEG per layer, cached, so it's only drawn once."""
    if layer_num in _DEPTH:
        return _DEPTH[layer_num]
    from .data import LAYERS
    L = LAYERS[layer_num - 1]
    same = [l for l in LAYERS if l.planet == L.planet]
    W, H = 600, 150
    img = _space_bg(W, H, "depth" + L.planet, stars=50)
    d = ImageDraw.Draw(img)
    sky = 34
    band_h = (H - sky) / len(same)
    r = random.Random(L.planet)
    # surface line
    surf = [(x, sky + math.sin(x / 37 + len(L.planet)) * 3) for x in range(0, W + 10, 10)]
    d.polygon(surf + [(W, H), (0, H)], fill=_dark(same[0].color, 0.35))
    for i, l in enumerate(same):
        y0, y1 = sky + i * band_h, sky + (i + 1) * band_h
        here = l.num == layer_num
        col = l.color if here else _dark(l.color, 0.5)
        d.rectangle([0, y0 + 2, W, y1], fill=_dark(col, 0.25 if here else 0.1))
        for _ in range(int(W / 9)):
            x, y = r.uniform(0, W), r.uniform(y0 + 4, y1 - 2)
            s = r.uniform(1.5, 4.5)
            d.ellipse([x - s, y - s * .7, x + s, y + s * .7], fill=_light(col, 0.15) if here else _dark(col, 0.45))
        d.line([(0, y0 + 2), (W, y0 + 2)], fill=_dark(col, 0.6), width=2)
        if here:
            d.rectangle([0, y0 + 2, W - 1, y1 - 1], outline=_light(col, 0.4), width=3)
            # a little miner with a pickaxe
            mx, my = 70, (y0 + y1) / 2 + 2
            d.ellipse([mx - 14, my - 8, mx + 14, my + 10], fill=(150, 220, 110), outline=(40, 80, 30), width=2)
            d.ellipse([mx + 6, my - 4, mx + 10, my], fill=(20, 20, 20))
            _pickaxe(d, mx + 22, my - 10, 30, (200, 200, 220))
            _outlined(d, (110, (y0 + y1) / 2), l.name.upper(), _fit(l.name.upper(), 330, 26), _light(col, 0.5), anchor="lm", stroke=3)
        elif band_h >= 22:
            _outlined(d, (110, (y0 + y1) / 2), l.name, 15, _light(col, 0.2), anchor="lm", stroke=2)
    _outlined(d, (12, sky / 2), L.planet_info.name.upper(), 20, (255, 255, 255), anchor="lm", stroke=3)
    # universe depth gauge on the right
    gx, gy0, gy1 = W - 46, 12, H - 12
    d.rounded_rectangle([gx - 12, gy0 - 4, gx + 12, gy1 + 4], radius=10, fill=(15, 12, 25), outline=(90, 90, 120), width=2)
    step = (gy1 - gy0) / len(LAYERS)
    for l in LAYERS:
        y = gy0 + (l.num - 1) * step
        on = l.num <= layer_num
        d.rectangle([gx - 7, y + 1, gx + 7, y + step - 1], fill=l.color if on else (40, 38, 55))
    y = gy0 + (layer_num - 0.5) * step
    d.polygon([(gx - 22, y - 6), (gx - 14, y), (gx - 22, y + 6)], fill=(255, 215, 60))
    _outlined(d, (gx - 26, H - 14), f"DEPTH {layer_num}/{len(LAYERS)}", 15, (255, 215, 60), anchor="rm", stroke=2)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=82)
    _DEPTH[layer_num] = buf.getvalue()
    return _DEPTH[layer_num]
