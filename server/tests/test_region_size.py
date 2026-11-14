"""Region のサイズを変えられる(WorldConfig.region_size / ECOLOGRID_REGION_SIZE)。"""
import json
import sys

import pytest
from fastapi.testclient import TestClient

from ecologrid.api.main import create_app
from ecologrid.api.settings import Settings
from ecologrid.core.config import WorldConfig, validate_config
from ecologrid.core.grid import Pos, region_of
from ecologrid.core.terrain import LAND
from ecologrid.core.world import World
from helpers import land_run


@pytest.mark.parametrize("size", [8, 16, 24, 32, 50])
def test_world_runs_with_any_region_size(size):
    w = World(WorldConfig(seed=2, regions_x=2, regions_y=2, region_size=size,
                          initial_counts={"Rabbit": 6, "Fox": 1}))
    w.generate()
    assert (w.cfg.width, w.cfg.height) == (2 * size, 2 * size)
    for _ in range(400):
        w.tick()
    snap = w.snapshot()
    assert snap["region_size"] == size and len(snap["regions"]) == 4
    for reg in snap["regions"]:
        assert reg["size"] == size and len(reg["terrain"]) == size and all(len(row) == size for row in reg["terrain"])
        rid = tuple(reg["region"])
        for e in reg["entities"]:                                   # Entity は、自分の Region の中にいる
            assert region_of(Pos(*e["pos"]), size) == rid
    assert w.census().get("Grass", 0) > 0


def test_terrain_is_the_same_cells_regardless_of_region_size():
    """地形は座標(と seed)だけで決まる。Region の分け方を変えても、同じセルは同じ種類。"""
    a = World(WorldConfig(seed=5, regions_x=2, regions_y=2, region_size=32))
    b = World(WorldConfig(seed=5, regions_x=4, regions_y=4, region_size=16))
    assert all(a.terrain.kind(x, y) == b.terrain.kind(x, y) for x in range(64) for y in range(64))


def test_deterministic_with_small_regions():
    def run():
        w = World(WorldConfig(seed=3, regions_x=3, regions_y=2, region_size=12, initial_counts={"Rabbit": 10, "Fox": 2}))
        w.generate()
        for _ in range(500):
            w.tick()
        return json.dumps(w.snapshot(), sort_keys=True), w.last_seq
    assert run() == run()


def test_handoff_across_small_region_boundary():
    w = World(WorldConfig(seed=1, regions_x=2, regions_y=1, region_size=8, initial_density={}, initial_counts={}))
    pos = None
    for y in range(8):                                  # 東端の 2 セル(x = 6, 7)が続けて陸のところ + その東(x = 8)も陸
        if all(w.terrain.kind(x, y) == LAND for x in (6, 7, 8)):
            pos = Pos(7, y)
            break
    if pos is None:
        pytest.skip("no suitable land strip for this seed")
    r0 = w.regions[(0, 0)]
    rb = r0.spawn("Rabbit", pos)
    assert rb.try_step("e", r0)                         # Region (0, 0) から (1, 0) へ
    w._deliver()
    assert rb.entity_id not in r0.entities
    moved = [e for e in w.regions[(1, 0)].entities.values() if e.species == "Rabbit"]
    assert len(moved) == 1 and moved[0].grid_position == Pos(8, pos.y)


def test_config_validation_of_region_size():
    assert validate_config(WorldConfig(region_size=32)) == []
    assert any("region_size" in m for m in validate_config(WorldConfig(region_size=8)))      # 視界(8)の 2 倍未満は警告
    for bad in (0, -4, 3.5, "32", True):
        with pytest.raises(ValueError):
            validate_config(WorldConfig(region_size=bad))
    for name in ("regions_x", "regions_y"):
        with pytest.raises(ValueError):
            validate_config(WorldConfig(**{name: 0}))


def test_settings_region_size_from_env_and_api(monkeypatch):
    monkeypatch.setenv("ECOLOGRID_REGION_SIZE", "16")
    monkeypatch.setenv("ECOLOGRID_REGIONS_X", "2")
    monkeypatch.setenv("ECOLOGRID_REGIONS_Y", "2")
    st = Settings.from_env()
    st.autotick = False
    st.admin_key = "adm"
    assert st.region_size == 16
    with TestClient(create_app(st)) as c:
        j = c.get("/api/v1/config").json()
        assert j["world"]["region_size"] == 16 and j["world"]["width"] == 32 and j["world"]["height"] == 32
        r = c.post("/api/v1/players", json={"name": "p"}).json()
        h = {"Authorization": f"Bearer {r['token']}"}
        snap = c.get("/api/v1/world/snapshot", headers={"X-Admin-Key": "adm"}).json()          # 世界全体は管理者だけ
        assert snap["region_size"] == 16 and all(len(x["terrain"]) == 16 for x in snap["regions"])
        assert c.get("/api/v1/world/regions", headers=h).json()["region_size"] == 16


def test_cli_view_follows_region_size_from_snapshot():
    sys.path.insert(0, "cli")
    from ecologrid_cli.render import render
    from ecologrid_cli.view import WorldView
    rows = ["." * 16 for _ in range(16)]
    snap = {"tick": 1, "region_size": 16, "event_seq": 0, "world_id": "w",
            "regions": [{"region": [rx, ry], "origin": [rx * 16, ry * 16], "size": 16, "terrain": rows,
                         "entities": [{"id": "me", "species": "Player", "name": "me", "pos": [20, 3],
                                       "player_id": "pid", "hydration": 1, "energy": 1, "health": 1}]
                         if (rx, ry) == (1, 0) else []} for ry in range(2) for rx in range(2)]}
    v = WorldView()
    v.player_id, v.me_id = "pid", "me"
    v.load(snap)
    assert v.region_size == 16 and v.me_pos == (20, 3)
    assert v.kind(20, 3) == "." and v.kind(40, 3) == " "            # 世界の外
    assert "pos=20,3" in render(v, cols=40, rows=20)


def test_simulate_tool_accepts_world_size_options(monkeypatch, capsys):
    from ecologrid.tools import simulate
    monkeypatch.setattr(sys, "argv", ["simulate", "--ticks", "100", "--every", "50", "--regions-x", "2",
                                      "--regions-y", "2", "--region-size", "16", "--scale-animals"])
    simulate.main()
    out = capsys.readouterr().out
    assert "world 32x32 cells = 2x2 Regions of 16x16" in out
    assert "'Rabbit': 2" in out and "'Fox': 1" in out               # 面積比 1/16 で、32 → 2、4 → 1(最低 1)
    monkeypatch.setattr(sys, "argv", ["simulate", "--ticks", "10", "--every", "10", "--regions-x", "1",
                                      "--regions-y", "1", "--region-size", "32", "--rabbits", "5", "--foxes", "0"])
    simulate.main()
    assert "'Rabbit': 5" in capsys.readouterr().out
