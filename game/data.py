"""All the static game content: tiers, ores, realms, planets, layers, pickaxes, parts and lucky-roll prizes.

Chances are written as "1 in N". Tweak numbers here to rebalance the game.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Tier:
    name: str
    color: tuple          # RGB used for embeds and effects
    effect: int           # 0 none, 1 small flash, 2 full reveal GIF, 3 huge, 4 insane
    announce: bool        # post to the server announce channel


TIERS = {
    "common":    Tier("Common",    (158, 158, 158), 0, False),
    "uncommon":  Tier("Uncommon",  (88, 200, 98),   0, False),
    "rare":      Tier("Rare",      (66, 135, 245),  0, False),
    "epic":      Tier("Epic",      (170, 85, 240),  1, False),
    "legendary": Tier("Legendary", (255, 190, 40),  2, True),
    "mythic":    Tier("Mythic",    (255, 70, 120),  3, True),
    "celestial": Tier("Celestial", (80, 230, 255),  3, True),
    "divine":    Tier("Divine",    (255, 255, 255), 4, True),
    "contraband": Tier("Contraband", (60, 255, 120), 5, True),   # each planet's joke drug. police-siren reveal
}


@dataclass(frozen=True)
class Ore:
    key: str
    name: str
    chance: int           # 1 in N
    value: int            # coins when sold
    tier: str
    colors: tuple         # (main, highlight) RGB
    shape: str            # chunk, crystal, gem, orb, crown, slug, star, baggie, pile, shroom, pill
    layers: tuple         # layers this ore can drop in

    @property
    def tier_info(self) -> Tier:
        return TIERS[self.tier]


ALL = (1, 2, 3, 4, 5)          # every layer of Slug Earth
def _from(n):
    return tuple(range(n, 6))
MOON, MARS, SHROOM, CANDY = (6, 7), (8, 9), (10, 11), (12, 13)


ORES = [
    # base ore for each layer (chance 1 = what you get when nothing rarer hits)
    Ore("stone", "Stone", 1, 1, "common", ((120, 120, 125), (170, 170, 175)), "chunk", (1,)),
    Ore("granite", "Granite", 1, 3, "common", ((150, 110, 100), (200, 160, 150)), "chunk", (2,)),
    Ore("basalt", "Basalt", 1, 8, "common", ((60, 55, 60), (110, 100, 105)), "chunk", (3,)),
    Ore("abyssal_shale", "Abyssal Shale", 1, 20, "common", ((40, 45, 80), (80, 90, 150)), "chunk", (4,)),
    Ore("slugstone", "Slugstone", 1, 50, "common", ((90, 150, 70), (170, 230, 120)), "chunk", (5,)),
    # commons
    Ore("coal", "Coal", 3, 3, "common", ((35, 35, 38), (80, 80, 85)), "chunk", ALL),
    Ore("copper", "Copper", 6, 6, "common", ((200, 110, 60), (245, 170, 120)), "chunk", ALL),
    Ore("tin", "Tin", 10, 9, "common", ((180, 190, 195), (230, 235, 240)), "chunk", ALL),
    Ore("iron", "Iron", 20, 18, "common", ((170, 140, 120), (225, 205, 190)), "chunk", ALL),
    Ore("glowcap", "Glowcap Crystal", 15, 40, "common", ((80, 220, 180), (180, 255, 230)), "crystal", (2,)),
    Ore("magmite", "Magmite", 25, 90, "common", ((230, 80, 20), (255, 190, 60)), "chunk", (3,)),
    Ore("deepglass", "Deepglass", 8, 160, "common", ((40, 90, 160), (120, 190, 255)), "crystal", (4,)),
    Ore("slime_pearl", "Slime Pearl", 5, 300, "common", ((140, 230, 90), (220, 255, 190)), "orb", (5,)),
    # uncommons
    Ore("silver", "Silver", 50, 45, "uncommon", ((190, 195, 205), (250, 250, 255)), "chunk", ALL),
    Ore("gold", "Gold", 100, 90, "uncommon", ((230, 180, 30), (255, 235, 120)), "chunk", ALL),
    Ore("quartz", "Quartz", 150, 130, "uncommon", ((230, 225, 235), (255, 255, 255)), "crystal", ALL),
    Ore("jade", "Jade", 250, 220, "uncommon", ((40, 160, 90), (130, 230, 160)), "gem", _from(2)),
    # rares
    Ore("amethyst", "Amethyst", 500, 450, "rare", ((140, 70, 200), (210, 160, 255)), "crystal", ALL),
    Ore("sapphire", "Sapphire", 900, 800, "rare", ((30, 70, 200), (110, 160, 255)), "gem", _from(2)),
    Ore("ruby", "Ruby", 1_500, 1_300, "rare", ((200, 20, 50), (255, 110, 130)), "gem", _from(2)),
    Ore("emerald", "Emerald", 2_500, 2_200, "rare", ((20, 170, 80), (120, 255, 170)), "gem", _from(3)),
    # epics
    Ore("diamond", "Diamond", 5_000, 5_000, "epic", ((170, 235, 255), (255, 255, 255)), "gem", ALL),
    Ore("moonstone", "Moonstone", 12_000, 11_000, "epic", ((180, 190, 230), (240, 245, 255)), "orb", _from(2)),
    Ore("obsidian_heart", "Obsidian Heart", 7_500, 7_000, "epic", ((40, 10, 50), (200, 60, 255)), "gem", _from(3)),
    # legendaries (special reveal GIF + server announcement)
    Ore("sunshard", "Sunshard", 25_000, 30_000, "legendary", ((255, 170, 20), (255, 245, 150)), "star", ALL),
    Ore("slugheart_opal", "Slugheart Opal", 60_000, 75_000, "legendary", ((120, 255, 200), (255, 180, 240)), "orb", _from(2)),
    Ore("voidstone", "Voidstone", 100_000, 130_000, "legendary", ((20, 0, 40), (150, 60, 255)), "orb", _from(4)),
    # mythics
    Ore("aurorite", "Aurorite", 250_000, 350_000, "mythic", ((60, 255, 170), (180, 100, 255)), "crystal", _from(2)),
    Ore("prismara", "Prismara", 600_000, 900_000, "mythic", ((255, 90, 200), (120, 220, 255)), "gem", _from(3)),
    Ore("chrono_crystal", "Chrono Crystal", 1_000_000, 1_600_000, "mythic", ((255, 210, 120), (120, 200, 255)), "crystal", _from(4)),
    # celestials
    Ore("nebulite", "Nebulite", 2_500_000, 4_500_000, "celestial", ((90, 40, 200), (255, 120, 220)), "orb", _from(3)),
    Ore("starforged_core", "Starforged Core", 5_000_000, 10_000_000, "celestial", ((255, 255, 200), (120, 220, 255)), "star", _from(5)),
    Ore("slug_kings_crown", "Slug King's Crown", 10_000_000, 22_000_000, "celestial", ((255, 200, 40), (255, 255, 200)), "crown", _from(5)),
    # divines
    Ore("eternium", "Eternium", 50_000_000, 120_000_000, "divine", ((255, 255, 255), (200, 160, 255)), "star", _from(5)),
    Ore("golden_slug", "The Golden Slug", 100_000_000, 300_000_000, "divine", ((255, 200, 30), (255, 250, 200)), "slug", _from(5)),
    # Slug Earth contraband
    Ore("blue_crystal", "Grandma's Blue Crystal", 5_000_000, 15_000_000, "contraband", ((60, 160, 255), (200, 240, 255)), "baggie", ALL),

    # ---------------- The Moon (layers 6-7)
    Ore("moon_dust", "Moon Dust", 1, 120, "common", ((180, 180, 175), (230, 230, 225)), "chunk", (6,)),
    Ore("dark_regolith", "Dark Regolith", 1, 250, "common", ((70, 70, 80), (130, 130, 145)), "chunk", (7,)),
    Ore("helium3", "Helium-3", 4, 150, "common", ((120, 220, 255), (220, 250, 255)), "orb", MOON),
    Ore("moon_cheese", "Moon Cheese", 30, 900, "common", ((250, 210, 70), (255, 240, 160)), "chunk", MOON),
    Ore("lunar_titanium", "Lunar Titanium", 80, 2_500, "uncommon", ((150, 160, 180), (230, 235, 245)), "chunk", MOON),
    Ore("flag_scrap", "Apollo Flag Scrap", 600, 18_000, "rare", ((220, 40, 50), (240, 240, 255)), "chunk", MOON),
    Ore("selenite", "Selenite", 3_000, 90_000, "epic", ((230, 230, 255), (255, 255, 255)), "crystal", MOON),
    Ore("lunar_pearl", "Lunar Pearl", 40_000, 1_200_000, "legendary", ((220, 225, 255), (255, 255, 255)), "orb", MOON),
    Ore("moonstone_prime", "Moonstone Prime", 400_000, 12_000_000, "mythic", ((150, 170, 255), (240, 240, 255)), "gem", (7,)),
    Ore("moon_rocks", "Moon Rocks (the other kind)", 10_000_000, 400_000_000, "contraband", ((230, 230, 210), (180, 255, 200)), "baggie", MOON),

    # ---------------- Mars (layers 8-9)
    Ore("red_dust", "Red Dust", 1, 600, "common", ((190, 80, 50), (230, 130, 90)), "chunk", (8,)),
    Ore("oxide_crust", "Iron Oxide Crust", 1, 1_200, "common", ((150, 60, 40), (210, 110, 70)), "chunk", (9,)),
    Ore("rust_nugget", "Rust Nugget", 4, 700, "common", ((180, 90, 40), (240, 150, 80)), "chunk", MARS),
    Ore("martian_ice", "Martian Ice", 25, 4_000, "common", ((170, 230, 255), (240, 250, 255)), "crystal", MARS),
    Ore("rover_parts", "Abandoned Rover Parts", 150, 20_000, "uncommon", ((200, 200, 200), (255, 220, 120)), "chunk", MARS),
    Ore("phobos_shard", "Phobos Shard", 1_000, 150_000, "rare", ((120, 100, 110), (220, 160, 140)), "crystal", MARS),
    Ore("olympus_ruby", "Olympus Ruby", 6_000, 900_000, "epic", ((230, 30, 40), (255, 140, 120)), "gem", MARS),
    Ore("red_giant_garnet", "Red Giant Garnet", 80_000, 12_000_000, "legendary", ((180, 0, 30), (255, 120, 60)), "gem", MARS),
    Ore("martian_fossil", "Ancient Martian Fossil", 1_500_000, 200_000_000, "mythic", ((220, 200, 160), (255, 240, 200)), "chunk", (9,)),
    Ore("k_dust", "Martian K-Dust", 15_000_000, 3_000_000_000, "contraband", ((240, 240, 250), (255, 150, 150)), "pile", MARS),

    # ---------------- Shroomtopia (layers 10-11)
    Ore("spore_soil", "Spore Soil", 1, 3_000, "common", ((90, 60, 90), (160, 110, 160)), "chunk", (10,)),
    Ore("mycelium_mat", "Mycelium Mat", 1, 6_000, "common", ((220, 210, 190), (255, 250, 230)), "chunk", (11,)),
    Ore("glowcap_spores", "Glowcap Spores", 4, 3_500, "common", ((90, 255, 200), (200, 255, 240)), "pile", SHROOM),
    Ore("funky_fungus", "Funky Fungus", 40, 30_000, "common", ((255, 120, 40), (255, 220, 120)), "shroom", SHROOM),
    Ore("truffle", "Truffle of Wealth", 300, 250_000, "uncommon", ((90, 60, 40), (200, 160, 100)), "orb", SHROOM),
    Ore("spore_sapphire", "Spore Sapphire", 2_500, 2_000_000, "rare", ((60, 90, 255), (180, 120, 255)), "gem", SHROOM),
    Ore("bouncy_cap", "Bouncy Cap", 12_000, 15_000_000, "epic", ((255, 80, 200), (255, 220, 250)), "shroom", SHROOM),
    Ore("mushroom_kings_cap", "Mushroom King's Cap", 150_000, 120_000_000, "legendary", ((230, 30, 40), (255, 255, 255)), "shroom", SHROOM),
    Ore("mycelium_heart", "Mycelium Heart", 3_000_000, 2_000_000_000, "celestial", ((140, 255, 160), (255, 200, 255)), "orb", (11,)),
    Ore("cosmic_shroom", "The Cosmic Shroom", 25_000_000, 25_000_000_000, "contraband", ((170, 80, 255), (120, 255, 230)), "shroom", SHROOM),

    # ---------------- Candy Nebula (layers 12-13)
    Ore("sugar_sand", "Sugar Sand", 1, 15_000, "common", ((250, 240, 245), (255, 255, 255)), "pile", (12,)),
    Ore("rock_candy", "Rock Candy Crust", 1, 30_000, "common", ((255, 170, 210), (255, 230, 245)), "crystal", (13,)),
    Ore("gumdrops", "Gumdrops", 4, 18_000, "common", ((80, 220, 90), (200, 255, 190)), "orb", CANDY),
    Ore("jawbreaker", "Jawbreaker", 50, 200_000, "common", ((255, 90, 90), (255, 240, 120)), "orb", CANDY),
    Ore("lollipop_crystal", "Lollipop Crystal", 400, 2_000_000, "uncommon", ((255, 80, 160), (255, 255, 255)), "crystal", CANDY),
    Ore("taffy_ore", "Cosmic Taffy", 3_000, 20_000_000, "rare", ((120, 220, 255), (255, 180, 230)), "chunk", CANDY),
    Ore("cotton_cloud", "Cotton Candy Cloud", 15_000, 120_000_000, "epic", ((255, 170, 220), (170, 220, 255)), "orb", CANDY),
    Ore("gobstopper", "Galactic Gobstopper", 200_000, 1_000_000_000, "legendary", ((140, 60, 255), (255, 220, 80)), "orb", CANDY),
    Ore("sugar_star", "Sugar Star", 5_000_000, 20_000_000_000, "celestial", ((255, 240, 150), (255, 160, 220)), "star", (13,)),
    Ore("space_pez", "Space Pez", 40_000_000, 150_000_000_000, "contraband", ((255, 120, 200), (120, 230, 255)), "pill", CANDY),

    # ---------------- The Singularity (layer 14)
    Ore("void_ash", "Void Ash", 1, 80_000, "common", ((50, 40, 60), (110, 90, 130)), "pile", (14,)),
    Ore("dark_matter", "Dark Matter", 5, 100_000, "common", ((30, 10, 50), (130, 60, 200)), "orb", (14, 15)),
    Ore("antimatter", "Antimatter", 60, 1_500_000, "uncommon", ((255, 255, 255), (255, 80, 220)), "orb", (14, 15)),
    Ore("entropy_crystal", "Entropy Crystal", 800, 20_000_000, "rare", ((80, 0, 160), (0, 255, 220)), "crystal", (14, 15)),
    Ore("singularity_shard", "Singularity Shard", 10_000, 300_000_000, "epic", ((10, 0, 20), (255, 255, 255)), "gem", (14, 15)),
    Ore("horizon_opal", "Event Horizon Opal", 500_000, 20_000_000_000, "mythic", ((255, 130, 40), (60, 0, 120)), "orb", (14, 15)),
    Ore("stardust", "Pure Uncut Stardust", 250_000_000, 2_000_000_000_000, "contraband", ((255, 255, 255), (200, 180, 255)), "pile", (14,)),

    # ---------------- The Nothing (layer 15)
    Ore("nothing", "Literally Nothing", 1, 160_000, "common", ((20, 20, 25), (60, 60, 70)), "orb", (15,)),
    Ore("eye_of_void", "Eye of the Void", 20_000_000, 1_000_000_000_000, "divine", ((0, 0, 0), (180, 80, 255)), "orb", (15,)),
    Ore("last_gram", "The Last Gram in the Universe", 1_000_000_000, 10_000_000_000_000, "contraband", ((255, 255, 255), (255, 215, 0)), "baggie", (15,)),
]
ORE_BY_KEY = {o.key: o for o in ORES}
TIER_ORDER = list(TIERS)


@dataclass(frozen=True)
class Realm:
    key: str
    name: str
    emoji: str


REALMS = {
    "milky": Realm("milky", "The Milky Way", "🌌"),
    "trippy": Realm("trippy", "The Trippy Dimension", "🍄"),
    "void": Realm("void", "The Void Realm", "🕳️"),
}


@dataclass(frozen=True)
class Planet:
    key: str
    name: str
    realm: str
    emoji: str


PLANETS = {
    "earth": Planet("earth", "Slug Earth", "milky", "🌍"),
    "moon": Planet("moon", "The Moon", "milky", "🌕"),
    "mars": Planet("mars", "Mars", "milky", "🔴"),
    "shroom": Planet("shroom", "Shroomtopia", "trippy", "🍄"),
    "candy": Planet("candy", "Candy Nebula", "trippy", "🍭"),
    "singularity": Planet("singularity", "The Singularity", "void", "⚫"),
    "nothing": Planet("nothing", "The Nothing", "void", "⬛"),
}


@dataclass(frozen=True)
class Layer:
    num: int
    name: str
    planet: str
    cost: int             # coins to reach this layer
    materials: dict       # parts and ores needed too (only for the first layer of a planet)
    luck: float
    color: tuple
    blurb: str

    @property
    def planet_info(self):
        return PLANETS[self.planet]

    @property
    def realm_info(self):
        return REALMS[self.planet_info.realm]


LAYERS = [
    Layer(1, "Mossy Tunnels", "earth", 0, {}, 1.0, (70, 110, 60), "Damp, mossy and full of slugs. Everyone starts here."),
    Layer(2, "Crystal Caverns", "earth", 5_000, {}, 1.25, (90, 200, 200), "Glowing crystals light the walls."),
    Layer(3, "Magma Depths", "earth", 75_000, {}, 1.5, (220, 80, 30), "Hot enough to cook a slug. Rare gems melt out of the rock."),
    Layer(4, "The Abyss", "earth", 1_000_000, {}, 2.0, (40, 30, 90), "Pitch black. Things in the dark are worth a fortune."),
    Layer(5, "Slug Core", "earth", 25_000_000, {}, 2.5, (130, 230, 80), "The heart of Slug Earth, home of the Slug King. Grandma hides her stash down here."),
    Layer(6, "Tranquility Craters", "moon", 30_000_000,
          {"part:fuel": 3, "part:hull": 2, "part:nav": 1, "ore:sunshard": 1}, 3.0, (190, 190, 200),
          "One small step for slug. The cheese is real."),
    Layer(7, "The Dark Side", "moon", 120_000_000, {}, 3.5, (50, 50, 70), "Pink Floyd is playing somewhere. Nobody knows where."),
    Layer(8, "Red Dust Flats", "mars", 300_000_000,
          {"part:fuel": 5, "part:hull": 5, "part:o2": 2, "ore:lunar_pearl": 1}, 4.0, (200, 80, 50),
          "Dusty, red, and full of abandoned rovers. The locals are very calm."),
    Layer(9, "Olympus Underbelly", "mars", 1_200_000_000, {}, 4.5, (140, 40, 30), "Under the tallest volcano in the system. Ancient things sleep here."),
    Layer(10, "Spore Forest", "shroom", 3_000_000_000,
          {"part:warp": 1, "part:o2": 3, "ore:red_giant_garnet": 1, "ore:olympus_ruby": 3}, 5.0, (150, 70, 170),
          "You've left the Milky Way. The trees are mushrooms. The mushrooms are watching."),
    Layer(11, "Mycelium Mines", "shroom", 12_000_000_000, {}, 5.5, (220, 200, 170), "Everything is connected, man."),
    Layer(12, "Gumdrop Belt", "candy", 30_000_000_000,
          {"part:munchie": 2, "part:fuel": 20, "ore:mushroom_kings_cap": 1}, 6.0, (255, 150, 200),
          "A nebula made of sugar. Your teeth hurt just landing here."),
    Layer(13, "Sugar Core", "candy", 120_000_000_000, {}, 6.5, (255, 220, 120), "The sweetest place in the universe. Some of the candy isn't candy."),
    Layer(14, "Event Horizon", "singularity", 300_000_000_000,
          {"part:void_engine": 1, "part:warp": 2, "ore:gobstopper": 1}, 7.0, (40, 20, 70),
          "You've crossed into the Void. Time is weird. Your pickaxe is spaghetti."),
    Layer(15, "The Nothing", "nothing", 3_000_000_000_000,
          {"part:void_engine": 2, "ore:singularity_shard": 3}, 8.0, (15, 15, 20),
          "There is nothing here. Except, rumor says, one last gram."),
]
REBIRTH_LAYER = 5         # you can rebirth once you've reached Slug Core


@dataclass(frozen=True)
class Part:
    key: str
    name: str
    price: int
    materials: dict
    blurb: str


# Spaceship parts, crafted in the Workshop and burned when you travel to a new planet.
PARTS = [
    Part("fuel", "Rocket Fuel", 25_000, {"ore:coal": 100, "ore:magmite": 10}, "Coal and magma in a jug. Smells illegal."),
    Part("hull", "Hull Plating", 50_000, {"bar:iron": 10, "bar:silver": 5}, "Keeps space out."),
    Part("nav", "Nav Computer", 200_000, {"ore:diamond": 1, "ore:sapphire": 3}, "Points the rocket away from the ground."),
    Part("o2", "Oxygen Tank", 2_000_000, {"ore:helium3": 60, "ore:lunar_titanium": 5}, "Breathing: highly recommended."),
    Part("warp", "Warp Core", 500_000_000, {"ore:red_giant_garnet": 1, "ore:phobos_shard": 5, "part:fuel": 10}, "Tears a hole into the next realm."),
    Part("munchie", "Munchie Shield", 5_000_000_000, {"ore:truffle": 10, "ore:funky_fungus": 60}, "Stops you eating the whole Candy Nebula."),
    Part("void_engine", "Void Engine", 100_000_000_000, {"ore:gobstopper": 1, "ore:sugar_star": 1, "part:warp": 1}, "Runs on nothing. Literally."),
]
PART_BY_KEY = {p.key: p for p in PARTS}


@dataclass(frozen=True)
class Pickaxe:
    key: str
    name: str
    price: int
    materials: dict       # item key -> qty ("bar:iron", "ore:diamond")
    luck: float
    cooldown: float       # seconds between swings
    rolls: int            # ores rolled per swing
    color: tuple


PICKAXES = [
    Pickaxe("wood", "Wooden Pickaxe", 0, {}, 1.0, 1.0, 1, (150, 100, 50)),
    Pickaxe("stone", "Stone Pickaxe", 250, {}, 1.1, 0.9, 1, (130, 130, 130)),
    Pickaxe("copper", "Copper Pickaxe", 1_500, {"bar:copper": 5}, 1.2, 0.85, 1, (200, 110, 60)),
    Pickaxe("iron", "Iron Pickaxe", 8_000, {"bar:iron": 10}, 1.35, 0.8, 1, (200, 190, 180)),
    Pickaxe("gold", "Golden Pickaxe", 40_000, {"bar:gold": 10}, 1.6, 0.75, 2, (240, 190, 40)),
    Pickaxe("diamond", "Diamond Pickaxe", 250_000, {"ore:diamond": 1, "bar:gold": 20}, 2.0, 0.7, 2, (170, 235, 255)),
    Pickaxe("obsidian", "Obsidian Pickaxe", 1_500_000, {"ore:obsidian_heart": 1, "bar:iron": 40}, 2.5, 0.65, 3, (90, 30, 120)),
    Pickaxe("slugsteel", "Slugsteel Pickaxe", 10_000_000, {"ore:slugheart_opal": 1, "bar:silver": 50}, 3.5, 0.6, 3, (130, 230, 80)),
    Pickaxe("starbreaker", "Starbreaker", 20_000_000, {"ore:sunshard": 2, "bar:gold": 100}, 4.5, 0.55, 4, (255, 240, 160)),
    Pickaxe("moon_drill", "Moon Drill", 200_000_000, {"ore:lunar_pearl": 1, "ore:selenite": 5}, 5.5, 0.5, 4, (200, 200, 230)),
    Pickaxe("plasma", "Plasma Pick", 2_000_000_000, {"ore:red_giant_garnet": 1, "ore:olympus_ruby": 5}, 7.0, 0.45, 5, (255, 90, 40)),
    Pickaxe("shroom_scythe", "Shroom Scythe", 20_000_000_000, {"ore:mushroom_kings_cap": 1, "ore:bouncy_cap": 5}, 8.5, 0.4, 5, (230, 40, 60)),
    Pickaxe("lollipick", "Lollipick", 200_000_000_000, {"ore:gobstopper": 1, "ore:cotton_cloud": 5}, 10.0, 0.35, 6, (255, 100, 180)),
    Pickaxe("void_breaker", "Void Breaker", 5_000_000_000_000, {"ore:horizon_opal": 1, "ore:singularity_shard": 10}, 12.0, 0.3, 7, (120, 40, 220)),
]
PICKAXE_BY_KEY = {p.key: p for p in PICKAXES}

# smelting: ORE_PER_BAR ores + a coin fee -> 1 bar. Bars sell for more than the ores did.
ORE_PER_BAR = 5
BARS = {
    "copper": ("Copper Bar", 10),
    "tin": ("Tin Bar", 15),
    "iron": ("Iron Bar", 30),
    "silver": ("Silver Bar", 80),
    "gold": ("Gold Bar", 160),
}
def bar_value(bar):
    return int(ORE_BY_KEY[bar].value * ORE_PER_BAR * 1.4)


# Sluglings are hired helpers that mine while you're away.
SLUGLING_BASE_COST = 2_000
SLUGLING_COST_GROWTH = 1.6
SLUGLING_SECONDS_PER_ROLL = 60
SLUGLING_LUCK = 0.5
COLLECT_CAP_HOURS = 12

# Lucky Roll: a spinning reel that hands out a small boost.
LUCKY_ROLL_COOLDOWN = 10 * 60


@dataclass(frozen=True)
class Prize:
    key: str
    name: str
    weight: int
    kind: str             # buff kind (luck, echo, sell, mutluck) or "coins"
    amount: float
    minutes: int
    color: tuple
    icon: str             # clover, bolt, coin, pick, slug, star, dna, cash


# Haste was retired in v0.6.0: Discord can't take button presses faster than about one a second anyway.
PRIZES = [
    Prize("pouch", "Coin Pouch", 30, "coins", 1, 0, (240, 190, 40), "coin"),
    Prize("lucky", "Lucky Hour  x1.5 luck", 26, "luck", 1.5, 10, (90, 210, 90), "clover"),
    Prize("midas", "Midas Touch  x1.5 sell value", 16, "sell", 1.5, 10, (255, 215, 90), "cash"),
    Prize("magnet", "Mutation Magnet  x3 mutations", 12, "mutluck", 3, 10, (120, 220, 255), "dna"),
    Prize("echo", "Echo Swing  2x mines", 7, "echo", 2, 5, (200, 130, 255), "pick"),
    Prize("surge", "Clover Surge  x3 luck", 7, "luck", 3, 5, (40, 255, 120), "star"),
    Prize("blessing", "Slug Blessing  x10 luck", 2, "luck", 10, 2, (255, 120, 220), "slug"),
]
PRIZE_BY_KEY = {p.key: p for p in PRIZES}

# Rebirth: reset progress for permanent boosts.
REBIRTH_COST = 50_000_000
REBIRTH_LUCK = 0.5        # +50% luck per rebirth
REBIRTH_SELL = 0.25       # +25% sell value per rebirth

# Daily reward: claim every 20h, streak breaks after 48h. Streak adds +10% a day (max +100%),
# and every 7th day also gives a free Lucky Roll recharge and 30 minutes of x2 luck.
DAILY_COOLDOWN = 20 * 3600
DAILY_GRACE = 48 * 3600
AUTOSELL_MAX_TIER = "uncommon"   # auto-sell only sells common and uncommon ores


# ================================================================ v0.3.0 Smuggler's Run

def scale(layer):
    """Coin prices that grow with how far you've gotten (x3 per layer), so new stuff stays relevant."""
    return 3 ** (layer - 1)


