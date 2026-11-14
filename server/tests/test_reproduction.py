from dataclasses import replace

from ecologrid.core import factory
from ecologrid.core.config import WorldConfig
from ecologrid.core.reproduction import breed_check, gestation_action_id, gestation_step
from ecologrid.core.species import FISSION, OVIPAROUS, SPECIES, VIVIPAROUS
from ecologrid.core.world import World
from helpers import land_run, region_for


def patch_repro(monkeypatch, species="Rabbit", **kw):
    sp = SPECIES[species]
    monkeypatch.setitem(SPECIES, species, replace(sp, repro=replace(sp.repro, **kw)))


def setup(monkeypatch=None, **kw):
    """初期個体なしの世界。ハンドラを直接呼ぶテスト用に Region の定期アクションは既定のまま。"""
    if monkeypatch is not None:
        patch_repro(monkeypatch, **kw)
    w = World(WorldConfig(initial_density={}))
    pos = land_run(w, 3)
    return w, region_for(w, pos), pos


ADULT = -3_000_000     # 十分に成体(最低年齢 777,600 tick(Fox は 1,296,000)を超える)


def adult(r, pos, **init):
    return r.spawn("Rabbit", pos, init={"birth_tick": ADULT, **init})


def check(r, a, tick):
    breed_check(r, r.find_action("breed_check:Rabbit"), tick, a)


def step(r, a, tick):
    gestation_step(r, r.find_action("gestation_step:Rabbit"), tick, a)


def test_eligibility_conditions(monkeypatch):
    w, r, pos = setup(monkeypatch, success_prob=1.0, min_age_ticks=100, min_energy=60,
                      min_interval_ticks=50)
    young = r.spawn("Rabbit", pos, init={"birth_tick": 950})             # 年齢 50 < 100
    hungry = adult(r, pos, energy=10)                                    # Energy 不足
    recent = adult(r, pos, last_birth_tick=980)                          # 間隔 20 < 50
    preg = adult(r, pos, gestation_started_tick=500)                     # 既に妊娠中
    ok = adult(r, pos)
    for a in (young, hungry, recent, preg, ok):
        check(r, a, 1000)
    assert young.gestation_started_tick is None
    assert hungry.gestation_started_tick is None
    assert recent.gestation_started_tick is None
    assert preg.gestation_started_tick == 500
    assert ok.gestation_started_tick == 1000
    assert ok.timers[gestation_action_id("Rabbit")] == 1000      # 妊娠成功の tick が基準


def test_success_probability(monkeypatch):
    w, r, pos = setup(monkeypatch, success_prob=0.0)
    a = adult(r, pos)
    check(r, a, 100)
    assert a.gestation_started_tick is None
    patch_repro(monkeypatch, success_prob=0.5)
    many = [adult(r, pos) for _ in range(200)]
    for m in many:
        check(r, m, 100)
    n = sum(m.gestation_started_tick is not None for m in many)
    assert 60 < n < 140


def test_gestation_consumes_energy_then_gives_birth(monkeypatch):
    w, r, pos = setup(monkeypatch, success_prob=1.0, gestation_period_ticks=100,
                      gestation_step_ticks=25, gestation_energy_cost=2.0, litter_size=3,
                      offspring_energy=33)
    mother = adult(r, pos, energy=80)
    check(r, mother, 1000)
    n0 = len(r.entities_of("Rabbit"))
    for t in (1025, 1050, 1075):
        step(r, mother, t)
        assert mother.gestation_started_tick == 1000
    assert mother.energy == 80 - 2 * 3
    assert len(r.entities_of("Rabbit")) == n0
    step(r, mother, 1100)                             # 期間満了
    kids = [e for e in r.entities_of("Rabbit") if e is not mother]
    assert len(kids) == 3
    assert all(k.energy == 33 and k.birth_tick == 1100 for k in kids)
    assert mother.gestation_started_tick is None and mother.last_birth_tick == 1100
    assert gestation_action_id("Rabbit") not in mother.timers        # タイマーは停止
    assert mother.energy == 80 - 2 * 4
    assert any(e["type"] == "reproduced" and e["kind"] == VIVIPAROUS for e in r.events)


def test_miscarriage_when_energy_runs_out(monkeypatch):
    w, r, pos = setup(monkeypatch, success_prob=1.0, gestation_period_ticks=100,
                      gestation_step_ticks=25, gestation_energy_cost=5.0)
    mother = adult(r, pos, energy=60)
    check(r, mother, 0)
    mother.energy = 3
    n0 = len(r.entities_of("Rabbit"))
    step(r, mother, 25)
    assert mother.gestation_started_tick is None
    assert gestation_action_id("Rabbit") not in mother.timers
    assert len(r.entities_of("Rabbit")) == n0
    assert any(e["type"] == "pregnancy_lost" for e in r.events)


def test_fission_halves_energy_into_two(monkeypatch):
    w, r, pos = setup(monkeypatch, type=FISSION, success_prob=1.0)
    a = adult(r, pos, energy=80)
    check(r, a, 700)
    rabbits = r.entities_of("Rabbit")
    assert len(rabbits) == 2
    assert [e.energy for e in rabbits] == [40, 40]
    child = next(e for e in rabbits if e is not a)
    assert child.birth_tick == 700 and a.last_birth_tick == 700
    assert a.gestation_started_tick is None


