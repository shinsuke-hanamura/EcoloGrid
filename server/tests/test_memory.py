"""記憶システム(経験ログ・長期記憶・整理・忘却・想起 A/B)。docs/tasks/1_17、docs/spec/animal-memory-spec.md。"""
import json
import math
from dataclasses import replace

import pytest

from ecologrid.core import factory
from ecologrid.core.ai import decide, think
from ecologrid.core.config import WorldConfig
from ecologrid.core.grid import Pos
from ecologrid.core.memory import (AVOID, FAIL, GOAL, REPLAY_HOOKS, RULES, STEP, Experience, LongEntry, Memory, Params)
from ecologrid.core.perception import perceive
from ecologrid.core.species import SPECIES
from ecologrid.core.world import World
from helpers import empty_world, land_run, region_for

SEED = 7


def _mem(**kw):
    return Memory(Params(**kw))


def _rec(m, tick, action, outcome, x, y, subject="", reward=None):
    return m.record(SEED, "e1", tick, action, outcome, subject, x, y, reward)


def _long(m, action, outcome, x, y, strength, subject="", count=1.0, reward_sum=None, tick=0):
    d = RULES[(action, outcome)].d
    e = LongEntry(action, outcome, subject, d, x // d, y // d, x, y, count,
                  (RULES[(action, outcome)].reward or 0.0) * count if reward_sum is None else reward_sum, strength, tick, tick)
    m.long[e.key] = e
    m._rebuild_index()
    return e


def _shore(r):
    """この Region の、水に隣接する歩けるセル。"""
    for water in sorted(r.water_cells):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            c = Pos(water.x + dx, water.y + dy)
            if r.walkable(c) and r.x0 <= c.x < r.x0 + r.size and r.y0 <= c.y < r.y0 + r.size:
                return c
    raise AssertionError("no shore")


def _animal(species="Rabbit", **kw):
    w = empty_world(**kw)
    pos = land_run(w, 8)
    r = region_for(w, pos)
    a = r.spawn(species, pos, **({"player_id": "p"} if species == "Player" else {}))
    a.wakefulness = a.wakefulness_max
    a.memory.log.clear()
    a.memory._agg.clear(); a.memory._agg_place.clear()
    return w, r, a, pos


# --- 表(報酬・分類・D) ---
def test_rules_table_values_classes_and_rounding_distances():
    R = RULES
    assert (R[("drink", "drank")].reward, R[("drink", "no_water")].reward) == (10.0, -3.0)
    assert R[("eat", "ate")].reward is None and R[("eat", "no_prey")].reward == -2.0           # ate = 嗜好
    assert (R[("rest", "rested")].reward, R[("sleep", "slept")].reward) == (3.0, 4.0)
    assert (R[("none", "born")].reward, R[("none", "gave_birth")].reward) == (3.0, 8.0)
    assert [R[k].d for k in (("drink", "drank"), ("rest", "rested"), ("eat", "ate"), ("sleep", "slept"))] == [2, 2, 4, 4]
    assert R[("drink", "drank")].cls == GOAL and R[("drink", "no_water")].cls == FAIL and R[("none", "hurt")].cls == AVOID
    assert ("look", "saw_water") not in R                      # 「見る」は action ではない(地図の更新)


# --- 強さ = (1 + |reward|) × 乱数 ---
def test_strength_is_one_plus_abs_reward_times_uniform_and_deterministic():
    m = _mem()
    xs = [_rec(m, t, "drink", "drank", 5, 5).strength for t in range(2000)]
    assert all(0 <= s < 11 for s in xs) and sum(xs) / len(xs) == pytest.approx(5.5, rel=0.06)      # (1 + 10) × 0.5
    ys = [_rec(m, t, "drink", "no_water", 5, 5).strength for t in range(2000)]
    assert all(0 <= s < 4 for s in ys) and sum(ys) / len(ys) == pytest.approx(2.0, rel=0.06)         # (1 + 3) × 0.5
    m2 = _mem()
    assert [_rec(m2, t, "drink", "drank", 5, 5).strength for t in range(50)] == xs[:50]              # 決定論


