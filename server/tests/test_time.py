"""時間の仕様(docs/spec/time-spec.md): 1 tick = ゲーム内の 1 秒、暦と時計、惑星、設定の検証、移動の間隔。"""
import json

import pytest

from ecologrid.core.clock import (TICKS_PER_HOUR, TICKS_PER_MINUTE, TICKS_PER_SECOND, format_duration,
                                  format_tick, game_time, day_phase)
from ecologrid.core.config import WorldConfig, validate_config
from ecologrid.core.grid import Pos
from ecologrid.core.messages import Command
from ecologrid.core.planets import EARTH, PLANETS, Planet, get_planet
from ecologrid.core.world import World
from helpers import empty_world, land_run, region_for


def test_time_units_and_earth():
    assert (TICKS_PER_SECOND, TICKS_PER_MINUTE, TICKS_PER_HOUR) == (1, 60, 3600)
    assert EARTH.day_length_ticks == 86_400 == 24 * TICKS_PER_HOUR      # 1 日 = 24 時間 = 86,400 tick
    assert EARTH.start_offset_ticks == 0                                 # tick 0 = 00:00(南中 12:00 の 12 時間前)
    assert EARTH.gravity == 0.0245
    assert list(PLANETS) == ["earth"] and get_planet("earth") is EARTH
    with pytest.raises(ValueError):
        get_planet("mars")


def test_game_time_clock_on_earth():
    assert game_time(0, EARTH).text == "Day 1 00:00:00"
    assert game_time(1, EARTH).text == "Day 1 00:00:01"
    assert game_time(60, EARTH).text == "Day 1 00:01:00"
    assert game_time(3_600, EARTH).text == "Day 1 01:00:00"
    assert game_time(43_200, EARTH).text == "Day 1 12:00:00"             # 南中
    assert game_time(86_400 - 1, EARTH).text == "Day 1 23:59:59"
    assert game_time(86_400, EARTH).text == "Day 2 00:00:00"
    gt = game_time(259_200, EARTH)                                       # 3 日後の同時刻
    assert (gt.day, gt.hour, gt.minute, gt.second) == (4, 0, 0, 0)
    assert gt.planet == "earth" and gt.to_dict()["text"] == "Day 4 00:00:00"


def test_absolute_tick_is_seconds_since_start():
    """絶対 tick は世界の開始からのゲーム内の経過秒数(Tick 259,200 = ちょうど 3 日)。"""
    a, b = game_time(0, EARTH), game_time(259_200, EARTH)
    assert (b.day - a.day, b.hour, b.minute, b.second) == (3, a.hour, a.minute, a.second)
    assert format_tick(259_200) == "Tick 259,200"
    assert format_duration(259_200) == "3 日"
    assert format_duration(32_400) == "9 時間" and format_duration(3_240) == "54 分"
    assert format_duration(640) == "10 分 40 秒" and format_duration(0) == "0 秒"


def test_day_phase_and_other_planet_clock():
    assert day_phase(0, EARTH) == 0.0 and day_phase(43_200, EARTH) == 0.5    # 00:00 は日の始まり、南中は日の半分
    p36 = Planet("test36", "Test 36h", day_length_ticks=129_600, start_offset_ticks=0, gravity=0.01)
    assert game_time(129_600 - 1, p36).text == "Day 1 35:59:59"          # 36 時間の日は時が 24 を超える
    assert game_time(129_600, p36).text == "Day 2 00:00:00"
    # 生理は日の長さによらない: 同じ tick 数は、どの惑星でも同じ長さの時間(32,400 tick = 9 時間)
    assert game_time(32_400, p36).hour == 9


def test_config_validation():
    assert validate_config(WorldConfig()) == []
    for bad in (0, -1, 50.5, "50", True):
        with pytest.raises(ValueError):
            validate_config(WorldConfig(tick_interval_ms=bad))
    assert any("recommended" in w for w in validate_config(WorldConfig(tick_interval_ms=500)))
    assert validate_config(WorldConfig(tick_interval_ms=20)) == [] and validate_config(WorldConfig(tick_interval_ms=200)) == []
    with pytest.raises(ValueError):
        validate_config(WorldConfig(region_planets={(0, 0): "mars"}))
    with pytest.raises(ValueError):
        World(WorldConfig(tick_interval_ms=0))


