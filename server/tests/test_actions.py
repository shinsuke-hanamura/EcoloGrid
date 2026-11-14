import pytest

from ecologrid.core import factory
from ecologrid.core.actions import (ENTITY, GLOBAL_SCOPE, START_MANUAL, InMemoryActionStore,
                                    ScheduledAction, TimerWheel, get_handler, region_scope,
                                    register_entity_handler, register_handler)
from ecologrid.core.config import WorldConfig
from ecologrid.core.world import World
from helpers import land_run, region_for

calls = []        # world モード: (rid, action_id, tick)
ecalls = []       # entity モード: (entity_id, action_id, tick)


@register_handler("test_record")
def _record(region, action, tick_no):
    calls.append((region.rid, action.action_id, tick_no))


@register_entity_handler("test_erecord")
def _erecord(region, action, tick_no, e):
    ecalls.append((e.entity_id, action.action_id, tick_no))


def _world(store, **kw):
    return World(WorldConfig(regions_x=2, regions_y=1, initial_density={}, **kw), actions=store)


def _ticks(w, n):
    for _ in range(n):
        w.tick()


# ---------- world モード ----------
def test_is_due_mod_and_offset():
    a = ScheduledAction("a", "test_record", 5)
    assert [t for t in range(0, 21) if a.is_due(t)] == [0, 5, 10, 15, 20]
    b = ScheduledAction("b", "test_record", 5, offset=2)
    assert [t for t in range(0, 21) if b.is_due(t)] == [2, 7, 12, 17]
    assert ScheduledAction("b", "test_record", 5, offset=7).is_due(2)        # offset は N で正規化
    assert not ScheduledAction("a", "test_record", 5, enabled=False).is_due(5)
    assert not ScheduledAction("e", "x", 5, mode=ENTITY, species="Rabbit").is_due(5)


def test_validation():
    with pytest.raises(ValueError):
        ScheduledAction("a", "h", 0)
    with pytest.raises(ValueError):
        ScheduledAction("a", "h", 5, mode="bogus")
    with pytest.raises(ValueError):
        ScheduledAction("a", "h", 5, mode=ENTITY)         # species 必須
    with pytest.raises(ValueError):
        ScheduledAction("a", "h", 5, mode=ENTITY, species="Rabbit", start="never")


def test_store_put_replace_remove_list_due():
    s = InMemoryActionStore()
    s.put("g", ScheduledAction("a", "h", 2))
    s.put("g", ScheduledAction("b", "h", 3))
    s.put("g", ScheduledAction("a", "h", 4))          # 同 ID は置換
    s.put("g", ScheduledAction("e", "h", 4, mode=ENTITY, species="Rabbit"))
    assert [(x.action_id, x.interval_ticks) for x in s.list("g")][:2] == [("a", 4), ("b", 3)]
    assert [x.action_id for x in s.due("g", 12)] == ["a", "b"]      # entity モードは due に出ない
    assert [x.action_id for x in s.entity_actions("g")] == ["e"]
    assert s.list("other") == []
    assert s.remove("g", "a") and not s.remove("g", "a")


def test_world_actions_run_on_multiples_in_every_region():
    calls.clear()
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("g4", "test_record", 4))
    store.put(GLOBAL_SCOPE, ScheduledAction("g4o1", "test_record", 4, offset=1))
    w = _world(store)
    _ticks(w, 12)
    assert sorted({t for (_, a, t) in calls if a == "g4"}) == [4, 8, 12]
    assert sorted({t for (_, a, t) in calls if a == "g4o1"}) == [1, 5, 9]
    assert {rid for (rid, _, _) in calls} == {(0, 0), (1, 0)}


def test_region_scope_only_runs_in_that_region():
    calls.clear()
    store = InMemoryActionStore()
    store.put(region_scope((1, 0)), ScheduledAction("only10", "test_record", 3))
    w = _world(store)
    _ticks(w, 9)
    assert {rid for (rid, _, _) in calls} == {(1, 0)}
    assert [t for (_, _, t) in calls] == [3, 6, 9]


def test_actions_added_at_runtime_take_effect():
    calls.clear()
    store = InMemoryActionStore()
    w = _world(store)
    w.tick()
    store.put(GLOBAL_SCOPE, ScheduledAction("late", "test_record", 2))
    w.tick()
    assert {t for (_, _, t) in calls} == {2}


def test_unknown_handler_raises():
    with pytest.raises(KeyError):
        get_handler("no_such_handler")
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("x", "no_such_handler", 1))
    with pytest.raises(KeyError):
        _world(store).tick()