def test_zero_reward_memory_is_weak_but_not_zero():
    m = _mem()
    zs = [_rec(m, t, "eat", "ate", 5, 5, "Grass", reward=0.0).strength for t in range(500)]
    assert all(0 <= s < 1 for s in zs) and sum(zs) / len(zs) == pytest.approx(0.5, rel=0.15) and min(zs) < 0.1


def test_distance_d_is_stored_per_memory_and_key_uses_rounded_coordinates():
    m = _mem()
    a = _rec(m, 1, "drink", "drank", 4, 4)        # D = 2: 区画 (2, 2)
    b = _rec(m, 2, "drink", "drank", 5, 5)        # 同じ区画 (2, 2)
    c = _rec(m, 3, "drink", "drank", 6, 4)        # 区画 (3, 2)
    e = _rec(m, 4, "eat", "ate", 5, 5, "Grass")   # D = 4: 区画 (1, 1)
    assert (a.d, e.d) == (2, 4) and m.d_values == {2, 4}
    assert len(m._agg) == 3 and m._agg[("drink", "drank", "", 2, 2, 2)].count == 2
    assert m._agg[("drink", "drank", "", 2, 2, 2)].x == 4                    # 最初の記憶の座標を保つ


# --- 記録の接続 ---
def test_hooks_record_drink_eat_rest_sleep_born_and_failures():
    w, r, a, pos = _animal("Fox")
    shore = _shore(r)
    r.move(a, shore)
    assert a.drink(r)
    e = a.memory.log[-1]
    assert (e.action, e.outcome, e.reward) == ("drink", "drank", 10.0) and (e.x, e.y) == (shore.x, shore.y)
    assert e.state is not None and len(e.state) == 8
    r.move(a, Pos(pos.x, pos.y))
    far = [c for c in r.land_cells if not r.adjacent_water(c)][0]
    r.move(a, far)
    r.tick_no += 5
    assert not a.drink(r)
    assert (a.memory.log[-1].action, a.memory.log[-1].outcome) == ("drink", "no_water")        # 失敗の結果も記憶
    prey = r.spawn("Rabbit", far)
    a.preference["Rabbit"] = 6.0
    assert a.eat_here(r)
    e = a.memory.log[-1]
    assert (e.action, e.outcome, e.subject, e.reward) == ("eat", "ate", "Rabbit", 6.0)               # reward = 嗜好
    assert not a.eat_here(r, record_failure=True) and a.memory.log[-1].outcome == "no_prey"
    n = len(a.memory.log)
    assert not a.eat_here(r)                                                                          # 失敗の記録は、明示したときだけ
    assert len(a.memory.log) == n
    assert a.start_rest(r) and a.memory.log[-1].outcome == "rested"


def test_birth_and_gave_birth_are_remembered():
    w, r, a, pos = _animal("Rabbit")
    kid = r.spawn("Rabbit", pos)
    assert [(e.action, e.outcome) for e in kid.memory.log] == [("none", "born")]
    from ecologrid.core.reproduction import _deliver
    mother = r.spawn("Rabbit", pos, init={"birth_tick": -10 ** 7})
    mother.memory.log.clear()
    _deliver(r, mother, SPECIES["Rabbit"].repro, 100)
    assert any((e.action, e.outcome) == ("none", "gave_birth") for e in mother.memory.log)


# --- 整理(入眠・意識消失時のみ): 忘却 → 統合 → 空に ---
def test_organize_happens_on_sleep_and_faint_only():
    w, r, a, pos = _animal("Rabbit")
    a.remember(r, "drink", "drank")
    assert a.memory.organized == 0 and len(a.memory.log) == 1 and not a.memory.long
    a.fall_asleep(r)
    assert a.memory.organized == 1 and not a.memory.log                       # 入眠で整理され、ログは空
    assert any(k[0] == "drink" for k in a.memory.long) and any(k[0] == "sleep" for k in a.memory.long)
    a.set_consciousness("awake")
    a.remember(r, "drink", "drank")
    a._faint(r)
    assert a.memory.organized == 2 and not a.memory.log                       # 意識消失でも整理する
    b = a.memory.organized
    a.set_consciousness("awake"); a.remember(r, "rest", "rested")
    assert a.memory.organized == b and a.memory.log                           # 入眠しなければ整理されない


