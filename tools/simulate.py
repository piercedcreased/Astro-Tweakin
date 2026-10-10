"""Quick balance check: python -m tools.simulate"""
import collections
from game import engine
from game.data import LAYERS

for layer in LAYERS:
    c = collections.Counter(engine.roll_ore(layer.num, layer.luck).name for _ in range(200_000))
    print(layer.name, c.most_common(8))
