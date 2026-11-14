"""ワールド設定。時間に関する値はすべて tick 数(docs/spec/time-spec.md)。"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .planets import DEFAULT_PLANET, PLANETS, Planet, get_planet

MIN_PERIODIC_TICKS = 20          # 周期的な判定の推奨最短(未満は警告)
TICKS_PER_HOUR_FOR_DAY = 3_600   # 1 日の長さの推奨単位


@dataclass
class WorldConfig:
    seed: int = 1
    region_size: int = 32
    regions_x: int = 4
    regions_y: int = 4
    tick_interval_ms: int = 50        # 1 tick の長さ(実時間)。サーバごとに調整。ゲームの進む速さはこれだけで決まる
    region_planets: Dict[Tuple[int, int], str] = field(default_factory=dict)   # 省略した Region は earth
    event_log_size: int = 20000       # イベントのリングバッファ件数
    command_queue_max: int = 8        # プレイヤー 1 人あたりの未処理コマンド上限
    command_dedup_size: int = 10000   # 冪等判定のために覚える command_id 数
    memorise_all_steps: bool = False    # True: 判断のたびの全ステップを、経験ログ(短期記憶)に追加する(RL の学習用。長期記憶には統合しない)
    recall_enabled: bool = True         # False: 想起(目標指向)を使わない(記憶あり/なしの比較用)
    player_sleep_enabled: bool = True   # False: Player は睡眠・意識消失しない(覚醒度が減らない)
    # 初期配置: 植物は陸セルあたりの密度、動物は世界全体の頭数(陸セルから一様に選ぶ)
    initial_density: Dict[str, float] = field(default_factory=lambda: {"Grass": 0.08, "Wheat": 0.02})
    initial_counts: Dict[str, int] = field(default_factory=lambda: {"Rabbit": 32, "Fox": 4})

    @property
    def width(self) -> int:
        return self.region_size * self.regions_x

    @property
    def height(self) -> int:
        return self.region_size * self.regions_y

    def planet_of(self, rid: Tuple[int, int]) -> Planet:
        return get_planet(self.region_planets.get(tuple(rid), DEFAULT_PLANET))


def validate_config(cfg: WorldConfig) -> List[str]:
    """起動時の検証。不正な値は ValueError、推奨を外れる値は警告(文字列のリスト)を返す。"""
    from .species import SPECIES

    warnings: List[str] = []
    for name in ("region_size", "regions_x", "regions_y"):
        v = getattr(cfg, name)
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"{name} must be a positive integer: {v!r}")
    max_vision = max((sp.perception_range for sp in SPECIES.values() if sp.cls != "Player"), default=0)   # NPC の知覚は担当 Region 内
    if cfg.region_size < 2 * max_vision:
        warnings.append(f"region_size={cfg.region_size} is smaller than twice the largest vision ({max_vision}): "
                        "animals see only inside their own Region, so their view is clipped at Region edges")
    if isinstance(cfg.tick_interval_ms, bool) or not isinstance(cfg.tick_interval_ms, int) or cfg.tick_interval_ms <= 0:
        raise ValueError(f"tick_interval_ms must be a positive integer: {cfg.tick_interval_ms!r}")
    if not 20 <= cfg.tick_interval_ms <= 200:
        warnings.append(f"tick_interval_ms={cfg.tick_interval_ms} is outside the recommended range 20-200")
    for rid, pid in cfg.region_planets.items():
        if pid not in PLANETS:
            raise ValueError(f"region {rid} has unknown planet {pid!r}")
    for p in PLANETS.values():
        if p.day_length_ticks <= 0 or p.start_offset_ticks < 0:
            raise ValueError(f"planet {p.id}: day_length_ticks must be positive, start_offset_ticks non-negative")
        if p.day_length_ticks % TICKS_PER_HOUR_FOR_DAY:
            warnings.append(f"planet {p.id}: day_length_ticks={p.day_length_ticks} is not a multiple of 3600")
    for sp in SPECIES.values():
        periodic = {"seed_dispersal_interval_ticks": sp.seed_dispersal_interval_ticks,
                    "germination_ticks": sp.germination_ticks}
        if sp.repro:
            periodic.update(check_interval_ticks=sp.repro.check_interval_ticks,
                            gestation_step_ticks=sp.repro.gestation_step_ticks)
        for name, v in periodic.items():
            if v and v < MIN_PERIODIC_TICKS:
                warnings.append(f"{sp.key}.{name}={v} is shorter than {MIN_PERIODIC_TICKS} ticks")
        for name in ("hydration_empty_ticks", "energy_empty_ticks", "energy_gain_full_ticks",
                     "deficiency_death_ticks", "regen_full_ticks", "move_interval_ticks"):
            v = getattr(sp, name)
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"{sp.key}.{name} must be a non-negative integer: {v!r}")
        if sp.sleeps:
            _validate_sleep(sp, warnings)
    return warnings


def _validate_sleep(sp, warnings: List[str]) -> None:
    """睡眠・スタミナ・気温のパラメータの検証(docs/spec/animal-states-spec.md §5.2・§6.9)。"""
    k = sp.key
    if sp.wakefulness_decay_ticks <= 0 or sp.wakefulness_recovery_ticks <= 0 or sp.mind <= 0:
        raise ValueError(f"{k}: mind and wakefulness decay/recovery ticks must be positive")
    if sp.sleep_check_interval_ticks < 1 or sp.sleep_cycle_ticks < 1:
        raise ValueError(f"{k}: sleep_check_interval_ticks and sleep_cycle_ticks must be positive")
    if not 0 <= sp.sleep_cycle_amplitude <= 1:
        raise ValueError(f"{k}.sleep_cycle_amplitude must be within 0..1")
    if sp.sleep_pressure_exponent < 1 or sp.sleep_probability_gain <= 0:
        raise ValueError(f"{k}: sleep_pressure_exponent must be >= 1 and sleep_probability_gain > 0")
    if not 0 <= sp.sleep_standard_onset_tick < sp.sleep_cycle_ticks:
        raise ValueError(f"{k}.sleep_standard_onset_tick must be within 0..sleep_cycle_ticks")
    if not 0 < sp.sleep_standard_duration_ticks < sp.sleep_cycle_ticks:
        raise ValueError(f"{k}.sleep_standard_duration_ticks must be within 1..sleep_cycle_ticks")
    if sp.comfort_low_c > sp.comfort_high_c:
        raise ValueError(f"{k}: comfort_low_c must not exceed comfort_high_c")
    if sp.sleep_cycle_ticks % TICKS_PER_HOUR_FOR_DAY or sp.sleep_standard_onset_tick % TICKS_PER_HOUR_FOR_DAY:
        warnings.append(f"{k}: sleep_cycle_ticks / sleep_standard_onset_tick are not multiples of 3600")