def test_organize_forgets_before_folding_and_todays_memories_survive_first_night():
    m = _mem(forgetting_factor=0.9, forgetting_threshold=0.5)
    old = _long(m, "drink", "drank", 10, 10, strength=0.52, count=2.0, reward_sum=20.0)       # 0.52 × 0.9 = 0.468 < 0.5
    keep = _long(m, "drink", "drank", 30, 30, strength=5.0, count=1.0, reward_sum=10.0)
    e = _rec(m, 100, "eat", "ate", 50, 50, "Grass", reward=0.0)                              # 今日の記憶(強さ < 1)
    e.strength = 0.3
    m._agg[("eat", "ate", "Grass", 4, 12, 12)].strength = 0.3
    stats = m.organize(200)
    assert stats["forgotten"] == 1 and old.key not in m.long
    assert keep.strength == pytest.approx(4.5) and keep.count == pytest.approx(0.9)
    today = m.long[("eat", "ate", "Grass", 4, 12, 12)]
    assert today.strength == pytest.approx(0.3)                                  # 今日の分は忘却されない(閾値未満でも残る)
    assert not m.log and not m._agg
    m.organize(300)                                                              # 次の整理で、忘却される(0.27 < 0.5)
    assert today.key not in m.long


def test_forgetting_keeps_mean_reward_and_fold_sums_strength_count_reward():
    m = _mem()
    for t, (x, y) in enumerate([(4, 4), (5, 5), (4, 5)]):                          # 同じ区画 (2, 2)
        _rec(m, 10 + t, "drink", "drank", x, y)
    _rec(m, 20, "drink", "no_water", 4, 4)                                         # 別の結果は、別の項目
    sums = (sum(e.strength for e in m.log if e.outcome == "drank"))
    m.organize(100)
    e = m.long[("drink", "drank", "", 2, 2, 2)]
    assert (e.count, e.reward_sum, e.x, e.y, e.first_tick, e.last_tick) == (3, 30.0, 4, 4, 10, 12)
    assert e.strength == pytest.approx(sums) and e.mean_reward == pytest.approx(10.0)
    assert m.long[("drink", "no_water", "", 2, 2, 2)].count == 1                    # 打ち消さず並存する
    m.organize(200)
    assert e.mean_reward == pytest.approx(10.0) and e.count == pytest.approx(2.7)   # 平均報酬は忘却で不変


def test_map_updates_are_in_the_log_but_never_folded_into_long_term():
    m = _mem()
    m.record_map_update(5, 1, [(1, 1, 1)], [])
    _rec(m, 6, "rest", "rested", 3, 3)
    assert len(m.log) == 2 and m.stats()["log_experiences"] == 1
    m.organize(10)
    assert len(m.long) == 1 and not m.log


def test_replay_hook_is_called_before_organizing_with_the_log():
    w, r, a, pos = _animal("Rabbit")
    seen = []
    REPLAY_HOOKS.append(lambda animal, log: seen.append((animal.entity_id, len(log), len(animal.memory.long))))
    try:
        a.remember(r, "drink", "drank")
        a.fall_asleep(r)
    finally:
        REPLAY_HOOKS.clear()
    assert seen and seen[0][1] >= 1 and seen[0][2] == 0               # 整理の直前: ログがあり、長期記憶はまだ空


# --- 想起 A: 強さに比例する選択 ---
def _goal_samples(m, n=3000, subject=None, action="drink"):
    out = {}
    for t in range(n):
        res = m.recall_goal(SEED, "e1", t, action, subject, 0, 0)
        out[None if res is None else (res[0], res[1])] = out.get(None if res is None else (res[0], res[1]), 0) + 1
    return out


def test_recall_goal_is_proportional_to_strength_between_buckets():
    m = _mem()
    _long(m, "drink", "drank", 10, 10, strength=30.0)
    _long(m, "drink", "drank", 40, 40, strength=10.0)
    c = _goal_samples(m)
    total = sum(c.values())
    assert c[(10, 10)] / total == pytest.approx(0.75, abs=0.04) and c[(40, 40)] / total == pytest.approx(0.25, abs=0.04)


