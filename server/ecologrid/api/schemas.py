from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

CommandType = Literal["move", "eat", "drink", "rest", "wait", "respawn"]
NAME_MAX_LEN = 24


class GameTimeInfo(BaseModel):
    planet: str
    day: int
    hour: int
    minute: int
    second: int
    text: str = Field(description="時刻表記(例: Day 3 14:05:30)。tick 表記はクライアントが tick から作る")


class JoinRequest(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LEN)


class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX_LEN, description="新しい名前。他のプレイヤーが使用中なら 409。マップ上の表示は 1 文字目")


class JoinResponse(BaseModel):
    player_id: str
    token: str = Field(description="以降の API に Authorization: Bearer で付ける")
    name: str
    world_id: str
    tick: int


class PlayerStatus(BaseModel):
    player_id: str
    name: str
    alive: bool
    region: Optional[List[int]] = None
    entity: Optional[Dict[str, Any]] = None
    world_id: str
    tick: int
    game_time: GameTimeInfo


class CommandRequest(BaseModel):
    command_id: str = Field(min_length=1, max_length=64, description="クライアント生成。同じ ID は 1 度だけ処理(冪等)")
    type: CommandType
    args: Dict[str, Any] = Field(default_factory=dict)


class CommandResponse(BaseModel):
    status: Literal["queued", "duplicate"]
    command_id: str


class EventsResponse(BaseModel):
    world_id: str
    tick: int
    game_time: GameTimeInfo
    next_seq: int = Field(description="次回の since に渡す値")
    events: List[Dict[str, Any]]


class RegionInfo(BaseModel):
    region: List[int]
    planet: str = Field(description="この Region の惑星(重力・1 日の長さ・時計が決まる)")
    url: Optional[str] = Field(None, description="担当サーバ URL。null は自サーバ(Phase 1 は常に null)")


class RegionsResponse(BaseModel):
    world_id: str
    region_size: int
    regions: List[RegionInfo]


class Health(BaseModel):
    status: str
    tick: int = Field(description="絶対 tick(世界の開始からのゲーム内の秒数)")
    game_time: GameTimeInfo
    players: int
    tick_errors: int


class WorldConfigInfo(BaseModel):
    seed: int = Field(description="地形の seed")
    width: int = Field(description="世界の幅(セル)")
    height: int = Field(description="世界の高さ(セル)")
    region_size: int = Field(description="1 Region の一辺(セル)")
    regions_x: int
    regions_y: int


class TickInfo(BaseModel):
    tick_interval_ms: int = Field(description="1 tick の長さ(実時間、ms)。サーバごとに調整。ゲームの進む速さを決める唯一の値")


class PlanetInfo(BaseModel):
    name: str
    day_length_ticks: int = Field(description="1 日の長さ(tick)。1 日の時間数は day_length_ticks ÷ ticks_per_hour")
    start_offset_ticks: int = Field(description="絶対 tick 0 の時点の日の中の位置(earth: 0 = 00:00)")
    gravity: float = Field(description="重力(ボクセル/tick²)")
    temperature_c: float = Field(description="気温(℃)。earth の標準は 20 ℃で一定")
    temperature_amplitude_c: float = Field(description="1 日の気温の変動幅(℃)。0 なら一定")


class TimeUnits(BaseModel):
    ticks_per_second: int
    ticks_per_minute: int
    ticks_per_hour: int


class SpeciesInfo(BaseModel):
    move_interval_ticks: int = Field(description="N tick で 1 ボクセル(0 = 動かない)")
    max_health: float
    strength: float
    mind: float = Field(description="初期精神力。覚醒度の最大値 = mind × wakefulness_per_mind")
    wakefulness_per_mind: float
    wakefulness_decay_ticks: int = Field(description="覚醒度が満タンから 0 になるまで(覚醒中)")
    wakefulness_recovery_ticks: int = Field(description="覚醒度が 0 から満タンになるまで(睡眠中)")
    attack_coef: float
    defense_coef: float
    sleep_cycle_ticks: int
    sleep_cycle_amplitude: float
    sleep_pressure_exponent: float
    sleep_probability_gain: float
    sleep_standard_onset_tick: int
    sleep_standard_duration_ticks: int
    sleep_check_interval_ticks: int
    wake_midpoint: float
    wake_steepness: float
    stamina_cost_move: float
    stamina_cost_eat: float
    stamina_cost_drink: float
    comfort_low_c: float
    comfort_high_c: float
    perception_range: int = Field(description="知覚の範囲(セル)の標準値。有効な範囲 = これ × 覚醒度の割合")
    perception_interval_ticks: int = Field(description="知覚して地図を更新する周期(tick)の標準値")
    absorption: float = Field(description="食べ物に共通の吸収性の既定値(0〜1)。得る Energy = 獲物の Energy × 吸収性")
    preference: float = Field(description="嗜好の既定値。`ate` の reward になる")


class LimitsInfo(BaseModel):
    max_players: int
    name_max_length: int
    command_queue_max: int = Field(description="プレイヤー 1 人あたりの未処理コマンド上限(超えると 429)")
    command_dedup_size: int = Field(description="冪等判定のために覚える直近の command_id 数")
    max_poll_wait_sec: float = Field(description="events の wait の上限(秒)")
    event_log_size: int = Field(description="サーバが保持するイベント件数(古い since は 410)")
    player_idle_timeout_sec: float = Field(description="API に触れない時間がこれを超えるとプレイヤーは退出")


class SystemConfig(BaseModel):
    api_version: str
    world_id: str = Field(description="起動ごとに変わる。値が変わったら世界がリセットされている(設定も再取得する)")
    join_key_required: bool = Field(description="true の場合、POST /players に X-Join-Key が必要")
    tick: TickInfo
    time_units: TimeUnits
    planets: Dict[str, PlanetInfo]
    species: Dict[str, SpeciesInfo]
    world: WorldConfigInfo
    limits: LimitsInfo
    command_types: List[str]
    directions: List[str] = Field(description="move の dir に指定できる値")
    cell_kinds: Dict[str, str] = Field(description="snapshot の terrain 文字 → 意味")


class ClearActionCacheRequest(BaseModel):
    rebuild_wheels: bool = Field(
        False, description="true: 個体単位アクションのタイマーホイールも捨て、各個体の timers から作り直す(不整合の復旧用)")


class ActionCacheStatus(BaseModel):
    store_version: Optional[int] = Field(None, description="アクションのストアの版番号(持たないストアは null)")
    regions: int
    cached_regions: int = Field(description="アクション計画のキャッシュを持つ Region 数(クリア直後は 0。次の tick で作り直す)")
    plan_builds: int = Field(description="これまでにアクション計画を作った回数(全 Region の合計)")
    clears: int = Field(description="管理 API でクリアした回数")
