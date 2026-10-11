"""Makes ore icons with the local ComfyUI (LOCALAI) instead of the drawn ones.

ComfyUI has to be running first (LOCALAI\\Start-ComfyUI.bat or "Start AI Studio.bat").

    python -m tools.ai_ore_icons                  # 2 versions of every ore -> assets/ore_drafts
    python -m tools.ai_ore_icons diamond sunshard # only these ores
    python -m tools.ai_ore_icons --tries 4        # more versions to pick from
    python -m tools.ai_ore_icons --keep           # copy version 1 of each draft into assets/ores (the game uses those)

Drafts go to assets/ore_drafts/<ore>_<n>.png plus a contact sheet (_sheet.png) so you can pick.
The game uses any assets/ores/<ore>.png it finds instead of drawing the ore.
"""
import io
import json
import os
import random
import shutil
import sys
import time
import uuid

import requests
from PIL import Image, ImageDraw

from game import images
from game.data import ORES, ORE_BY_KEY

COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
WORKFLOW = os.environ.get("COMFY_ICON_WORKFLOW",
                          os.path.join(os.path.dirname(images.ROOT), "LOCALAI", "workflows", "api", "icon-transparent.api.json"))
DRAFTS = os.path.join(images.ROOT, "assets", "ore_drafts")

STYLE = ("Game item icon: {desc}, centered, single object filling most of the frame, stylized hand-painted cartoon "
         "mobile game style, bold clean dark outline, vibrant saturated colours, soft rim light, {extra}"
         "plain flat white background, no text, no border")

TIER_EXTRA = {
    "common": "",
    "uncommon": "",
    "rare": "slight shine, ",
    "epic": "shiny with a few sparkles, ",
    "legendary": "glowing with a bright magical aura and sparkles, ",
    "mythic": "radiant glowing aura, floating sparkles, magical energy, ",
    "celestial": "cosmic glow, tiny stars swirling around it, divine light, ",
    "divine": "blinding holy glow, golden light rays, swirling stars, ultimate legendary treasure, ",
    "contraband": "shady and sketchy, ",
}

