"""周期アクションの解決のキャッシュ(ActionPlan)。docs/tasks/1_15。"""
import json
from dataclasses import replace

import pytest

from ecologrid.core.actions import (ENTITY, GLOBAL_SCOPE, START_MANUAL, InMemoryActionStore, ScheduledAction,
                                    register_entity_handler, register_handler, region_scope)
from ecologrid.core.config import WorldConfig
from ecologrid.core.species import SPECIES
from ecologrid.core.world import World
from helpers import empty_world, land_run, region_for

calls = []


@register_handler("plan_test_world")
def _world_h(region, act, tick):
    calls.append(("w", act.action_id, region.rid, tick))


@register_entity_handler("plan_test_entity")
def _entity_h(region, act, tick, e):
    calls.append(("e", act.action_id, region.rid, tick, e.entity_id))


def _world(store=None, **kw):
    return World(WorldConfig(regions_x=2, regions_y=1, initial_density={}, initial_counts={}, **kw), actions=store)


class NoVersionStore:
    """`version` を持たないストア(自作のストアの想定)。内容は InMemoryActionStore に委譲。"""
    def __init__(self):
        self._s = InMemoryActionStore()

    def put(self, scope, action): self._s.put(scope, action)
    def remove(self, scope, action_id): return self._s.remove(scope, action_id)
    def list(self, scope): return self._s.list(scope)
    def due(self, scope, tick_no): return self._s.due(scope, tick_no)
    def entity_actions(self, scope): return self._s.entity_actions(scope)


def _ticks(w, n):
    for _ in range(n):
        w.tick()


# --- ストアの版番号 ---
def test_store_version_changes_on_put_and_remove_only():
    s = InMemoryActionStore()
    assert s.version == 0
    s.put(GLOBAL_SCOPE, ScheduledAction("a", "plan_test_world", 5))
    s.put(GLOBAL_SCOPE, ScheduledAction("a", "plan_test_world", 7))        # 置換も版番号が変わる
    assert s.version == 2
    s.list(GLOBAL_SCOPE); s.due(GLOBAL_SCOPE, 7); s.entity_actions(GLOBAL_SCOPE)
    assert s.version == 2                                                   # 読み取りでは変わらない
    assert s.remove(GLOBAL_SCOPE, "a") and s.version == 3
    assert not s.remove(GLOBAL_SCOPE, "a") and s.version == 3               # 何も消えなければ変わらない


# --- 計画の作成・再利用・作り直し ---
def test_plan_is_built_once_and_reused_until_store_changes():
    w = _world()
    r = w.regions[(0, 0)]
    _ticks(w, 50)
    assert r.plan_builds == 1
    w.actions.put(GLOBAL_SCOPE, ScheduledAction("w1", "plan_test_world", 10))
    _ticks(w, 5)
    assert r.plan_builds == 2
    w.actions.remove(GLOBAL_SCOPE, "w1")
    _ticks(w, 5)
    assert r.plan_builds == 3
    st = w.action_cache_status()
    assert st["store_version"] == w.actions.version and st["cached_regions"] == 2 and st["plan_builds"] == 6


def test_clear_action_cache_rebuilds_on_next_tick_and_keeps_state():
    calls.clear()
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("ea", "plan_test_entity", 5, mode=ENTITY, species="Rabbit"))
    w = _world(store)
    pos = land_run(w, 3)
    r = region_for(w, pos)
    e = r.spawn("Rabbit", pos, init={"birth_tick": 0})
    _ticks(w, 12)
    assert [c[3] for c in calls] == [5, 10]
    before = r.plan_builds
    st = w.clear_action_cache()
    assert st["cached_regions"] == 0 and st["clears"] == 1
    assert e.timers == {"ea": 0}                                           # 状態(基準 tick)は消えない
    _ticks(w, 10)
    assert r.plan_builds == before + 1                                      # 次の tick で作り直した
    assert [c[3] for c in calls] == [5, 10, 15, 20]                          # 実行のタイミングは変わらない


def test_clear_with_rebuild_wheels_restores_wheels_from_timers():
    calls.clear()
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("ea", "plan_test_entity", 7, mode=ENTITY, species="Rabbit"))
    w = _world(store)
    pos = land_run(w, 3)
    r = region_for(w, pos)
    r.spawn("Rabbit", pos, init={"birth_tick": 3})
    _ticks(w, 8)
    w.clear_action_cache(rebuild_wheels=True)
    assert r._wheels == {}
    _ticks(w, 16)
    assert [c[3] for c in calls] == [10, 17, 24]                              # anchor 3 から 7 tick ごと
    assert set(r._wheels) == {"ea"}


# --- 反映のタイミング・順序・互換 ---
def test_runtime_changes_take_effect_next_tick():
    calls.clear()
    w = _world()
    pos = land_run(w, 3)
    r = region_for(w, pos)
    r.spawn("Rabbit", pos, init={"birth_tick": 0})
    _ticks(w, 3)
    w.actions.put(GLOBAL_SCOPE, ScheduledAction("w1", "plan_test_world", 2))
    w.actions.put(GLOBAL_SCOPE, ScheduledAction("ea", "plan_test_entity", 4, mode=ENTITY, species="Rabbit"))
    _ticks(w, 6)
    assert [c[3] for c in calls if c[0] == "w" and c[2] == (0, 0)] == [4, 6, 8]     # tick 4 から(登録の次の tick)
    assert [c[3] for c in calls if c[0] == "e"] == [4, 8]                            # 既存の個体にも(anchor 0)
    w.actions.put(GLOBAL_SCOPE, ScheduledAction("w1", "plan_test_world", 2, enabled=False))   # 無効化
    calls.clear()
    _ticks(w, 6)
    assert [c for c in calls if c[0] == "w"] == []
    w.actions.remove(GLOBAL_SCOPE, "ea")                                             # 削除
    calls.clear()
    _ticks(w, 8)
    assert calls == []


