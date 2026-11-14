from ecologrid.core.grid import Pos
from ecologrid.core.species import SPECIES
from helpers import empty_world, land_run, region_for


def test_move_costs_energy_and_respects_interval():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    rb = r.spawn("Rabbit", pos)
    n = SPECIES["Rabbit"].move_interval_ticks
    assert n == 20
    e0 = rb.energy
    assert rb.try_step("e", r)
    assert rb.grid_position == Pos(pos.x + 1, pos.y)
    assert abs((e0 - rb.energy) - SPECIES["Rabbit"].move_energy_cost) < 1e-9
    assert rb.next_move_tick == r.tick_no + n
    assert not rb.try_step("e", r)         # 同じ tick では動けない
    r.tick_no += n - 1
    assert not rb.try_step("e", r)         # N tick に満たない
    r.tick_no += 1
    assert rb.try_step("e", r)             # ちょうど N tick 後に動ける
    assert any(e["type"] == "entity_moved" and e["duration_ticks"] == n for e in r.events)


def test_eat_prey_gains_absorbed_energy():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    fox = r.spawn("Fox", pos)
    rabbit = r.spawn("Rabbit", pos)
    fox.energy = 20
    rabbit.energy = 50
    assert fox.eat_here(r)
    assert rabbit.entity_id not in r.entities
    assert abs(fox.energy - (20 + 50 * SPECIES["Fox"].absorption)) < 1e-6


def test_non_prey_not_eaten():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    rb = r.spawn("Rabbit", pos)
    fox = r.spawn("Fox", pos)
    assert not rb.eat_here(r)        # Rabbit は Fox を食べない
    assert fox.entity_id in r.entities


def test_player_cannot_eat_grass():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    p = r.spawn("Player", pos, player_id="p")
    r.spawn("Grass", pos)
    assert not p.eat_here(r)


def test_walking_onto_seed_tramples_it():
    w = empty_world()
    pos = land_run(w)
    r = region_for(w, pos)
    rb = r.spawn("Rabbit", pos)
    seed = r.spawn("Seed", Pos(pos.x + 1, pos.y), plant_type="Grass")
    rb.try_step("e", r)
    assert seed.entity_id not in r.entities
