from ecologrid.core import factory
from ecologrid.core.actions import ScheduledAction
from ecologrid.core.config import WorldConfig
from ecologrid.core.grid import Pos
from ecologrid.core.plant import plant_cycle
from ecologrid.core.species import SPECIES
from ecologrid.core.world import World
from helpers import empty_world, land_run, region_for


def test_seed_germinates_into_plant():
    """発芽はタイマー: 落ちた tick を 1 回目として germination_ticks 回目の tick(落ちた tick + N − 1)に発芽する
    (従来の、種子が毎 tick age を数えて N に達する tick と同じ)。"""
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    n = SPECIES["Grass"].germination_ticks
    t0 = 1_000
    r.tick_no = t0
    r._place_seed("Grass", pos)                                    # 落ちた tick = t0
    (seed,) = r.entities_of("Seed")
    assert seed.timers == {"germinate:Grass": t0 - 1} and seed.born_tick == t0
    for t in range(t0, t0 + n - 1):                                 # 発芽の 1 tick 前まで
        r.tick([], t)
        assert [e.species for e in r.entities_at(pos)] == ["Seed"], t
    r.tick([], t0 + n - 1)                                          # N 回目の tick
    assert [e.species for e in r.entities_at(pos)] == ["Grass"]
    assert r.entities_at(pos)[0].birth_tick == t0 + n - 1          # 発芽した tick が誕生 tick
    assert any(e["type"] == "seed_germinated" for e in r.events)
    assert not r.entities_of("Seed") and not r._wheels["germinate:Grass"].slots   # タイマーも片付く


def test_seed_trampled_before_germination_never_germinates():
    w = empty_world()
    pos = land_run(w, 3)
    r = region_for(w, pos)
    r.tick_no = 500
    r._place_seed("Wheat", pos)
    rb = r.spawn("Rabbit", Pos(pos.x - 1, pos.y)) if r.walkable(Pos(pos.x - 1, pos.y)) else None
    (seed,) = r.entities_of("Seed")
    r.remove(seed, "trampled")
    assert not r._wheels["germinate:Wheat"].slots
    for t in range(500, 500 + SPECIES["Wheat"].germination_ticks + 2):
        r.tick([], t)
    assert [e.species for e in r.entities_at(pos)] == []


def test_two_seeds_germinate_independently_and_in_order():
    w = empty_world()
    pos = land_run(w, 4)
    r = region_for(w, pos)
    p2 = Pos(pos.x + 1, pos.y)
    r.tick_no = 100
    r._place_seed("Grass", pos)
    r.tick_no = 160
    r._place_seed("Grass", p2)
    n = SPECIES["Grass"].germination_ticks
    for t in range(100, 160 + n):
        r.tick([], t)
    assert [e.species for e in r.entities_at(pos)] == ["Grass"]
    assert [e.species for e in r.entities_at(p2)] == ["Grass"]
    assert r.entities_at(pos)[0].birth_tick == 100 + n - 1 and r.entities_at(p2)[0].birth_tick == 160 + n - 1


def test_plants_seeds_and_eggs_are_not_in_the_per_tick_loop():
    """毎 tick の tick を呼ぶのは動物・Player だけ(植物・種子・卵は Region の tick 集合に入らない)。"""
    w = empty_world()
    pos = land_run(w, 6)
    r = region_for(w, pos)
    g = r.spawn("Grass", pos)
    r._place_seed("Wheat", Pos(pos.x + 1, pos.y))
    rb = r.spawn("Rabbit", Pos(pos.x + 2, pos.y))
    egg = r.spawn("Egg", Pos(pos.x + 3, pos.y), parent_species="Rabbit", laid_tick=0, incubation_ticks=10)
    p = r.spawn("Player", Pos(pos.x + 4, pos.y), player_id="p")
    assert set(r._tickers) == {rb.entity_id, p.entity_id}
    assert len(r.entities) == 5
    r.remove(rb, "death")                                           # 除去すると集合からも消える
    assert set(r._tickers) == {p.entity_id}
    q = Pos(pos.x + 5, pos.y)
    p.try_step("e", r)                                              # 移動(_unindex → _index)しても 1 件のまま
    assert list(r._tickers) == [p.entity_id]


def test_plant_energy_is_lazily_evaluated_and_does_not_tick():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    g = r.spawn("Grass", pos)
    e0 = g.energy
    g.tick(r)                                  # 毎 tick の処理はしない
    assert g.energy == e0
    r.tick_no += 1000                          # 時間が経過すると、読み取り時に経過 tick 分が反映される
    assert abs((g.energy - e0) - 1000 * 100 / 129_600) < 1e-6
    r.tick_no += 10 ** 7
    assert g.energy == 100                     # 満タンで頭打ち