# ---- Space Authority cargo checks
# "Hot cargo" is any Legendary-or-rarer ore. Contraband makes the cops way more interested.
HOT_TIER = "legendary"
CHECK_CHANCE_CONTRABAND = 1 / 500     # per swing while you carry contraband
CHECK_CHANCE_HOT = 1 / 2000           # per swing while you carry other hot cargo
CHECK_CHANCE_TRAVEL = 0.25            # launching to a new planet with contraband aboard
CHECK_COOLDOWN = 15 * 60              # at least this long between two checks on the same player
CHECK_STEP_SECONDS = 25               # time to pick an option before the cops lose patience
FINE_CONTRABAND = 0.10                # share of your coins fined when busted
FINE_HOT = 0.05
BRIBE_SHARE = 0.08                    # bribe costs this share of your coins (at least BRIBE_MIN)
BRIBE_MIN = 1_000
BRIBE_SUCCESS = 0.75
TALK_RIGHT = 0.85                     # smooth talk with the excuse that matches the officer's mood
TALK_WRONG = 0.25

STASH_SPOTS = [
    "Under the driver's seat", "Inside the fuel tank", "In a hollowed-out moon cheese",
    "Taped under the toilet", "In the glovebox (bold)", "Inside a slug plushie",
    "In the spare spacesuit", "Behind a poster of the Slug King", "In your own sock",
    "In the cereal box", "Inside the nav computer", "Under the floor mat",
]