def test_failure_representative_excludes_the_bucket_in_proportion_to_strength():
    m = _mem()
    _long(m, "drink", "drank", 10, 10, strength=10.0)
    _long(m, "drink", "no_water", 10, 10, strength=90.0)          # 同じ区画で、失敗の記憶が強い
    c = _goal_samples(m)
    assert c.get((10, 10), 0) / sum(c.values()) == pytest.approx(0.10, abs=0.03)    # 代表が drank になる確率 = 10%
    m2 = _mem()
    _long(m2, "drink", "drank", 10, 10, strength=40.0)
    _long(m2, "drink", "no_water", 10, 10, strength=4.0)
    c2 = _goal_samples(m2)
    assert c2.get((10, 10), 0) / sum(c2.values()) == pytest.approx(40 / 44, abs=0.03)


def test_weak_zero_preference_food_is_still_recalled():
    m = _mem()
    e = _long(m, "eat", "ate", 20, 20, strength=0.4, subject="Grass", reward_sum=0.0)       # 嗜好 0 の食べ物(弱い)
    assert m.recall_goal(SEED, "e1", 1, "eat", None, 0, 0) is not None                      # 想起は弱いが、目標になる
    m2 = _mem()
    _long(m2, "eat", "ate", 20, 20, strength=0.4, subject="Grass", reward_sum=-3.0)        # 嫌い(負の嗜好)でも「目標」
    assert m2.recall_goal(SEED, "e1", 1, "eat", None, 0, 0)[:2] == (20, 20)


def test_recall_goal_is_deterministic_and_empty_returns_none():
    m = _mem()
    assert m.recall_goal(SEED, "e1", 5, "drink", None, 0, 0) is None
    _long(m, "drink", "drank", 10, 10, strength=5.0)
    _long(m, "drink", "drank", 40, 40, strength=5.0)
    assert [m.recall_goal(SEED, "e1", t, "drink", None, 0, 0) for t in range(30)] == \
           [m.recall_goal(SEED, "e1", t, "drink", None, 0, 0) for t in range(30)]


def test_recall_goal_includes_todays_log_and_subject_filter():
    m = _mem()
    _rec(m, 1, "eat", "ate", 50, 50, "Wheat", reward=2.0)
    assert m.recall_goal(SEED, "e1", 2, "eat", "Wheat", 0, 0)[:2] == (50, 50)
    assert m.recall_goal(SEED, "e1", 2, "eat", "Grass", 0, 0) is None


def test_distance_exponent_prefers_near_places():
    m = _mem(recall_distance_exponent=1.0)
    _long(m, "drink", "drank", 2, 2, strength=10.0)
    _long(m, "drink", "drank", 60, 60, strength=10.0)
    c = _goal_samples(m)
    assert c[(2, 2)] > 4 * c.get((60, 60), 0)                          # κ = 1: 近い場所が選ばれやすい


# --- 想起 B: 場所による想起 ---
def test_recall_place_probability_follows_strength():
    m = _mem(recall_refractory_ticks=0)
    for s, expected in ((1.0, 0.181), (5.0, 0.632), (20.0, 0.982)):
        m.long.clear()
        e = _long(m, "drink", "drank", 10, 10, strength=s)
        hits = sum(1 for t in range(2000) if m.recall_place(SEED, "e1", t, 10, 10, commit=False))
        assert hits / 2000 == pytest.approx(expected, abs=0.04), s
    weak, strong = 0, 0
    m.long.clear()
    _long(m, "drink", "drank", 10, 10, strength=1.0)
    _long(m, "rest", "rested", 10, 10, strength=15.0)
    for t in range(1500):
        for e in m.recall_place(SEED, "e1", t, 10, 10, commit=False):
            weak += e.action == "drink"; strong += e.action == "rest"
    assert strong > 3 * weak                                           # 強い記憶ほど思い出しやすい