def test_plant_hydration_and_water():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    g = r.spawn("Grass", pos)
    g._near_water = True
    g.hydration = 10
    r.tick_no += 5
    assert g.hydration == 100                  # 水の近くでは満たされる
    g._near_water = False
    g.hydration = 100
    r.tick_no += 100_000
    assert abs(g.hydration - (100 - 100_000 * 100 / 1_296_000)) < 1e-6   # 水が無ければ spec の率で減る


def test_plant_dehydration_dooms_and_cycle_removes():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    g = r.spawn("Grass", pos)
    g._near_water = False
    g.hydration = 0
    r.tick_no += SPECIES["Grass"].deficiency_death_ticks - 100
    assert g.health > 0 and not g._doomed
    r.tick_no += 200                           # 水分 0 のまま deficiency_death_ticks を超える
    assert g.health == 0 and g._doomed
    assert g.entity_id in r.entities           # プロパティの読み取りでは除去しない(走査中の変更を避ける)
    act = r.find_action("plant_cycle:Grass")
    plant_cycle(r, act, r.tick_no, g)
    assert g.entity_id not in r.entities
    assert any(e["type"] == "entity_removed" and e["reason"] == "death" for e in r.events)


def test_snapshot_does_not_mutate_region_even_with_doomed_plant():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    g = r.spawn("Grass", pos)
    g._near_water, g.hydration = False, 0
    r.tick_no += SPECIES["Grass"].deficiency_death_ticks + 10
    snap = w.snapshot()                        # to_dict が各プロパティを読んでも、辞書は変更されない
    assert any(e["id"] == g.entity_id and e["health"] == 0 for rr in snap["regions"] for e in rr["entities"])


def test_plant_disperses_seed_at_birth_anchored_interval():
    """誕生 tick から seed_dispersal_interval_ticks ごと(tick の倍数ではなく、個体ごとの位相)。"""
    w = empty_world()
    pos = land_run(w, 5)
    r = region_for(w, pos)
    n = SPECIES["Grass"].seed_dispersal_interval_ticks
    for _ in range(100):
        w.tick()
    g = r.spawn("Grass", pos, init={"birth_tick": r.tick_no})       # tick 100 に誕生
    g.energy = 100
    first = r.tick_no + n
    while w.tick_no < first - 1:
        w.tick()
    assert not any(e.species == "Seed" for e in r.entities.values())      # 周期の手前では散布しない
    w.tick()                                                             # tick = 100 + n
    seeds = [e for e in r.entities.values() if e.species == "Seed"]
    assert w.tick_no == first and len(seeds) <= 1
    assert abs(g.energy - (100 - SPECIES["Grass"].seed_cost)) < 1e-6 or not seeds   # 散布で Energy を消費


def test_plant_cycle_handler_sows_and_costs_energy():
    w = empty_world()
    pos = land_run(w, 5)
    r = region_for(w, pos)
    g = r.spawn("Grass", pos)
    g.energy = 100
    for i in range(30):                         # 乱数で (0,0) が出ることもあるので複数回
        g.energy = 100
        plant_cycle(r, r.find_action("plant_cycle:Grass"), r.tick_no, g)
    assert any(e.species == "Seed" for e in r.entities.values())
    g.energy = 0
    n = len(r.entities)
    plant_cycle(r, r.find_action("plant_cycle:Grass"), r.tick_no, g)
    assert len(r.entities) == n and g.energy == 0     # Energy が足りなければ散布しない


def test_plant_state_survives_serialization():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    g = r.spawn("Wheat", pos, init={"birth_tick": 7})
    r.tick_no += 5000
    d = g.to_dict()
    assert d["birth_tick"] == 7 and abs(d["energy"] - (60 + 5000 * 100 / 162_000)) < 0.01
    b = factory.restore(d)
    assert abs(b.energy - d["energy"]) < 0.01 and b.birth_tick == 7


def test_initial_plants_have_spread_phases():
    w = World(WorldConfig())
    w.generate()
    n = SPECIES["Grass"].seed_dispersal_interval_ticks
    phases = {e.timers["plant_cycle:Grass"] % n for r in w.regions.values() for e in r.entities_of("Grass")}
    assert len(phases) > 600                    # 約 700 個の Grass が、ほぼ全員異なる位相


def test_default_plant_actions_registered():
    w = World(WorldConfig(regions_x=1, regions_y=1))
    ids = {a.action_id for a in w.actions.list("global")}
    assert {"plant_cycle:Grass", "plant_cycle:Wheat"} <= ids