def test_default_actions_registered():
    w = World(WorldConfig(regions_x=1, regions_y=1))
    acts = {a.action_id: a for a in w.actions.list(GLOBAL_SCOPE)}
    assert acts["breed_check:Rabbit"].mode == ENTITY and acts["breed_check:Rabbit"].start == "spawn"
    assert acts["gestation_step:Rabbit"].start == START_MANUAL
    assert "breed_check:Fox" in acts


# ---------- entity モード ----------
def _entity_world(interval=10, start="spawn"):
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("ea", "test_erecord", interval, mode=ENTITY,
                                            species="Rabbit", start=start))
    w = _world(store)
    pos = land_run(w, 3)
    return w, region_for(w, pos), pos


def test_timer_wheel_basics():
    t = TimerWheel(10)
    t.add("a", 3)
    t.add("b", 13)
    t.add("c", -7)            # 負の anchor も mod で同じスロット(-7 % 10 == 3)
    assert t.due(23) == ["a", "b", "c"] and t.due(24) == []
    t.discard("a", 3)
    assert t.due(23) == ["b", "c"]


def test_entity_action_fires_every_N_ticks_from_own_anchor():
    ecalls.clear()
    w, r, pos = _entity_world(10)
    _ticks(w, 3)
    a = r.spawn("Rabbit", pos, init={"birth_tick": r.tick_no})     # anchor=3
    _ticks(w, 4)
    b = r.spawn("Rabbit", pos, init={"birth_tick": r.tick_no})     # anchor=7
    _ticks(w, 30)
    for e, anchor in ((a, 3), (b, 7)):
        ts = [t for (eid, _, t) in ecalls if eid == e.entity_id]
        assert ts == [anchor + 10, anchor + 20, anchor + 30]       # 世界の 10 の倍数ではない
    assert not any(t % 10 == 0 for (eid, _, t) in ecalls if eid == a.entity_id)


def test_manual_start_and_stop_timer():
    ecalls.clear()
    w, r, pos = _entity_world(5, start=START_MANUAL)
    e = r.spawn("Rabbit", pos)
    _ticks(w, 12)
    assert ecalls == []                                  # manual は開始するまで動かない
    r.start_timer(e, "ea")                               # anchor = 12
    _ticks(w, 11)
    assert [t for (_, _, t) in ecalls] == [17, 22]
    r.stop_timer(e, "ea")
    _ticks(w, 10)
    assert len(ecalls) == 2 and "ea" not in e.timers


def test_removed_entity_stops_firing():
    ecalls.clear()
    w, r, pos = _entity_world(4)
    e = r.spawn("Rabbit", pos, init={"birth_tick": 0})
    _ticks(w, 5)
    r.remove(e, "test")
    _ticks(w, 12)
    assert [t for (_, _, t) in ecalls] == [4]


def test_phase_survives_region_handoff():
    ecalls.clear()
    w, r0, pos = _entity_world(10)
    r1 = w.regions[(1, 0)] if r0.rid == (0, 0) else w.regions[(0, 0)]
    _ticks(w, 3)
    e = r0.spawn("Rabbit", pos, init={"birth_tick": 3})
    _ticks(w, 2)
    d = e.to_dict()
    r0.remove(e, "left")                                  # 越境の移動を模擬(状態を辞書で運ぶ)
    r1.add(factory.restore(d), announce=False)
    _ticks(w, 20)
    assert [t for (eid, _, t) in ecalls if eid == d["id"]] == [13, 23]
    assert d["timers"] == {"ea": 3}


def test_entity_action_added_at_runtime_backfills_existing_entities():
    ecalls.clear()
    store = InMemoryActionStore()
    w = _world(store)
    pos = land_run(w, 3)
    r = region_for(w, pos)
    e = r.spawn("Rabbit", pos, init={"birth_tick": 0})
    _ticks(w, 3)
    store.put(GLOBAL_SCOPE, ScheduledAction("late", "test_erecord", 5, mode=ENTITY, species="Rabbit"))
    _ticks(w, 10)
    assert [t for (_, _, t) in ecalls] == [5, 10]        # anchor=0 から数える


def test_changing_interval_rebuilds_wheel():
    ecalls.clear()
    w, r, pos = _entity_world(10)
    e = r.spawn("Rabbit", pos, init={"birth_tick": 0})
    _ticks(w, 2)
    w.actions.put(GLOBAL_SCOPE, ScheduledAction("ea", "test_erecord", 3, mode=ENTITY, species="Rabbit"))
    _ticks(w, 7)
    assert [t for (_, _, t) in ecalls] == [3, 6, 9]


def test_disabled_entity_action_does_not_run():
    ecalls.clear()
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("ea", "test_erecord", 2, mode=ENTITY,
                                            species="Rabbit", enabled=False))
    w = _world(store)
    pos = land_run(w, 3)
    region_for(w, pos).spawn("Rabbit", pos)
    _ticks(w, 10)
    assert ecalls == []