def test_execution_order_global_before_region_and_world_before_entity():
    calls.clear()
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("g_e", "plan_test_entity", 1, mode=ENTITY, species="Rabbit"))
    store.put(GLOBAL_SCOPE, ScheduledAction("g_w", "plan_test_world", 1))
    store.put(region_scope((0, 0)), ScheduledAction("r_e", "plan_test_entity", 1, mode=ENTITY, species="Rabbit"))
    store.put(region_scope((0, 0)), ScheduledAction("r_w", "plan_test_world", 1))
    w = _world(store)
    pos = land_run(w, 3)
    r = region_for(w, pos)
    assert r.rid == (0, 0)
    r.spawn("Rabbit", pos, init={"birth_tick": -10})
    calls.clear()
    w.tick()
    order = [c[1] for c in calls if c[2] == (0, 0)]
    assert order == ["g_w", "g_e", "r_w", "r_e"]


def test_unknown_entity_handler_raises_only_when_due():
    store = InMemoryActionStore()
    store.put(GLOBAL_SCOPE, ScheduledAction("bad", "no_such_entity_handler", 5, mode=ENTITY, species="Rabbit"))
    w = _world(store)
    pos = land_run(w, 3)
    region_for(w, pos).spawn("Rabbit", pos, init={"birth_tick": 0})
    _ticks(w, 4)                                                        # 実行される tick の前までは、エラーにならない
    with pytest.raises(KeyError):
        _ticks(w, 1)


def test_store_without_version_still_works_and_matches():
    calls.clear()
    def run(store):
        calls.clear()
        store.put(GLOBAL_SCOPE, ScheduledAction("g_w", "plan_test_world", 3))
        store.put(GLOBAL_SCOPE, ScheduledAction("g_e", "plan_test_entity", 4, mode=ENTITY, species="Rabbit"))
        w = _world(store)
        pos = land_run(w, 3)
        region_for(w, pos).spawn("Rabbit", pos, init={"birth_tick": 0})
        _ticks(w, 20)
        return list(calls), w
    a, _ = run(InMemoryActionStore())
    b, wb = run(NoVersionStore())
    assert a == b and a
    assert wb.action_cache_status()["store_version"] is None


def test_cache_off_path_matches_cached_path():
    def run(cache):
        calls.clear()
        store = InMemoryActionStore()
        store.put(GLOBAL_SCOPE, ScheduledAction("g_w", "plan_test_world", 3))
        store.put(GLOBAL_SCOPE, ScheduledAction("g_e", "plan_test_entity", 4, mode=ENTITY, species="Rabbit"))
        w = _world(store)
        for r in w.regions.values():
            r.cache_actions = cache
        pos = land_run(w, 3)
        region_for(w, pos).spawn("Rabbit", pos, init={"birth_tick": 0})
        _ticks(w, 25)
        return list(calls)
    assert run(True) == run(False) and run(True)


# --- 等価性: キャッシュあり・なしで、世界の結果が完全に一致する ---
def test_cached_and_uncached_worlds_are_identical(monkeypatch):
    """繁殖・妊娠・出産、種子の散布・発芽、睡眠の判定を、短い周期にして回し、スナップショットとイベントが一致すること。"""
    rb = SPECIES["Rabbit"]
    monkeypatch.setitem(SPECIES, "Rabbit", replace(rb, repro=replace(
        rb.repro, min_age_ticks=50, min_interval_ticks=60, check_interval_ticks=40, success_prob=0.5,
        gestation_period_ticks=120, gestation_step_ticks=40, gestation_energy_cost=1.0, litter_size=2),
        sleep_check_interval_ticks=70))
    for key, (disp, germ) in {"Grass": (150, 80), "Wheat": (200, 100)}.items():
        monkeypatch.setitem(SPECIES, key, replace(SPECIES[key], seed_dispersal_interval_ticks=disp,
                                                  germination_ticks=germ))

    def run(cache):
        w = World(WorldConfig(seed=7, regions_x=2, regions_y=2, initial_counts={"Rabbit": 20, "Fox": 2}))
        w.generate()
        for r in w.regions.values():
            r.cache_actions = cache
        _ticks(w, 2_500)
        return json.dumps(w.snapshot(), sort_keys=True), w.last_seq, w.census()
    a, b = run(True), run(False)
    assert a == b
    assert a[2].get("Rabbit", 0) > 20 and a[2]["Seed"] + a[2]["Grass"] > 0          # 繁殖・散布が実際に起きている


# --- スコープの違い ---
def test_region_scoped_action_only_in_that_region_with_plan():
    calls.clear()
    store = InMemoryActionStore()
    store.put(region_scope((1, 0)), ScheduledAction("only10", "plan_test_world", 2))
    w = _world(store)
    _ticks(w, 6)
    assert {c[2] for c in calls} == {(1, 0)} and [c[3] for c in calls] == [2, 4, 6]