# (mood, hint shown to the player, excuse that works)
OFFICER_MOODS = [
    ("Hungry", "keeps staring at your snacks and his stomach is growling", "Want half my sandwich, officer?"),
    ("Bored", "is yawning and checking the clock", "I'm just a boring space accountant."),
    ("Grumpy", "is red in the face and muttering about paperwork", "I'll fill out every form twice, sir!"),
    ("Sleepy", "can barely keep his eyes open", "It's late, you should go home and rest."),
    ("Starstruck", "keeps asking if you're famous", "I'm a celebrity. Want an autograph?"),
]
OFFICER_NAMES = ["Officer Glorp", "Sergeant Zorb", "Deputy Flarn", "Inspector Blip", "Captain Krunk"]

# (emoji, riddle) for distracting Sniffles the K-9 Slug
SNIFFLES_TREATS = [
    ("🍩", "something round with a hole in the middle"),
    ("🍕", "a triangle with cheese on it"),
    ("🌭", "a sausage in a bun"),
    ("🍪", "a crunchy circle with chocolate chips"),
    ("🧀", "the stuff the Moon is made of"),
    ("🥓", "crispy strips from a pig"),
    ("🍌", "a long yellow fruit"),
    ("🦴", "the classic, a bone"),
]

# ---- Spaceship upgrades
@dataclass(frozen=True)
class ShipUpgrade:
    key: str
    name: str
    emoji: str
    max_level: int
    base: int             # coins at level 1, before layer scaling
    bar: str              # bar type used, needs (level) * 10 of them
    blurb: str


