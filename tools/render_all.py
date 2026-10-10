"""Renders every ore icon, a few reveal GIFs and a reel into assets/preview so you can look at the art.

    python -m tools.render_all
"""
import os
from PIL import Image
from game import images
from game.data import ORES, PRIZES, PICKAXES

out = os.path.join(images.ROOT, "assets", "preview")
os.makedirs(out, exist_ok=True)

# contact sheet of every ore
cols = 10
rows = (len(ORES) + cols - 1) // cols
sheet = Image.new("RGB", (cols * 170, rows * 200), (25, 22, 30))
from PIL import ImageDraw
d = ImageDraw.Draw(sheet)
for i, ore in enumerate(ORES):
    x, y = (i % cols) * 170, (i // cols) * 200
    ic = images.ore_image(ore)
    sheet.paste(ic, (x + 5, y + 5), ic)
    d.text((x + 85, y + 172), ore.name, font=images.font(14), fill=ore.tier_info.color, anchor="mm")
    d.text((x + 85, y + 190), f"1 in {ore.chance:,}", font=images.font(12), fill=(200, 200, 200), anchor="mm")
sheet.save(os.path.join(out, "ores.png"))

for key in ["diamond", "sunshard", "aurorite", "golden_slug", "space_pez", "blue_crystal"]:
    ore = next(o for o in ORES if o.key == key)
    with open(os.path.join(out, f"reveal_{key}.gif"), "wb") as f:
        f.write(images.reveal_gif(ore, "Slugs"))
with open(os.path.join(out, "reel.gif"), "wb") as f:
    f.write(images.reel_gif(PRIZES[-1]))
with open(os.path.join(out, "prize.png"), "wb") as f:
    f.write(images.prize_card(PRIZES[-1]))
for p in PICKAXES:
    images.pickaxe_icon(p)
print("wrote previews to", out)
