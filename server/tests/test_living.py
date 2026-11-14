from ecologrid.core.species import SPECIES
from helpers import empty_world, land_run, region_for


def _rabbit(w):
    pos = land_run(w)
    r = region_for(w, pos)
    return r, r.spawn("Rabbit", pos)


def test_dehydration_reduces_health():
    w = empty_world()
    r, rb = _rabbit(w)
    rb.hydration = 0
    rb.energy = 100
    h0 = rb.health
    rb.tick(r)
    assert abs((h0 - rb.health) - SPECIES["Rabbit"].damage_per_tick) < 1e-9 and rb.energy > 0


def test_deficiency_death_takes_exactly_configured_ticks():
    """水分 0(または Energy 0)の間、Health 100 → 0 まで deficiency_death_ticks。両方なら半分。"""
    n = SPECIES["Rabbit"].deficiency_death_ticks
    for both, expected in ((False, n), (True, n // 2)):
        w = empty_world()
        r, rb = _rabbit(w)
        rb.hydration, rb.energy = 0, (0 if both else 100)
        ticks = 0
        while not rb.dead:
            if not both:
                rb.energy = 100            # Energy は満たしておく(水分の欠乏だけを測る)
            rb._metabolize(r)
            ticks += 1
        assert abs(ticks - expected) <= 1, (both, ticks, expected)


def test_zero_energy_blocks_movement_and_damages():
    w = empty_world()
    r, rb = _rabbit(w)
    rb.energy = 0
    rb.hydration = 100
    h0 = rb.health
    assert not rb.try_step("e", r)
    rb._metabolize(r)
    assert rb.health < h0


def test_death_emits_removal():
    w = empty_world()
    r, rb = _rabbit(w)
    rb.hydration = 0
    rb.health = 0.001
    rb.tick(r)
    assert rb.entity_id not in r.entities
    assert any(e["type"] == "entity_removed" and e["reason"] == "death" for e in r.events)


def test_health_regenerates_when_fine():
    w = empty_world()
    r, rb = _rabbit(w)
    rb.hydration = rb.energy = 100
    rb.health = 50
    rb._metabolize(r)
    assert abs(rb.health - (50 + SPECIES["Rabbit"].regen_per_tick)) < 1e-9


def _ticks_to_dehydrate(p, r, temperature_c):
    r.temperature_c = temperature_c
    ticks = 0
    while p.hydration > 0 and ticks < 1_000_000:
        p.energy = 100                     # Energy は満たしておく
        p._metabolize(r)
        ticks += 1
    return ticks


def test_player_hydration_depends_on_temperature():
    """水分は気温の指数関数で減る(docs/spec/animal-states-spec.md §6.9)。Player が満タンから 0 になるまで:
    0 ℃で約 80 時間、20 ℃で約 62.8 時間、35 ℃で約 12.3 時間、40 ℃でちょうど 5 時間、100 ℃では 1 tick。"""
    w = empty_world()
    pid = w.add_player("p")
    p = w.player_entity(pid)
    r = w.regions[w.players[pid].region]
    assert p.hydration == 100 and p.energy == 100
    expected = {-20: 287_973, 0: 286_558, 20: 225_929, 35: 44_184, 40: 18_000, 60: 351, 100: 1}
    for temp, ticks in expected.items():
        p.hydration = 100
        got = _ticks_to_dehydrate(p, r, float(temp))
        assert abs(got - ticks) <= max(2, ticks * 0.001), (temp, got, ticks)


def test_hydration_unaffected_by_actions_and_state():
    """水分の消費は気温だけで決まる: 睡眠・休憩・動作中でも同じ(20 ℃)。"""
    w = empty_world()
    r, rb = _rabbit(w)
    rates = []
    for mode in ("awake", "sleeping", "resting", "acting"):
        rb.hydration = 100
        rb.state, rb.resting, rb.last_action_tick = "awake", False, -10**9
        if mode == "sleeping":
            rb.state = "sleeping"
        elif mode == "resting":
            rb.resting = True
        elif mode == "acting":
            rb.last_action_tick = r.tick_no
        rb._metabolize(r)
        rates.append(100 - rb.hydration)
    assert max(rates) - min(rates) < 1e-12


def test_species_tick_values():
    """docs/spec/time-spec.md §4.3 の値(現行の秒 × 648。1 tick = ゲーム内の 1 秒)。"""
    S = SPECIES
    assert S["Player"].energy_empty_ticks == 216_000
    assert (S["Rabbit"].energy_empty_ticks, S["Fox"].energy_empty_ticks) == (162_000, 129_600)
    assert [S[k].hydration_rate_factor for k in ("Player", "Rabbit", "Fox")] == [1.0, 1.2, 1.2]   # 水分は気温で決まる
    assert S["Player"].deficiency_death_ticks == 32_400 and S["Player"].regen_full_ticks == 129_600
    assert (S["Rabbit"].repro.min_age_ticks, S["Rabbit"].repro.gestation_period_ticks) == (777_600, 194_400)
    assert (S["Fox"].repro.min_age_ticks, S["Fox"].repro.gestation_period_ticks) == (1_296_000, 324_000)
    assert (S["Grass"].seed_dispersal_interval_ticks, S["Grass"].germination_ticks) == (97_200, 64_800)
    assert (S["Wheat"].seed_dispersal_interval_ticks, S["Wheat"].germination_ticks) == (162_000, 97_200)
    assert (S["Rabbit"].move_interval_ticks, S["Fox"].move_interval_ticks, S["Player"].move_interval_ticks) == (20, 12, 5)
    assert S["Player"].start_hydration == 100 and S["Player"].start_energy == 100
    assert S["Grass"].energy_decay < 0 and abs(S["Grass"].energy_decay + 100 / 129_600) < 1e-12