SHIP_UPGRADES = [
    ShipUpgrade("stash", "Hidden Compartments", "🗄️", 5, 3_000, "iron",
                "-15% cargo check chance per level, and the cops sometimes miss your stash spot."),
    ShipUpgrade("hull", "Reinforced Hull", "🛡️", 5, 4_000, "silver",
                "Raiders have a lower chance of finding anything on your ship."),
    ShipUpgrade("engine", "Turbo Engine", "🔥", 5, 5_000, "gold",
                "Better raids and more firepower in duels."),
]
SHIP_BY_KEY = {u.key: u for u in SHIP_UPGRADES}
STASH_CHECK_CUT = 0.15        # per Hidden Compartments level
STASH_MISS = 0.10             # per level: chance the inspector misses your spot even if he looks there
PAINT_COST = 1_000            # x layer scale
PAINTS = [
    ("Slug Green", (110, 220, 90)), ("Cop Car Blue", (60, 110, 230)), ("Hot Pink", (255, 90, 190)),
    ("Rust Bucket", (170, 90, 50)), ("Void Black", (40, 35, 55)), ("Gold Chain", (240, 200, 60)),
    ("Grandma White", (235, 235, 240)), ("Candy Red", (230, 40, 60)),
]

# ---- Raids and duels
RAID_COOLDOWN = 20 * 60
RAID_PROTECT = 2 * 3600       # the same ship can't be raided by the same player again for this long
RAID_BASE = 0.50
RAID_ENGINE = 0.04
RAID_HULL = 0.08
RAID_MIN, RAID_MAX = 0.10, 0.85
RAID_LOOT = (3, 6)            # ores found when a raid works
RAID_LUCK = 3.0               # bonus luck on raid loot rolls
DUEL_TIMEOUT = 120
DUEL_COOLDOWN = 60

