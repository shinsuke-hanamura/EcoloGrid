"""睡眠の計算(入眠圧・起床確率・直近の入眠時刻・睡眠リズム)。docs/spec/animal-states-spec.md §5。"""
import math
from dataclasses import replace

import pytest

from ecologrid.core.sleep import (clip01, level, peak_tick, sleep_pressure, sleep_probability, sleep_rhythm,
                                  wake_probability)
from ecologrid.core.species import SPECIES

PL = SPECIES["Player"]
DAY = 86_400


# --- 直近の入眠時刻(最頻値・1 時間丸め) ---
def test_peak_tick_defaults_to_species_standard_when_no_records():
    assert peak_tick([], DAY, 79_200) == 79_200


def test_peak_tick_rounds_to_hour_and_takes_mode():
    h = 3_600
    # 22:10, 21:50(どちらも 22 時), 23:40(24 時 = 0 時), 22:20, 22:05 → 22 時が 4 回
    hist = [22 * h + 600 + DAY, 21 * h + 3_000 + 2 * DAY, 23 * h + 2_400, 22 * h + 1_200 + 3 * DAY, 22 * h + 300]
    assert peak_tick(hist, DAY, 0) == 22 * h


def test_peak_tick_tie_takes_most_recent_bin():
    h = 3_600
    assert peak_tick([22 * h, 23 * h], DAY, 0) == 23 * h          # 1 回ずつ → 新しい記録
    assert peak_tick([22 * h, 23 * h, 22 * h, 23 * h], DAY, 0) == 23 * h
    assert peak_tick([23 * h, 22 * h], DAY, 0) == 22 * h


def test_peak_tick_wraps_midnight_without_averaging_to_noon():
    h = 3_600
    hist = [23 * h + 1_900, 0 * h + 1_700 + DAY]       # 23:31 → 24 時 = 0 時、00:28 → 0 時
    assert peak_tick(hist, DAY, 12 * h) == 0             # 平均(昼の 12 時)にならない


def test_peak_tick_rounding_boundary():
    assert peak_tick([1_799], DAY, 0) == 0               # 29 分 59 秒は切り捨て
    assert peak_tick([1_800], DAY, 0) == 3_600           # 30 分は切り上げ


# --- 睡眠リズム係数 ---
def test_sleep_rhythm_is_one_at_peak_and_zero_opposite():
    peak = 79_200
    assert sleep_rhythm(peak, DAY, peak) == pytest.approx(1.0)
    assert sleep_rhythm(peak + DAY // 2, DAY, peak) == pytest.approx(0.0, abs=1e-12)
    assert sleep_rhythm(peak + DAY // 4, DAY, peak) == pytest.approx(0.5)
    assert sleep_rhythm(peak + 5 * DAY, DAY, peak) == pytest.approx(1.0)       # 周期で繰り返す
    for dt in (1_000, 20_000, 40_000):                                         # 頂点の前後で対称
        assert sleep_rhythm(peak - dt, DAY, peak) == pytest.approx(sleep_rhythm(peak + dt, DAY, peak))


# --- 入眠圧と入眠確率 ---
def test_pressure_table_from_spec():
    # amplitude 0.3: x = 0.3 → 頂点 1.00、頂点の反対側 0.70。x = 0 → 1.30 / 1.00
    assert sleep_pressure(0.3, 1.0, PL) == pytest.approx(1.0)
    assert sleep_pressure(0.3, 0.0, PL) == pytest.approx(0.7)
    assert sleep_pressure(0.9, 1.0, PL) == pytest.approx(0.4)
    assert sleep_pressure(0.0, 1.0, PL) == pytest.approx(1.3)
    assert sleep_pressure(0.0, 0.0, PL) == pytest.approx(1.0)


def test_probability_table_from_spec():
    # 係数 0.1・n = 20: 入眠圧 → 確率
    expected = {0.8: 0.0012, 0.9: 0.0122, 1.0: 0.1, 1.05: 0.265, 1.1: 0.673}
    for pr, p in expected.items():
        assert sleep_probability(pr, PL) == pytest.approx(p, abs=0.0015), pr
    assert sleep_probability(0.0, PL) == 0.0 and sleep_probability(-1, PL) == 0.0
    threshold = (1 / PL.sleep_probability_gain) ** (1 / PL.sleep_pressure_exponent)
    assert threshold == pytest.approx(1.122, abs=0.001)
    assert sleep_probability(threshold + 1e-6, PL) == 1.0           # 入眠確率が 1 以上 → 必ず入眠
    assert sleep_probability(1.3, PL) == 1.0                         # 上限は 1


def test_probability_is_one_formula_regardless_of_pressure_threshold():
    """入眠圧が 1 以上か未満かで場合分けしない: gain × P^n。"""
    spec = replace(PL, sleep_probability_gain=0.5, sleep_pressure_exponent=2.0)
    for pr in (0.5, 0.99, 1.0, 1.01):
        assert sleep_probability(pr, spec) == pytest.approx(min(1.0, 0.5 * pr ** 2))


def test_hunger_and_thirst_suppress_sleep_pressure():
    base = sleep_pressure(0.3, 1.0, PL)
    assert sleep_pressure(0.3, 1.0, PL, hunger=0.5) == pytest.approx(base * 0.5)
    assert sleep_pressure(0.3, 1.0, PL, hunger=1.0) == 0.0
    assert sleep_pressure(0.3, 1.0, PL, thirst=1.0) == 0.0
    assert sleep_probability(sleep_pressure(0.0, 1.0, PL, hunger=1.0), PL) == 0.0


def test_pressure_gains_scale_the_two_terms():
    spec = replace(PL, wakefulness_pressure_gain=0.5, cycle_pressure_gain=2.0)
    assert sleep_pressure(0.4, 0.5, spec) == pytest.approx(0.5 * 0.6 + 2.0 * 0.3 * 0.5)


def test_pressure_rises_as_wakefulness_falls_and_near_peak():
    xs = [1.0, 0.8, 0.6, 0.4, 0.2, 0.0]
    p = [sleep_probability(sleep_pressure(x, 0.5, PL), PL) for x in xs]
    assert p == sorted(p)
    assert sleep_probability(sleep_pressure(0.4, 1.0, PL), PL) > sleep_probability(sleep_pressure(0.4, 0.0, PL), PL)


# --- 起床確率 ---
def test_wake_probability_table_from_spec():
    expected = {0.7: 0.0, 0.8: 0.005, 0.85: 0.029, 0.9: 0.148, 0.95: 0.5, 1.0: 0.852}
    for x, p in expected.items():
        assert wake_probability(x, PL) == pytest.approx(p, abs=0.002), x
    assert wake_probability(0.0, PL) == pytest.approx(0.0, abs=1e-12)


def test_wake_probability_hunger_thirst_added_and_clipped():
    assert wake_probability(0.5, PL, thirst=0.5) == pytest.approx(0.075, abs=1e-6)       # +0.15 × 0.5
    assert wake_probability(0.5, PL, hunger=1.0, thirst=1.0) == pytest.approx(0.25, abs=1e-6)
    assert wake_probability(1.0, PL, hunger=1.0, thirst=1.0) == 1.0                       # 0〜1 に収まる


def test_level_and_clip():
    assert level(60, 60) == 0.0 and level(0, 60) == 1.0 and level(30, 60) == pytest.approx(0.5)
    assert level(100, 60) == 0.0
    assert clip01(-1) == 0 and clip01(2) == 1 and clip01(0.3) == 0.3
    assert math.isfinite(wake_probability(-100.0, PL))
