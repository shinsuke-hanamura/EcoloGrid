"""知覚・知覚した地図・NPC の判断の地図化・経路探索(docs/tasks/1_16)。"""
import pytest

from ecologrid.core import factory
from ecologrid.core.ai import Decision, decide, execute, think
from ecologrid.core.config import WorldConfig, validate_config
from ecologrid.core.grid import Pos
from ecologrid.core.pathfinding import bfs_path
from ecologrid.core.percept_map import BUCKET, T_LAND, T_WATER, PerceptMap
from ecologrid.core.perception import perceive, scan_mobile
from ecologrid.core.species import SPECIES
from ecologrid.core.world import World
from helpers import empty_world, land_run, region_for


def _setup(species="Rabbit", run=8):
    w = empty_world()
    pos = land_run(w, run)
    r = region_for(w, pos)
    a = r.spawn(species, pos, **({"player_id": "p"} if species == "Player" else {}))
    a.wakefulness = a.wakefulness_max
    return w, r, a, pos


# --- 知覚の範囲 ---
def test_species_perception_standards_and_copy_to_individual():
    S = SPECIES
    assert (S["Rabbit"].perception_range, S["Fox"].perception_range, S["Player"].perception_range) == (6, 8, 20)
    assert all(S[k].perception_interval_ticks == 20 for k in ("Rabbit", "Fox", "Player"))
    w, r, a, _ = _setup("Fox")
    assert (a.perception_range, a.perception_interval_ticks) == (8, 20)


def test_effective_range_is_floor_of_range_times_wakefulness_ratio():
    w, r, a, _ = _setup("Rabbit")                      # 範囲 6
    for ratio, expected in ((1.0, 6), (0.5, 3), (0.34, 2), (0.16, 0), (0.0, 0)):
        a.wakefulness = a.wakefulness_max * ratio
        assert a.effective_range(r) == expected, ratio
    a.wakefulness = a.wakefulness_max
    a.state = "sleeping"
    assert a.effective_range(r) == 0                   # 眠っている間は 0


def test_player_standard_range_is_20_and_sleep_disabled_gives_full_range():
    w = empty_world(player_sleep_enabled=False)
    pid = w.add_player("p")
    p = w.player_entity(pid)
    r = w.regions[w.players[pid].region]
    assert p.perception_range == 20 and p.effective_range(r) == 20


def test_individual_range_is_a_copy_and_can_differ():
    w, r, a, pos = _setup("Rabbit")
    a.perception_range = 3
    assert a.effective_range(r) == 3
    b = r.spawn("Rabbit", pos)
    assert b.perception_range == 6                      # 種の標準値は変わらない


# --- 距離(チェビシェフ)と範囲内の個体 ---
def test_entities_in_range_is_a_square_around_the_cell():
    w, r, a, pos = _setup("Rabbit", run=8)
    cells = {(dx, 0): r.spawn("Grass", Pos(pos.x + dx, pos.y)) for dx in range(0, 6)}
    ids_in = {e.entity_id for e in r.entities_in_range(pos, 3)}
    for (dx, _), e in cells.items():
        assert (e.entity_id in ids_in) == (dx <= 3), dx
    assert a.entity_id in {e.entity_id for e in r.entities_in_range(pos, 0)}     # r = 0: 自分のセルだけ
    assert {e.entity_id for e in r.entities_in_range(pos, 0)} == {a.entity_id, cells[(0, 0)].entity_id}


