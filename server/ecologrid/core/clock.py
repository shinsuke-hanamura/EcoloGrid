"""暦と時計: 絶対 tick と惑星の属性から、日数・時刻を計算する(換算なし)。

1 tick = ゲーム内の 1 秒。1 分 = 60 tick、1 時間 = 3,600 tick(全惑星で共通)。
1 日の長さは惑星の day_length_ticks。時計は表示のための換算で、ゲームの仕組みは tick だけで動く。
"""
from dataclasses import dataclass

from .planets import Planet

TICKS_PER_SECOND = 1
TICKS_PER_MINUTE = 60
TICKS_PER_HOUR = 3_600


@dataclass(frozen=True)
class GameTime:
    planet: str
    day: int
    hour: int
    minute: int
    second: int
    text: str

    def to_dict(self) -> dict:
        return {"planet": self.planet, "day": self.day, "hour": self.hour,
                "minute": self.minute, "second": self.second, "text": self.text}


def position_in_day(tick: int, planet: Planet) -> int:
    return (tick + planet.start_offset_ticks) % planet.day_length_ticks


def day_number(tick: int, planet: Planet) -> int:
    return (tick + planet.start_offset_ticks) // planet.day_length_ticks + 1


def day_phase(tick: int, planet: Planet) -> float:
    """昼夜・太陽の位置に使う、日の中の位置(0 以上 1 未満)。"""
    return position_in_day(tick, planet) / planet.day_length_ticks


def game_time(tick: int, planet: Planet) -> GameTime:
    pos = position_in_day(tick, planet)
    h, rest = divmod(pos, TICKS_PER_HOUR)
    m, s = divmod(rest, TICKS_PER_MINUTE)
    d = day_number(tick, planet)
    return GameTime(planet.id, d, h, m, s, f"Day {d} {h:02d}:{m:02d}:{s:02d}")


def format_tick(tick: int) -> str:
    """tick 表記(絶対 tick = 世界の開始からのゲーム内の秒数)。"""
    return f"Tick {tick:,}"


def format_duration(ticks: int) -> str:
    """tick 数を「N 日 N 時間 N 分 N 秒」で表す(文書・ログ用)。"""
    d, r = divmod(ticks, 86_400)
    h, r = divmod(r, TICKS_PER_HOUR)
    m, s = divmod(r, TICKS_PER_MINUTE)
    parts = [f"{v} {u}" for v, u in ((d, "日"), (h, "時間"), (m, "分"), (s, "秒")) if v]
    return " ".join(parts) or "0 秒"