def test_recall_place_uses_each_memorys_own_d_and_edge_trigger_and_refractory():
    m = _mem(recall_refractory_ticks=600)
    big = _long(m, "drink", "drank", 5, 5, strength=100.0)             # D = 2: 区画 (2, 2)
    far = _long(m, "eat", "ate", 20, 20, strength=100.0, subject="Grass")   # D = 4: 区画 (5, 5)
    assert m.d_values == {2, 4}
    got = m.recall_place(SEED, "e1", 1000, 4, 4)                       # (4, 4): D = 2 → (2, 2) 一致。D = 4 → (1, 1) 不一致
    assert [e.action for e in got] == ["drink"]
    assert m.recall_place(SEED, "e1", 1010, 4, 4) == []                # 区画が変わっていない → 判定しない(エッジ)
    assert m.recall_place(SEED, "e1", 1020, 30, 30) == []              # 離れる
    assert m.recall_place(SEED, "e1", 1100, 5, 5) == []                # 戻っても、不応期(600 tick)の間は思い出さない
    assert m.recall_place(SEED, "e1", 1020 + 30, 30, 30) == []
    assert [e.action for e in m.recall_place(SEED, "e1", 1700, 4, 4)] == ["drink"]                 # 不応期の後
    got2 = m.recall_place(SEED, "e1", 1800, 21, 21)                    # D = 4: (5, 5) 一致
    assert [e.action for e in got2] == ["eat"]


def test_recall_place_per_memory_distance_can_differ():
    m = _mem()
    e = Experience(1, "drink", "drank", "", 9, 9, 3, 10.0, 50.0)        # この記憶だけ D = 3(将来、記憶ごとに変える)
    m._aggregate(e)
    assert m.d_values == {3}
    assert m.recall_place(SEED, "e1", 5, 10, 10, commit=False) or m.recall_place(SEED, "e1", 6, 10, 10, commit=False) \
        or True
    hit = [m.recall_place(SEED, "e1", t, 10, 10, commit=False) for t in range(40)]
    assert any(hit)                                                     # (9, 9) と (10, 10) は、D = 3 で同じ区画 (3, 3)
    assert not any(m.recall_place(SEED, "e1", t, 12, 12, commit=False) for t in range(40))     # (4, 4) の区画 → 一致しない