# ---- Asteroid Run (v0.5.0): steer through a falling asteroid field with buttons, one row per press
ASTEROID_LANES = 7
ASTEROID_ROWS = 8             # rows on screen; the rocket sits on the bottom one
ASTEROID_MOVES = 20           # survive this many moves to dock at Tim's station
ASTEROID_FEE = 1_000          # x layer scale, paid when the run starts
ASTEROID_BONUS = 3            # docking pays the fee back this many times
ASTEROID_LUCK = 2.0           # bonus luck on the ore chunks you pick up
ASTEROID_CHUNK_CHANCE = 0.5   # chance each new row has an ore chunk
ASTEROID_COOLDOWN = 10 * 60

# ---- Space Snacks (crafted from coins + your current layer's base ore, eaten for buffs)
@dataclass(frozen=True)
class Snack:
    key: str
    name: str
    emoji: str
    buff: str             # buff kind
    amount: float         # strength for a brand-new player...
    amount_max: float     # ...growing to this with depth and pickaxe (see snack_power in space.py)
    minutes: int          # same for how long it lasts
    minutes_max: int
    coins: int            # x layer scale, cheaper early on
    ore_qty: int          # how many of your current layer's base ore
    blurb: str            # "{amount}" and "{minutes}" get filled in


# Keep this list the same length: Tweaker Tim's saved visits point into it by position.
SNACKS = [
    Snack("luck_juice", "Luck Juice", "🧃", "snack_luck", 1.5, 2.5, 10, 15, 3_000, 40, "x{amount} luck for {minutes} minutes. Tastes like pennies."),
    Snack("mutation_mints", "Mutation Mints", "🍬", "mutluck", 1.5, 3, 10, 15, 3_500, 40, "x{amount} mutation chance for {minutes} minutes. Minty. Glowing."),
    Snack("double_dip", "Double Dip Chips", "🥔", "rolls", 2, 2, 5, 10, 4_000, 50, "x{amount} ores per swing for {minutes} minutes."),
    Snack("cop_repellent", "Cop Repellent", "🧴", "nocops", 1, 1, 30, 120, 5_000, 60, "No cargo checks for {minutes} minutes. Smells like donuts."),
    Snack("mega_munchies", "Mega Munchies", "🍔", "snack_luck", 3, 6, 5, 5, 12_000, 120, "x{amount} luck for {minutes} minutes. You will not stop eating."),
]
RETIRED_SNACKS = {"haste_gummies": "mutation_mints"}   # old snack -> what players get instead
SNACK_BY_KEY = {s.key: s for s in SNACKS}

