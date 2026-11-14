import json

from ecologrid.core.config import WorldConfig
from ecologrid.core.grid import Pos
from ecologrid.core.messages import Command
from ecologrid.core.world import EventsExpired, World
from helpers import empty_world


def _run(seed, ticks):
    w = World(WorldConfig(seed=seed, regions_x=2, regions_y=2))
    w.generate()
    for _ in range(ticks):
        w.tick()
    return w


def test_deterministic_simulation():
    a, b = _run(3, 300), _run(3, 300)
    assert json.dumps(a.snapshot(), sort_keys=True) == json.dumps(b.snapshot(), sort_keys=True)
    assert a.last_seq == b.last_seq


def test_generate_populates():
    w = World(WorldConfig(regions_x=2, regions_y=2))
    w.generate()
    c = w.census()
    assert c["Grass"] > 0 and c["Rabbit"] > 0


def test_initial_animal_counts_are_exact_and_deterministic():
    """初期の動物は世界全体の頭数(既定: Rabbit 32・Fox 4)。植物は密度。"""
    def positions(seed):
        w = World(WorldConfig(seed=seed))
        w.generate()
        return w, sorted((e.species, tuple(e.grid_position)) for r in w.regions.values()
                         for sp in ("Rabbit", "Fox") for e in r.entities_of(sp))
    w, a = positions(1)
    c = w.census()
    assert c["Rabbit"] == 32 and c["Fox"] == 4 and c["Grass"] > 500
    assert positions(1)[1] == a and positions(2)[1] != a
    w2 = World(WorldConfig(initial_counts={"Rabbit": 3}, initial_density={}))
    w2.generate()
    assert w2.census() == {"Rabbit": 3}
    assert all(e["type"] != "entity_spawned" for r in w2.regions.values() for e in r.events)   # 初期配置はイベントにしない


def test_ecosystem_survives_short_run():
    w = _run(1, 1500)
    c = w.census()
    assert c.get("Grass", 0) > 0


def test_player_move_command_and_idempotency():
    w = empty_world()
    pid = w.add_player("alice")
    p = w.player_entity(pid)
    start = p.grid_position
    ok = False
    for d in "nsew":
        cmd = Command(f"c-{d}", pid, "move", {"dir": d})
        assert w.submit(cmd) == (True, "ok")
        assert w.submit(cmd) == (False, "duplicate")
        w.tick()
        if w.player_entity(pid).grid_position != start:
            ok = True
            break
    assert ok


def test_unknown_player_and_queue_full():
    w = empty_world(command_queue_max=2)
    assert w.submit(Command("x", "nobody", "wait")) == (False, "unknown_player")
    pid = w.add_player("a")
    assert w.submit(Command("1", pid, "wait"))[0]
    assert w.submit(Command("2", pid, "wait"))[0]
    assert w.submit(Command("3", pid, "wait")) == (False, "queue_full")


def test_player_death_and_respawn():
    w = empty_world()
    pid = w.add_player("a")
    p = w.player_entity(pid)
    p.health = 0.001
    p.hydration = 0
    w.tick()
    assert w.player_entity(pid) is None
    evs, _ = w.events_since(0)
    assert any(e["type"] == "player_died" for e in evs)
    w.submit(Command("r", pid, "respawn"))
    w.tick()
    assert w.player_entity(pid) is not None


def test_events_since_and_expiry():
    w = empty_world(event_log_size=5)
    w.add_player("a")
    for _ in range(30):
        w.tick()
    pid = next(iter(w.players))
    for i in range(20):
        w.submit(Command(f"m{i}", pid, "wait"))
        w.tick()
    # stats_changed 等が出ていれば古い seq は期限切れ
    if w.last_seq > 5:
        try:
            w.events_since(0)
            assert False, "expected EventsExpired"
        except EventsExpired:
            pass
    evs, last = w.events_since(w.last_seq)
    assert evs == [] and last == w.last_seq


def test_region_crossing_handoff():
    w = empty_world(regions_x=2, regions_y=1)
    t = w.terrain
    size = w.cfg.region_size
    # 領域境界(x=size-1 → size)を挟んで陸が連続する行を探す
    row = next((y for y in range(w.cfg.height)
                if t.kind(size - 1, y) == "." and t.kind(size, y) == "."), None)
    assert row is not None
    r0 = w.regions[(0, 0)]
    pid = w.add_player("a")
    old = w.player_entity(pid)
    rec = w.players[pid]
    w.regions[rec.region].remove(old, "test")
    e = r0.spawn("Player", Pos(size - 1, row), player_id=pid)
    rec.entity_id, rec.region = e.entity_id, (0, 0)
    w.submit(Command("go", pid, "move", {"dir": "e"}))
    w.tick()
    # 越境した直後(tick の合間)でも、Entity は移動先 Region に存在し snapshot にも載る
    assert rec.region == (1, 0) and w.player_entity(pid) is not None
    ids = [x["id"] for r in w.snapshot()["regions"] for x in r["entities"]]
    assert ids.count(e.entity_id) == 1
    w.tick()
    assert rec.region == (1, 0)
    assert w.player_entity(pid).grid_position == Pos(size, row)
    assert e.entity_id not in r0.entities