# What each ore looks like. Ores missing here fall back to "<name>, a <shape>".
DESC = {
    "stone": "a plain grey stone rock chunk",
    "granite": "a speckled pinkish-brown granite rock chunk",
    "basalt": "a dark charcoal basalt rock chunk with hexagonal columns",
    "abyssal_shale": "a deep navy blue layered shale rock chunk from the abyss",
    "slugstone": "a lime green slimy rock chunk with slug slime trails",
    "coal": "a lump of black coal",
    "copper": "a raw copper ore nugget, orange metallic veins in rock",
    "tin": "a pale silvery tin ore chunk",
    "iron": "a rusty brown iron ore chunk with metal flecks",
    "glowcap": "a cluster of teal glowing crystals",
    "magmite": "a chunk of molten orange lava rock with glowing cracks",
    "deepglass": "a dark blue glassy crystal shard",
    "slime_pearl": "a round glossy lime green slime pearl",
    "silver": "a shiny silver ore nugget",
    "gold": "a shiny gold nugget",
    "quartz": "a cluster of clear white quartz crystals",
    "jade": "a polished green jade gemstone",
    "amethyst": "a cluster of purple amethyst crystals",
    "sapphire": "a cut deep blue sapphire gem",
    "ruby": "a cut red ruby gem",
    "emerald": "a cut green emerald gem",
    "diamond": "a brilliant cut sparkling ice-blue diamond",
    "moonstone": "a smooth pale lavender moonstone orb with a soft inner shimmer",
    "obsidian_heart": "a heart-shaped black-purple obsidian gem",
    "sunshard": "a jagged star-shaped shard of the sun, bright orange and yellow, burning",
    "slugheart_opal": "a round mint green opal with a glowing slug-shaped heart inside",
    "voidstone": "a pitch black orb with a purple void swirling inside",
    "aurorite": "a crystal cluster glowing with northern lights, green and teal aurora colours",
    "prismara": "a hot pink rainbow prism gem splitting light into rainbows",
    "chrono_crystal": "a golden crystal with a tiny clock face frozen inside",
    "nebulite": "a purple orb containing a swirling galaxy nebula",
    "starforged_core": "a blazing white-gold star core forged from a dying star",
    "slug_kings_crown": "a golden royal crown with gems, topped with a little slug",
    "eternium": "a pure white eternal star gem of infinite light",
    "golden_slug": "a cute shiny solid gold slug with big eyes",
    "blue_crystal": "a small plastic zip baggie full of glowing blue crystal shards",
    "moon_dust": "a chunk of pale grey cratered moon rock covered in dust",
    "dark_regolith": "a dark grey lunar regolith rock chunk",
    "helium3": "a glowing light blue bubble orb of helium-3 gas",
    "moon_cheese": "a wedge of yellow cheese with craters like the moon",
    "lunar_titanium": "a bluish-grey titanium metal ore chunk",
    "flag_scrap": "a torn scrap of a red white and blue flag on a broken metal pole",
    "selenite": "a pale white translucent selenite crystal wand",
    "lunar_pearl": "a large glowing pearly white pearl shaped like a full moon",
    "moonstone_prime": "a cut periwinkle blue gem with a crescent moon inside",
    "moon_rocks": "a small plastic baggie of white chunky rocks with a crescent moon sticker",
    "red_dust": "a pile of rusty red martian dust on a rock",
    "oxide_crust": "a crusty dark red iron oxide rock chunk",
    "rust_nugget": "a rusty orange metal nugget",
    "martian_ice": "a pale blue ice crystal chunk from mars",
    "rover_parts": "a pile of broken robot rover parts: a wheel, bolts and a little antenna",
    "phobos_shard": "a lumpy grey potato-shaped moon rock shard",
    "olympus_ruby": "a large cut blood red ruby shaped like a volcano",
    "red_giant_garnet": "a deep crimson garnet gem glowing like a red giant star",
    "martian_fossil": "a bone-coloured fossil of a weird alien creature in sandstone",
    "k_dust": "a pile of white powder on a credit card with a little red mars planet logo",
    "spore_soil": "a clump of dark purple soil with tiny mushrooms sprouting",
    "mycelium_mat": "a fuzzy cream white mat of fungus mycelium",
    "glowcap_spores": "a pile of glowing teal mushroom spores",
    "funky_fungus": "a funky orange mushroom with groovy spots",
    "truffle": "a lumpy brown truffle mushroom with gold flecks",
    "spore_sapphire": "a blue sapphire gem with tiny mushrooms growing from it",
    "bouncy_cap": "a bouncy hot pink jelly mushroom",
    "mushroom_kings_cap": "a red royal mushroom with white spots wearing a tiny golden crown",
    "mycelium_heart": "a glowing mint green orb of tangled mycelium shaped like a heart",
    "cosmic_shroom": "a trippy psychedelic purple mushroom with swirling galaxy patterns",
    "sugar_sand": "a pile of sparkly white sugar crystals",
    "rock_candy": "a pink rock candy crystal cluster",
    "gumdrops": "a few green sugar-coated gumdrop candies",
    "jawbreaker": "a round red jawbreaker candy ball",
    "lollipop_crystal": "a hot pink crystal shaped like a swirly lollipop",
    "taffy_ore": "a twisted chunk of light blue saltwater taffy",
    "cotton_cloud": "a fluffy pink cotton candy cloud",
    "gobstopper": "a huge purple galaxy gobstopper candy with swirling layers",
    "sugar_star": "a glowing yellow sugar candy star",
    "space_pez": "a pez candy dispenser with an alien head, pink candy tablets",
    "void_ash": "a pile of dark purple-grey ash from the void",
    "dark_matter": "a black orb of dark matter with faint purple distortion around it",
    "antimatter": "a pure white crackling energy orb of antimatter",
    "entropy_crystal": "a dark purple crystal cracking and crumbling apart",
    "singularity_shard": "a jet black gem shard with light bending around it",
    "horizon_opal": "an orange opal orb with a black hole event horizon glowing inside",
    "stardust": "a pile of sparkling pure white glittering stardust powder",
    "nothing": "an empty dark circle of literally nothing, a tiny dusty void",
    "eye_of_void": "a giant cosmic eye in a black void orb, staring, purple and gold iris",
    "last_gram": "a tiny plastic baggie with a few glowing white sparkles, the last one in the universe",
}


def prompt_for(ore):
    desc = DESC.get(ore.key, f"{ore.name}, a {ore.shape}")
    return STYLE.format(desc=desc, extra=TIER_EXTRA.get(ore.tier, ""))