# ---- Bounty board: 3 jobs a day (UTC). stat = which counter has to go up, need = by how much.
@dataclass(frozen=True)
class BountyKind:
    key: str
    stat: str
    text: str             # "{n}" gets the amount
    need: tuple           # (low, high) before scaling
    scale_by: str         # "", "rolls" (pickaxe ores per swing) or "layer" (coin scale)
    reward: int           # x layer scale


BOUNTY_KINDS = [
    BountyKind("mine", "mined", "Mine {n} ores", (150, 300), "rolls", 1_500),
    BountyKind("rare", "found_rare", "Find {n} Rare-or-better ores", (2, 5), "", 2_500),
    BountyKind("sell", "sold_coins", "Make {n} coins selling ore", (2_000, 5_000), "layer", 2_000),
    BountyKind("roll", "lucky_rolls", "Use {n} Lucky Rolls", (2, 4), "", 2_000),
    BountyKind("smelt", "smelted", "Smelt {n} bars", (5, 15), "", 1_500),
    BountyKind("raid", "raids", "Raid {n} ship(s)", (1, 2), "", 3_000),
    BountyKind("snack", "snacks_eaten", "Eat {n} Space Snack(s)", (1, 2), "", 2_000),
    BountyKind("cook", "snacks_made", "Make {n} Space Snack(s)", (1, 3), "", 1_500),
    BountyKind("collect", "collected", "Collect from your sluglings {n} time(s)", (1, 3), "", 1_000),
]
BOUNTY_BY_KEY = {b.key: b for b in BOUNTY_KINDS}
BOUNTIES_PER_DAY = 3

