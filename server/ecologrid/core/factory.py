"""種キー・シリアライズ辞書から Entity を生成する。"""
from .animal import Animal
from .egg import Egg
from .entity import Entity
from .grid import Pos
from .plant import SeedPlant
from .player import Player
from .seed import Seed
from .species import SPECIES

_CLASSES = {"Animal": Animal, "Player": Player, "SeedPlant": SeedPlant}


def create(species: str, entity_id: str, pos: Pos, name: str = "", **kw) -> Entity:
    if species == "Egg":
        return Egg(entity_id, pos, kw["parent_species"], kw["laid_tick"], kw["incubation_ticks"], name)
    if species == "Seed":
        return Seed(entity_id, pos, kw["plant_type"], name)
    spec = SPECIES[species]
    return _CLASSES[spec.cls](spec, entity_id, pos, name, **kw)


def restore(d: dict) -> Entity:
    kw = {}
    if d["species"] == "Seed":
        kw = {"plant_type": d["plant_type"]}
    elif d["species"] == "Egg":
        kw = {k: d[k] for k in ("parent_species", "laid_tick", "incubation_ticks")}
    e = create(d["species"], d["id"], Pos(*d["pos"]), d.get("name", ""), **kw)
    e.load_state(d)
    return e