def load_workflow():
    with open(WORKFLOW) as f:
        return json.load(f)


def queue(wf, text, seed, prefix):
    wf = json.loads(json.dumps(wf))
    for node in wf.values():
        c, i = node["class_type"], node["inputs"]
        if c == "CLIPTextEncode":
            i["text"] = text
        elif c == "KSampler":
            i["seed"] = seed
        elif c == "EmptySD3LatentImage":
            i["width"] = i["height"] = 1024
        elif c == "SaveImage":
            i["filename_prefix"] = prefix
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait_for(pid, timeout=600):
    end = time.time() + timeout
    while time.time() < end:
        h = requests.get(f"{COMFY}/history/{pid}", timeout=30).json()
        if pid in h:
            if h[pid].get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(h[pid]["status"])[:500])
            for out in h[pid]["outputs"].values():
                for im in out.get("images", []):
                    if im.get("type") == "output":
                        r = requests.get(f"{COMFY}/view", params=im, timeout=60)
                        r.raise_for_status()
                        return Image.open(io.BytesIO(r.content)).convert("RGBA")
        time.sleep(1)
    raise TimeoutError(pid)


def tidy(img, size=512):
    """Crops to the ore itself and centres it on a square, see-through canvas."""
    box = img.split()[3].point(lambda v: 255 if v > 24 else 0).getbbox() or (0, 0) + img.size
    img = img.crop(box)
    side = int(max(img.size) * 1.08)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return sq.resize((size, size), Image.LANCZOS)


def sheet(keys, tries):
    cell = 200
    out = Image.new("RGB", (cell * (tries + 1), cell * len(keys)), (25, 22, 30))
    d = ImageDraw.Draw(out)
    for row, key in enumerate(keys):
        y = row * cell
        old = images.ore_image(ORE_BY_KEY[key], 180) if not os.path.exists(os.path.join(images.CUSTOM, key + ".png")) else None
        if old:
            out.paste(old, (10, y + 10), old)
        d.text((cell // 2, y + 190), "drawn" if old else "current", font=images.font(13), fill=(200, 200, 200), anchor="mm")
        for n in range(1, tries + 1):
            p = os.path.join(DRAFTS, f"{key}_{n}.png")
            if os.path.exists(p):
                im = Image.open(p).convert("RGBA").resize((180, 180), Image.LANCZOS)
                out.paste(im, (n * cell + 10, y + 10), im)
            d.text((n * cell + cell // 2, y + 190), f"{key}_{n}", font=images.font(13), fill=(200, 200, 200), anchor="mm")
    out.save(os.path.join(DRAFTS, "_sheet.png"))


def main(argv):
    tries = 2
    if "--tries" in argv:
        i = argv.index("--tries")
        tries = int(argv[i + 1])
        del argv[i:i + 2]
    keep = "--keep" in argv
    keys = [a for a in argv if not a.startswith("--")] or [o.key for o in ORES]
    bad = [k for k in keys if k not in ORE_BY_KEY]
    if bad:
        sys.exit(f"Unknown ore: {', '.join(bad)}")
    os.makedirs(DRAFTS, exist_ok=True)

    if keep:
        os.makedirs(images.CUSTOM, exist_ok=True)
        for k in keys:
            src = os.path.join(DRAFTS, f"{k}_1.png")
            if os.path.exists(src):
                shutil.copy(src, os.path.join(images.CUSTOM, k + ".png"))
                print("kept", k)
        return

    requests.get(f"{COMFY}/system_stats", timeout=5).raise_for_status()
    wf = load_workflow()
    for k in keys:
        ore = ORE_BY_KEY[k]
        for n in range(1, tries + 1):
            path = os.path.join(DRAFTS, f"{k}_{n}.png")
            if os.path.exists(path):
                continue
            t = time.time()
            pid = queue(wf, prompt_for(ore), random.randint(1, 2**31), f"astro_tweakin/{k}")
            tidy(wait_for(pid)).save(path)
            print(f"{k}_{n}  {time.time() - t:.0f}s", flush=True)
    sheet(keys, tries)
    print("done, contact sheet:", os.path.join(DRAFTS, "_sheet.png"))


if __name__ == "__main__":
    main(sys.argv[1:])