# ---- Meteor strikes and Tweaker Tim
METEOR_CLAIMS = 5             # how many players can mine each meteor
METEOR_ROLLS = 8
METEOR_LUCK = 10.0
METEOR_MINUTES = 10
METEOR_SHOWER = (2.0, 15)     # server-wide luck boost when one hits: (x luck, minutes)
METEOR_BONUS_PER_MINUTE = 1 / 4000   # rare extra meteors on top of the guaranteed daily one
TIM_MINUTES = 10
TIM_GAP_HOURS = (5, 11)       # time between Tim's visits (about 3 a day)
TIM_WEIRD_ROCK_CHANCE = 0.005
TIM_FENCE_MULT = 1.5

# ---- Achievements. Every achievement unlocks its name as a title you can wear with /title.
@dataclass(frozen=True)
class Achievement:
    key: str
    name: str
    emoji: str
    desc: str


ACHIEVEMENTS = [
    Achievement("first_ore", "Rookie Tweaker", "⛏️", "Mine your first ore"),
    Achievement("mined_1k", "Rock Muncher", "🪨", "Mine 1,000 ores"),
    Achievement("mined_100k", "Professional Tweaker", "💼", "Mine 100,000 ores"),
    Achievement("mined_1m", "Has Not Slept", "😵", "Mine 1,000,000 ores"),
    Achievement("moon", "Moon Walker", "🌕", "Reach the Moon"),
    Achievement("mars", "Red Planet Regular", "🔴", "Reach Mars"),
    Achievement("trippy", "Interdimensional", "🍄", "Warp into the Trippy Dimension"),
    Achievement("candy", "Sugar Rush", "🍭", "Reach the Candy Nebula"),
    Achievement("void", "Void Gazer", "⚫", "Warp into the Void Realm"),
    Achievement("nothing", "Seen Nothing", "⬛", "Reach The Nothing"),
    Achievement("legendary", "Lucky Slug", "🟡", "Find a Legendary ore"),
    Achievement("mythic", "Mythbuster", "🔴", "Find a Mythic ore"),
    Achievement("celestial", "Starchild", "💠", "Find a Celestial ore"),
    Achievement("divine", "The Chosen One", "🌟", "Find a Divine ore"),
    Achievement("contraband", "Known to Police", "🚨", "Find any contraband"),
    Achievement("kingpin", "Kingpin", "👑", "Find every piece of contraband in the universe"),
    Achievement("smooth", "Smooth Criminal", "🕶️", "Get through 5 cargo checks clean"),
    Achievement("busted", "Space Felon", "🚔", "Get busted by the Space Authority"),
    Achievement("pirate", "Space Pirate", "🏴‍☠️", "Pull off 10 successful raids"),
    Achievement("topgun", "Top Gun", "✈️", "Win 10 duels"),
    Achievement("meteors", "Rock Chaser", "☄️", "Mine 5 meteors"),
    Achievement("tim", "Tim's Best Customer", "👽", "Make 5 deals with Tweaker Tim"),
    Achievement("munchies", "Munchie Monster", "🍔", "Eat 20 Space Snacks"),
    Achievement("bounty", "Bounty Hunter", "📜", "Finish 15 bounties"),
    Achievement("rebirth", "Arsonist", "🔥", "Burn the lab down (rebirth)"),
    Achievement("pimped", "Pimp My Rocket", "🚀", "Max out every ship upgrade"),
    Achievement("million", "Millionaire", "💰", "Hold 1,000,000 coins"),
    Achievement("billion", "Billionaire", "💎", "Hold 1,000,000,000 coins"),
    Achievement("trillion", "Space Oligarch", "🛸", "Hold 1,000,000,000,000 coins"),
]
ACH_BY_KEY = {a.key: a for a in ACHIEVEMENTS}


