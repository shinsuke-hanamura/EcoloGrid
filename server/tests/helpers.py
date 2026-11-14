from ecologrid.core.config import WorldConfig
from ecologrid.core.grid import Pos
from ecologrid.core.terrain import LAND
from ecologrid.core.world import World


def empty_world(seed=1, **kw) -> World:
    """個体のいない世界(generate しない)。"""
    return World(WorldConfig(seed=seed, **kw))


def land_run(world: World, length: int = 3) -> Pos:
    """東向きに length セル連続して陸の左端セルを返す。"""
    t = world.terrain
    for y in range(world.cfg.height):
        for x in range(world.cfg.width - length):
            if all(t.kind(x + i, y) == LAND for i in range(length)):
                return Pos(x, y)
    raise AssertionError("no land run")


def region_for(world: World, pos: Pos):
    return world.regions[(pos.x // world.cfg.region_size, pos.y // world.cfg.region_size)]