def test_regions_have_planets():
    w = World(WorldConfig(regions_x=2, regions_y=1))
    assert {r.planet.id for r in w.regions.values()} == {"earth"}
    assert all(r.gravity == 0.0245 for r in w.regions.values())
    snap = w.snapshot()
    assert [r["planet"] for r in snap["regions"]] == ["earth", "earth"]
    assert w.game_time((0, 0)).text == "Day 1 00:00:00" and w.absolute_tick == 0
    w.tick()
    assert w.absolute_tick == 1 and w.game_time((1, 0)).text == "Day 1 00:00:01"


def test_tick_interval_does_not_affect_simulation():
    """1 tick の長さ(実時間)は待ち時間だけを変える。tick で数えた結果は変わらない。"""
    def run(ms):
        w = World(WorldConfig(seed=5, regions_x=2, regions_y=2, tick_interval_ms=ms))
        w.generate()
        for _ in range(400):
            w.tick()
        return json.dumps(w.snapshot(), sort_keys=True)
    assert run(50) == run(100) == run(25)


def _steps(w, pid, ticks, direction):
    """direction へ move コマンドを毎 tick 送り、移動した tick を返す。"""
    moved, last = [], w.player_entity(pid).grid_position
    for i in range(ticks):
        w.submit(Command(f"{direction}{i}", pid, "move", {"dir": direction}))
        w.tick()
        p = w.player_entity(pid)
        if p.grid_position != last:
            moved.append(w.tick_no)
            last = p.grid_position
    return moved


def test_player_moves_at_most_every_5_ticks():
    w = empty_world()
    pid = w.add_player("p")
    p = w.player_entity(pid)
    r = w.regions[w.players[pid].region]
    # 東へ 3 マス以上ある陸の左端に置く
    pos = land_run(w, 8)
    r.remove(p, "test")
    q = region_for(w, pos).spawn("Player", pos, player_id=pid, name="p")
    w.players[pid].entity_id, w.players[pid].region = q.entity_id, region_for(w, pos).rid
    moved = _steps(w, pid, 40, "e")
    assert len(moved) >= 3
    gaps = [b - a for a, b in zip(moved, moved[1:])]
    assert all(g == 5 for g in gaps), gaps               # 5 tick に 1 ボクセル(それより速くない)


def test_rabbit_moves_at_most_every_20_ticks_and_fox_12():
    w = empty_world()
    pos = land_run(w, 6)
    pos = Pos(pos.x + 1, pos.y)                      # 領域の端から離れた位置で、東西に往復させる
    r = region_for(w, pos)
    for sp, n in (("Rabbit", 20), ("Fox", 12)):
        a = r.spawn(sp, pos)
        times, direction = [], "e"
        for _ in range(400):
            r.tick_no += 1
            before = a.grid_position
            if a.try_step(direction, r):
                times.append(r.tick_no)
                direction = "w" if direction == "e" else "e"
            a.energy = 100
        gaps = [y - x for x, y in zip(times, times[1:])]
        assert gaps and min(gaps) >= n and max(gaps) == n, (sp, gaps[:5])
        r.remove(a, "test")


def test_ai_acts_once_per_interval_and_phases_are_spread():
    """AI は move_interval_ticks に 1 回だけ考える。初期個体の行動の位相は散らされる。"""
    w = World(WorldConfig(initial_counts={"Rabbit": 60}))      # 既定 4x4 Region に、ウサギ 60 匹
    w.generate()
    phases = [e.next_action_tick % 20 for r in w.regions.values() for e in r.entities_of("Rabbit")]
    assert len(phases) > 40 and len(set(phases)) >= 14   # 20 通りの位相に散る
    ids = {e.entity_id for r in w.regions.values() for e in r.entities_of("Rabbit")}
    acts, per_tick = {i: 0 for i in ids}, []
    for _ in range(20):                                  # 20 tick の間に、各ウサギはちょうど 1 回だけ考える
        before = {e.entity_id: e.next_action_tick for r in w.regions.values() for e in r.entities_of("Rabbit")}
        w.tick()
        n = 0
        for r in w.regions.values():
            for e in r.entities_of("Rabbit"):
                if e.entity_id in before and e.next_action_tick != before[e.entity_id]:
                    acts[e.entity_id] += 1
                    n += 1
        per_tick.append(n)
    assert set(acts.values()) == {1}, sorted(set(acts.values()))
    assert max(per_tick) < len(ids) * 0.3                # 1 tick に全員が集中しない