# ================================================================ v0.4.0 Mutations
# Any ore you find can come out mutated. Each find rolls every mutation, rarest first.
@dataclass(frozen=True)
class Mutation:
    key: str
    name: str
    emoji: str
    chance: int           # 1 in N per ore found
    mult: int             # sell value multiplier
    color: tuple
    announce: bool        # shout to the server and play the big reveal


MUTATIONS = [
    Mutation("tweaked", "Tweaked", "🌀", 50_000, 150, (255, 40, 200), True),
    Mutation("galactic", "Galactic", "🌌", 10_000, 50, (110, 80, 255), True),
    Mutation("holographic", "Holographic", "🌈", 2_500, 20, (120, 230, 255), True),
    Mutation("shadow", "Shadow", "🌑", 1_000, 10, (90, 40, 140), False),
    Mutation("radioactive", "Radioactive", "☢️", 400, 6, (120, 255, 60), False),
    Mutation("shiny", "Shiny", "✨", 100, 3, (255, 235, 120), False),
]
MUT_BY_KEY = {m.key: m for m in MUTATIONS}
MUT_ORDER = [m.key for m in reversed(MUTATIONS)]   # shiny ... tweaked (weakest to strongest)

ACHIEVEMENTS += [
    Achievement("shiny", "Shiny Hunter", "✨", "Find a mutated ore"),
    Achievement("holo", "Holo Collector", "🌈", "Find a Holographic ore"),
    Achievement("galactic", "Galaxy Brain", "🌌", "Find a Galactic ore"),
    Achievement("tweaked", "Fully Tweaked", "🌀", "Find a Tweaked ore"),
    Achievement("mutant", "Mutant Menagerie", "🧬", "Find every kind of mutation"),
]
ACH_BY_KEY = {a.key: a for a in ACHIEVEMENTS}