def test_oviparous_lays_egg_then_hatches(monkeypatch):
    w, r, pos = setup(monkeypatch, type=OVIPAROUS, success_prob=1.0, gestation_period_ticks=50,
                      gestation_step_ticks=25, gestation_energy_cost=1.0, incubation_ticks=40,
                      litter_size=2, offspring_energy=25)
    mother = adult(r, pos)
    check(r, mother, 0)
    step(r, mother, 25)
    step(r, mother, 50)
    eggs = r.entities_of("Egg")
    assert len(eggs) == 2 and len(r.entities_of("Rabbit")) == 1
    assert all(e.timers == {"hatch:Rabbit": 50} for e in eggs)       # 孵化はタイマー(産卵の tick を基準)
    act = r.find_action("hatch:Rabbit")
    r.tick_no = 60                       # 産卵 tick=50, 孵化まで 40
    r.run_entity_action(act, 60)
    assert len(r.entities_of("Egg")) == 2
    r.tick_no = 89
    r.run_entity_action(act, 89)         # 1 tick 手前では孵化しない
    assert len(r.entities_of("Egg")) == 2
    r.tick_no = 90
    r.run_entity_action(act, 90)
    assert len(r.entities_of("Egg")) == 0
    kids = [e for e in r.entities_of("Rabbit") if e is not mother]
    assert len(kids) == 2 and all(k.energy == 25 and k.birth_tick == 90 for k in kids)


def test_reproduction_state_survives_serialization(monkeypatch):
    w, r, pos = setup(monkeypatch)
    a = adult(r, pos, gestation_started_tick=123, last_birth_tick=45)
    b = factory.restore(a.to_dict())
    assert (b.birth_tick, b.last_birth_tick, b.gestation_started_tick) == (ADULT, 45, 123)
    assert b.timers == a.timers and "breed_check:Rabbit" in b.timers
    egg = factory.restore(factory.create("Egg", "e1", pos, parent_species="Rabbit",
                                         laid_tick=7, incubation_ticks=9).to_dict())
    assert (egg.parent_species, egg.laid_tick, egg.incubation_ticks) == ("Rabbit", 7, 9)


# ---------- tick 駆動(定期アクション経由)の検証 ----------
def _single_region_world():
    w = World(WorldConfig(regions_x=1, regions_y=1, initial_density={}))
    return w, w.regions[(0, 0)], land_run(w, 3)


def test_gestation_runs_exactly_from_conception_tick(monkeypatch):
    """妊娠成功が世界の tick の倍数でなくても、成功から正確に step tick ごとに育ち period で出産。"""
    patch_repro(monkeypatch, success_prob=0.0, gestation_period_ticks=100,
                gestation_step_ticks=25, gestation_energy_cost=2.0, litter_size=1)
    w, r, pos = _single_region_world()
    for _ in range(7):
        w.tick()                                       # tick_no = 7
    m = r.spawn("Rabbit", pos, init={"birth_tick": ADULT, "energy": 100})
    m.gestation_started_tick = w.tick_no
    r.start_timer(m, gestation_action_id("Rabbit"), w.tick_no)
    energy_at = {}
    while w.tick_no < 7 + 100:
        w.tick()
        energy_at[w.tick_no] = m.energy
    kids = [e for e in r.entities_of("Rabbit") if e is not m]
    assert len(kids) == 1 and kids[0].birth_tick == 107              # 妊娠 tick 7 + 100 ちょうど
    assert energy_at[31] > energy_at[32]                             # 最初の成長は 7 + 25 = 32
    assert m.last_birth_tick == 107


def test_initial_population_phases_are_spread():
    """初期個体の子作り判定タイミング(anchor % N)が特定の tick に偏らない。"""
    from collections import Counter
    w = World(WorldConfig(initial_counts={"Rabbit": 40}))          # 既定 4x4 Region に、ウサギ 40 匹
    w.generate()
    n = SPECIES["Rabbit"].repro.check_interval_ticks      # 16,200 スロット
    c = Counter(e.timers["breed_check:Rabbit"] % n
                for r in w.regions.values() for e in r.entities_of("Rabbit"))
    total = sum(c.values())
    assert total == 40 and len(c) >= total * 0.8 and max(c.values()) <= 3


def test_births_are_spread_across_ticks(monkeypatch):
    """多数の成体を同時に置いても、判定が毎 tick に分散する(1 tick に全員が集中しない)。"""
    patch_repro(monkeypatch, success_prob=0.0, check_interval_ticks=20)
    w, r, pos = _single_region_world()
    for i in range(100):
        r.spawn("Rabbit", pos, init={"birth_tick": ADULT - i})
    wheel = r._wheels["breed_check:Rabbit"]
    sizes = [len(s) for s in wheel.slots.values()]
    assert len(sizes) == 20 and max(sizes) <= 6


def test_world_ticks_drive_breeding_end_to_end(monkeypatch):
    patch_repro(monkeypatch, success_prob=1.0, min_age_ticks=0, min_interval_ticks=0,
                check_interval_ticks=10, gestation_period_ticks=20, gestation_step_ticks=10,
                gestation_energy_cost=1.0)
    w, r, pos = _single_region_world()
    r.spawn("Rabbit", pos, init={"energy": 100})
    for _ in range(60):
        w.tick()
    assert len(r.entities_of("Rabbit")) >= 3
