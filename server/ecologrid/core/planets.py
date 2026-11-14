"""惑星(Planet): 時間と物理の属性のまとまり。Region は惑星を持つ。現在は earth のみ。

時間はすべて tick 数で定義する(docs/spec/time-spec.md)。1 tick = ゲーム内の 1 秒。
1 時間の分の数(60)と、1 分・1 時間の tick 数は全惑星で共通(clock.py)。
"""
import math
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class Planet:
    id: str
    name: str
    day_length_ticks: int        # 1 日の長さ(惑星の自転周期に相当)
    start_offset_ticks: int      # 絶対 tick 0 の時点の「日の中の位置」(earth: 0 = 00:00。南中(12:00)の 12 時間前)
    gravity: float               # ボクセル/tick²(tick 単位で定義。無重力は 0)
    temperature_c: float = 20.0               # 気温(℃)。earth の標準は 20 ℃で一定(docs/spec/animal-states-spec.md §6.9)
    temperature_amplitude_c: float = 0.0      # 1 日の変動幅(℃)。0 なら一定
    temperature_peak_ticks: int = 50_400      # 最高気温の日の中の位置(earth: 14:00)

    def temperature_at(self, tick: int) -> float:
        """絶対 tick の気温。変動幅 0 なら temperature_c(計算なし)。"""
        if not self.temperature_amplitude_c:
            return self.temperature_c
        pos = (tick + self.start_offset_ticks) % self.day_length_ticks
        return self.temperature_c + self.temperature_amplitude_c * math.cos(
            2 * math.pi * (pos - self.temperature_peak_ticks) / self.day_length_ticks)


EARTH = Planet("earth", "Earth", day_length_ticks=86_400, start_offset_ticks=0, gravity=0.0245)

PLANETS: Dict[str, Planet] = {p.id: p for p in [EARTH]}
DEFAULT_PLANET = "earth"


def get_planet(planet_id: str) -> Planet:
    try:
        return PLANETS[planet_id]
    except KeyError:
        raise ValueError(f"unknown planet: {planet_id!r}") from None