def test_recall_place_max_three_strongest():
    m = _mem(recall_max=3, recall_refractory_ticks=0)
    for i, s in enumerate((50, 60, 70, 80, 90)):
        _long(m, "drink", "drank", 4, 4, strength=float(s)) if i == 0 else None
    m.long.clear()
    for i, (act, out, s) in enumerate((("drink", "drank", 90.0), ("rest", "rested", 80.0), ("drink", "no_water", 70.0),
                                       ("sleep", "slept", 60.0), ("none", "born", 50.0))):
        d = RULES[(act, out)].d
        m.long[(act, out, "", d, 1 // d, 1 // d)] = LongEntry(act, out, "", d, 1 // d, 1 // d, 1, 1, 1, 1.0, s, 0, 0)
    m._rebuild_index()
    got = m.recall_place(SEED, "e1", 5, 1, 1)
    assert len(got) == 3 and [e.strength for e in got] == sorted((e.strength for e in got), reverse=True)


# --- NPC の判断への利用 ---
def test_thirsty_npc_with_no_known_water_walks_to_recalled_place():
    w, r, a, pos = _animal("Rabbit")
    a.hydration = 20.0
    perceive(a, r)
    target = Pos(pos.x + 3, pos.y)
    a.map.terrain = bytearray(3 if v == 2 else v for v in a.map.terrain)        # 既知の水を、地図からなくす(岩に置き換える)
    assert a.map.known_water_cells() == 0
    _long(a.memory, "drink", "drank", target.x, target.y, strength=20.0)
    # 地図の既知の水がなければ、想起を使う(ここでは経路が地図にあるように、地形を既知にしておく)
    from ecologrid.core.percept_map import T_LAND
    for dx in range(0, 4):
        a.map.set_terrain(pos.x + dx, pos.y, T_LAND)
    d = decide(a, r, use_cache=False)
    assert d.kind == "move" and d.goal == "recalled" and d.target == (target.x, target.y)
    assert d.materials["recalled_goal"] == [target.x, target.y]


def test_recall_disabled_flag_turns_off_goal_recall():
    w, r, a, pos = _animal("Rabbit", recall_enabled=False)
    a.hydration = 20.0
    perceive(a, r)
    _long(a.memory, "drink", "drank", pos.x + 3, pos.y, strength=20.0)
    assert a.recall_goal(r, "drink", None) is None
    assert decide(a, r, use_cache=False).goal != "recalled"


def test_think_records_recalled_places_in_materials():
    w, r, a, pos = _animal("Rabbit")
    _long(a.memory, "rest", "rested", pos.x, pos.y, strength=500.0)
    r.tick_no = 1000
    think(a, r)
    assert [e.action for e in a.recalled] == ["rest"]


# --- memorise_all_steps ---
def test_memorise_all_steps_adds_step_entries_but_not_to_long_term():
    for flag in (False, True):
        w, r, a, pos = _animal("Rabbit", memorise_all_steps=flag)
        for i in range(6):
            r.tick_no = 100 + 25 * i
            think(a, r)
        steps = [e for e in a.memory.log if isinstance(e, Experience) and RULES[(e.action, e.outcome)].cls == STEP]
        assert bool(steps) == flag
        if flag:
            R = a.perception_range
            assert all(len(e.state) == 9 and len(e.state[8]) == (2 * R + 1) ** 2 for e in steps)       # 窓のコピー
        a.fall_asleep(r)
        assert all(k[0] not in ("move", "idle") for k in a.memory.long)                              # 長期記憶には統合しない
        assert not a.memory.log


# --- シリアライズと公開範囲 ---
def test_memory_roundtrip_through_state_json_and_not_public():
    w, r, a, pos = _animal("Rabbit", memorise_all_steps=True)
    a.remember(r, "drink", "drank")
    for i in range(3):
        r.tick_no = 100 + 25 * i
        think(a, r)
    a.fall_asleep(r)
    a.remember(r, "rest", "rested")
    st = a.to_state()
    json.dumps(st)                                                  # JSON にできる(窓のバイト列を含めて)
    b = factory.restore(json.loads(json.dumps(st)))
    assert len(b.memory.long) == len(a.memory.long) and b.memory.organized == a.memory.organized
    assert [e.to_list() for e in b.memory.log if isinstance(e, Experience)] == \
           [e.to_list() for e in a.memory.log if isinstance(e, Experience)]
    assert {k: (e.count, e.reward_sum, e.strength) for k, e in b.memory.long.items()} == \
           {k: (e.count, e.reward_sum, e.strength) for k, e in a.memory.long.items()}
    pub = a.to_dict()
    assert "memory" not in pub and "map" not in pub


def test_memory_and_map_follow_the_individual_across_regions():
    w = World(WorldConfig(seed=1, regions_x=2, regions_y=1, region_size=16, initial_density={}, initial_counts={}))
    r0 = w.regions[(0, 0)]
    pos = next(Pos(15, y) for y in range(16) if all(r0.walkable(Pos(15, y)) for _ in (0,))
               and w.terrain.kind(16, y) == "." and r0.walkable(Pos(15, y)))
    a = r0.spawn("Rabbit", pos)
    a.wakefulness = a.wakefulness_max
    perceive(a, r0)
    a.remember(r0, "drink", "drank")
    r0.tick_no = 1000
    a.next_move_tick = 0
    assert a.try_step("e", r0)
    w._deliver()
    b = next(e for e in w.regions[(1, 0)].entities.values() if e.species == "Rabbit")
    assert b is not a and b.map.known >= 1 and any(e.outcome == "drank" for e in b.memory.log if isinstance(e, Experience))


# --- 統計・全体 ---
def test_world_memory_stats():
    w, r, a, pos = _animal("Rabbit")
    a.remember(r, "drink", "drank")
    a.fall_asleep(r)
    st = w.memory_stats()
    assert st["individuals"] == 1 and st["long_entries"] >= 2 and st["organized"] == 1 and st["memory_bytes_estimate"] > 0


def test_world_with_memory_runs_and_is_deterministic():
    def run():
        w = World(WorldConfig(seed=3, regions_x=2, regions_y=2, region_size=16, initial_counts={"Rabbit": 6, "Fox": 1}))
        w.generate()
        for _ in range(6000):
            w.tick()
        return json.dumps(w.snapshot(), sort_keys=True), json.dumps(w.memory_stats(), sort_keys=True)
    a, b = run(), run()
    assert a == b
    assert json.loads(a[1])["individuals"] > 0