def test_bucket_index_follows_move_and_remove():
    w, r, a, pos = _setup("Rabbit")
    assert a.entity_id in r._buckets[(pos.x // BUCKET, pos.y // BUCKET)]
    r.tick_no += 100
    assert a.try_step("e", r)
    np = a.grid_position
    assert a.entity_id in r._buckets[(np.x // BUCKET, np.y // BUCKET)]
    r.remove(a, "death")
    assert all(a.entity_id not in b for b in r._buckets.values())


# --- 地図の更新 ---
def test_perceive_marks_own_cell_plus_range_and_skips_known_cells():
    w, r, a, pos = _setup("Rabbit")
    a.wakefulness = a.wakefulness_max * 0.5             # r = 3
    assert perceive(a, r)
    x_min, x_max = max(pos.x - 3, r.x0), min(pos.x + 3, r.x0 + r.size - 1)
    y_min, y_max = max(pos.y - 3, r.y0), min(pos.y + 3, r.y0 + r.size - 1)
    assert a.map.known == (x_max - x_min + 1) * (y_max - y_min + 1)
    v = a.map.version
    assert not perceive(a, r) and a.map.version == v    # 既知は更新を飛ばす(変化なし → 版番号は同じ)
    assert a.map.terrain_at(pos.x, pos.y) == T_LAND


def test_range_zero_perceives_only_own_cell():
    w, r, a, pos = _setup("Rabbit")
    a.wakefulness = a.wakefulness_max * 0.01
    assert a.effective_range(r) == 0
    perceive(a, r)
    assert a.map.known == 1 and a.map.terrain_at(pos.x, pos.y) == T_LAND


def test_resource_layer_counts_plants_per_bucket_with_nearest_representative():
    w, r, a, pos = _setup("Rabbit", run=8)
    bx, by = pos.x // BUCKET, pos.y // BUCKET
    g1 = r.spawn("Grass", Pos(pos.x + 1, pos.y))
    g2 = r.spawn("Grass", Pos(pos.x + 2, pos.y))
    a.wakefulness = a.wakefulness_max
    perceive(a, r)
    total = sum(e[0] for b, d in a.map.resources.items() for sp, e in d.items() if sp == "Grass")
    assert total == 2
    res = a.map.nearest_resource(("Grass",), pos.x, pos.y)
    assert res == (pos.x + 1, pos.y, "Grass")           # 代表 = 自分に最も近い植物
    # 食べられて消えたら、次の知覚で、(区画が全部見えていれば)地図からも消える
    r.remove(g1, "eaten"); r.remove(g2, "eaten")
    r.tick_no += 10
    perceive(a, r)
    assert a.map.nearest_resource(("Grass",), pos.x, pos.y) is None


def test_decay_thins_resources_but_not_terrain():
    m = PerceptMap(32, 32)
    m.set_terrain(1, 1, T_LAND)
    m.set_resource((0, 0), "Grass", 3, 5, 1, 1)
    m.set_resource((1, 1), "Wheat", 0.6, 5, 5, 5)
    removed = m.decay(0.9, 0.5)
    assert removed == 0 and m.resources[(0, 0)]["Grass"][0] == pytest.approx(2.7)
    removed = m.decay(0.5, 0.5)                         # 0.54 → 0.27(閾値未満) / 2.7 → 1.35
    assert removed == 1 and (1, 1) not in m.resources and m.resources[(0, 0)]["Grass"][0] == pytest.approx(1.35)
    assert m.terrain_at(1, 1) == T_LAND                  # 地形は薄めない


def test_map_state_roundtrip_and_not_public():
    w, r, a, pos = _setup("Rabbit")
    r.spawn("Grass", Pos(pos.x + 1, pos.y))
    perceive(a, r)
    st = a.to_state()
    assert st["map"] is not None and len(st["map"]["terrain"]) < 2000        # 圧縮されている(世界 64×64 でも小さい)
    b = factory.restore(st)
    assert b.map.known == a.map.known and b.map.version == a.map.version
    assert b.map.resources == a.map.resources and bytes(b.map.terrain) == bytes(a.map.terrain)
    assert "map" not in a.to_dict()                                           # 公開用には含めない


def test_perception_runs_every_interval_with_scattered_phase():
    w, r, a, pos = _setup("Rabbit")
    a.next_action_tick = 10 ** 9
    r.tick_no = 0
    seen = []
    orig = type(a).perceive_if_due
    def spy(self, region):
        before = self.next_perception_tick
        orig(self, region)
        if self.next_perception_tick != before:
            seen.append(region.tick_no)
    type(a).perceive_if_due = spy
    try:
        for t in range(1, 200):
            r.tick_no = t
            a.tick(r)
    finally:
        type(a).perceive_if_due = orig
    assert len(seen) >= 8 and all(b - a_ == 20 for a_, b in zip(seen, seen[1:]))     # 20 tick ごと
    phases = {make for make in (
        World.__init__ and __import__("ecologrid.core.rng", fromlist=["make_rng"]).make_rng(1, "perc0", f"e{i}").randrange(20)
        for i in range(40))}
    assert len(phases) > 10                              # 個体ごとに位相が散る


# --- 経路探索 ---
def test_bfs_detours_around_obstacles_and_is_deterministic():
    wall = {(2, y) for y in range(-3, 4) if y != 3}
    passable = lambda x, y: -5 <= x <= 8 and -5 <= y <= 8 and (x, y) not in wall
    path = bfs_path((0, 0), passable, lambda x, y: (x, y) == (4, 0))
    assert path is not None and len(path) == 4 + 2 * 3          # 壁の隙間(y = 3)まで回り込む
    pos = (0, 0)
    for d in path:
        pos = (pos[0] + {"e": 1, "w": -1}.get(d, 0), pos[1] + {"s": 1, "n": -1}.get(d, 0))
        assert passable(*pos)
    assert pos == (4, 0)
    assert bfs_path((0, 0), passable, lambda x, y: (x, y) == (4, 0)) == path   # 決定論


def test_bfs_unreachable_start_goal_and_limit():
    passable = lambda x, y: (x, y) in {(0, 0), (1, 0), (5, 5)}
    assert bfs_path((0, 0), passable, lambda x, y: (x, y) == (5, 5)) is None
    assert bfs_path((0, 0), passable, lambda x, y: (x, y) == (0, 0)) == []
    open_world = lambda x, y: True
    assert bfs_path((0, 0), open_world, lambda x, y: (x, y) == (100, 100), max_nodes=50) is None


# --- NPC は地図で判断する ---
def _prefill_region_terrain(a, r):
    m = a.map or PerceptMap(r.cfg.width, r.cfg.height)
    a.map = m
    from ecologrid.core.percept_map import CODE
    for y in range(r.y0, r.y0 + r.size):
        for x in range(r.x0, r.x0 + r.size):
            m.set_terrain(x, y, CODE[r.terrain.kind(x, y)])


def test_npc_decides_from_map_without_querying_the_world(monkeypatch):
    w, r, a, pos = _setup("Rabbit")
    a.hydration = 20.0
    _prefill_region_terrain(a, r)
    def boom(*args, **kw):
        raise AssertionError("世界への問い合わせを使った")
    monkeypatch.setattr(type(r), "nearest_water", boom)
    monkeypatch.setattr(type(r), "nearest_prey", boom)
    d = decide(a, r)
    assert isinstance(d, Decision) and d.kind in ("move", "drink", "wander")


def test_decide_is_pure_and_returns_materials():
    w, r, a, pos = _setup("Rabbit")
    a.hydration = 20.0
    _prefill_region_terrain(a, r)
    before = (tuple(a.grid_position), list(a.path), a.resting, a.next_move_tick)
    d1, d2 = decide(a, r, use_cache=False), decide(a, r, use_cache=False)
    assert (tuple(a.grid_position), list(a.path), a.resting, a.next_move_tick) == before
    assert d1.to_dict() == d2.to_dict()
    assert {"r", "hydration", "energy", "stamina", "health"} <= set(d1.materials)


def test_thirsty_npc_drinks_next_to_water_and_walks_to_known_water():
    w = empty_world(seed=1, regions_x=2, regions_y=2, region_size=16)
    r = w.regions[(0, 0)]
    # 水に隣接する陸と、そこから離れた陸を探す
    water = next(p for p in sorted(r.water_cells))
    shore = next(Pos(p.x + dx, p.y + dy) for p in [water] for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                 if r.walkable(Pos(p.x + dx, p.y + dy)))
    a = r.spawn("Rabbit", shore)
    a.hydration, a.wakefulness = 20.0, a.wakefulness_max
    perceive(a, r)
    d = decide(a, r)
    assert d.kind == "drink" and d.goal == "water"
    h = a.hydration
    r.tick_no = 100
    execute(a, r, d)
    assert a.hydration > h


def test_thirsty_rabbit_behind_obstacle_reaches_water_seed1_small_world():
    """渇きで死んだ状況の再現(seed 1・2×2×16・位置 (18, 3))。経路探索のない軸方向の移動は詰まった。
    水まで、陸の経路で 11〜12 歩あるが、直線では 7〜8 歩。新しい AI は、迂回して飲む。"""
    w = World(WorldConfig(seed=1, regions_x=2, regions_y=2, region_size=16, initial_density={}, initial_counts={}))
    rb = w.regions[(1, 0)].spawn("Rabbit", Pos(18, 3))
    eid = rb.entity_id
    rb.hydration, rb.wakefulness = 20.0, rb.wakefulness_max

    def find():
        for reg in w.regions.values():                 # Region を越えると、別のオブジェクトに置き換わる
            if eid in reg.entities:
                return reg.entities[eid]
    drank_at = None
    for t in range(1, 6_000):
        w.tick()
        a = find()
        assert a is not None, "死んだ"
        a.wakefulness, a.energy = max(a.wakefulness, 0.9 * a.wakefulness_max), 100.0
        if a.hydration > 22:
            drank_at = t
            break
    assert drank_at is not None and drank_at < 5_000


def test_hungry_npc_goes_to_known_plant_and_eats_when_touching():
    w, r, a, pos = _setup("Rabbit", run=8)
    g = r.spawn("Grass", Pos(pos.x + 3, pos.y))
    a.energy, a.wakefulness = 20.0, a.wakefulness_max
    perceive(a, r)
    d = decide(a, r)
    assert d.kind == "move" and d.goal == "food" and d.target == (pos.x + 3, pos.y)
    r.tick_no = 1000
    for _ in range(3):                                  # 3 歩。足元に入ったとき、空腹で食事が可能なので食べる
        r.tick_no += 25
        think(a, r)
    assert a.grid_position == Pos(pos.x + 3, pos.y)
    assert g.entity_id not in r.entities and a.energy > 20.0


def test_fox_chases_visible_rabbit_live_but_map_stores_no_mobile():
    w, r, fox, pos = _setup("Fox", run=8)
    rb = r.spawn("Rabbit", Pos(pos.x + 4, pos.y))
    fox.energy, fox.wakefulness = 20.0, fox.wakefulness_max
    perceive(fox, r)
    assert scan_mobile(fox, r) == [("Rabbit", pos.x + 4, pos.y, rb.entity_id)]
    assert all("Rabbit" not in d for d in fox.map.resources.values())          # 動く個体は地図に残さない
    d = decide(fox, r)
    assert d.kind == "move" and d.goal == "prey" and d.target == (pos.x + 4, pos.y)


def test_sleepy_npc_sees_less_and_sleeping_npc_perceives_nothing():
    w, r, a, pos = _setup("Rabbit", run=8)
    far = r.spawn("Grass", Pos(pos.x + 5, pos.y))
    a.wakefulness = a.wakefulness_max * 0.5             # r = 3 → 5 セル先は見えない
    perceive(a, r)
    assert a.map.nearest_resource(("Grass",), pos.x, pos.y) is None
    a.wakefulness = a.wakefulness_max                    # r = 6 → 見える
    r.tick_no += 10
    perceive(a, r)
    assert a.map.nearest_resource(("Grass",), pos.x, pos.y) is not None
    a.state = "sleeping"
    before = a.map.version
    a.next_perception_tick = 0
    a.perceive_if_due(r)
    assert a.map.version == before                       # 眠っている間は知覚しない


# --- 設定・API ---
def test_validate_config_has_no_warning_for_default_world():
    assert validate_config(WorldConfig()) == []


def test_config_exposes_perception_standards():
    from fastapi.testclient import TestClient
    from ecologrid.api.main import create_app
    from ecologrid.api.settings import Settings
    with TestClient(create_app(Settings(regions_x=1, regions_y=1, autotick=False))) as c:
        sp = c.get("/api/v1/config").json()["species"]
        assert (sp["Rabbit"]["perception_range"], sp["Fox"]["perception_range"], sp["Player"]["perception_range"]) == (6, 8, 20)
        assert sp["Player"]["perception_interval_ticks"] == 20
