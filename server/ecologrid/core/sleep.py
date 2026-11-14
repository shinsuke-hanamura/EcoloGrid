"""睡眠の計算(純粋な関数)。仕様: docs/spec/animal-states-spec.md §5。

入眠圧 = wakefulness_pressure_gain × (1 − x) + cycle_pressure_gain × sleep_cycle_amplitude × sleep_rhythm
入眠確率 = min(1, sleep_probability_gain × 入眠圧 ^ sleep_pressure_exponent)(1 以上は必ず入眠)
起床確率 = clip₀₁(σ↑(x) + wake_hunger_weight × 飢え + wake_thirst_weight × 渇き)
x は覚醒度の割合(覚醒度 ÷ 最大値)。
"""
import math
from typing import List, Sequence, Tuple

from .actions import ENTITY, GLOBAL_SCOPE, START_SPAWN, ScheduledAction, register_entity_handler
from .species import SPECIES

SLEEP_CHECK = "sleep_check"
HISTORY_SIZE = 5          # 直近の入眠 tick を記録する件数
HOUR_TICKS = 3_600        # 入眠時刻を丸める単位(ゲーム内の 1 時間)


def clip01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def level(value: float, threshold: float) -> float:
    """飢え・渇きのレベル(0〜1)。threshold 未満になるほど 1 に近づく(Energy / 水分 0 で 1)。"""
    return clip01((threshold - value) / threshold) if threshold > 0 else 0.0


def peak_tick(history: Sequence[int], cycle: int, standard: int) -> int:
    """直近の入眠時刻(周期内の位置)。直近 5 回の入眠 tick を周期の余りにして 1 時間に丸め、最頻値を採る。
    最頻値が複数あれば、その中で最も新しい記録の区画。記録がなければ種の標準入眠時刻。"""
    if not history:
        return standard
    nbins = max(1, -(-cycle // HOUR_TICKS))
    counts: dict = {}
    for i, t in enumerate(history):
        b = ((t % cycle) + HOUR_TICKS // 2) // HOUR_TICKS % nbins
        c = counts.get(b, (0, 0))
        counts[b] = (c[0] + 1, i)
    best = max(counts.items(), key=lambda kv: kv[1])[0]
    return best * HOUR_TICKS


def sleep_rhythm(tick: int, cycle: int, peak: int) -> float:
    """睡眠リズム係数(0〜1)。直近の入眠時刻 peak で 1、周期の半分後で 0。"""
    return (1.0 + math.cos(2 * math.pi * ((tick % cycle) - peak) / cycle)) / 2.0


def sleep_pressure(x: float, rhythm: float, spec, hunger: float = 0.0, thirst: float = 0.0) -> float:
    p = (spec.wakefulness_pressure_gain * (1.0 - x)
         + spec.cycle_pressure_gain * spec.sleep_cycle_amplitude * rhythm)
    return p * (1.0 - hunger) * (1.0 - thirst)       # 飢え・渇きが深刻なほど眠りにくい(生存行動を優先)


def sleep_probability(pressure: float, spec) -> float:
    if pressure <= 0:
        return 0.0
    return min(1.0, spec.sleep_probability_gain * pressure ** spec.sleep_pressure_exponent)


def wake_probability(x: float, spec, hunger: float = 0.0, thirst: float = 0.0) -> float:
    z = -spec.wake_steepness * (x - spec.wake_midpoint)
    sig = 0.0 if z > 700 else 1.0 / (1.0 + math.exp(z))
    return clip01(sig + spec.wake_hunger_weight * hunger + spec.wake_thirst_weight * thirst)


# --- 定期アクション(個体単位。入眠・起床の判定) ---
def default_sleep_actions() -> List[Tuple[str, ScheduledAction]]:
    return [(GLOBAL_SCOPE, ScheduledAction(f"{SLEEP_CHECK}:{key}", SLEEP_CHECK, sp.sleep_check_interval_ticks,
                                           mode=ENTITY, species=key, start=START_SPAWN))
            for key, sp in SPECIES.items() if sp.sleeps]


@register_entity_handler(SLEEP_CHECK)
def sleep_check(region, action: ScheduledAction, tick_no: int, a) -> None:
    a.sleep_check(region, tick_no)
