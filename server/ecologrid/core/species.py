"""種ごとのパラメータ(データ駆動)。数値は暫定で、バランス調整の対象。

時間に関する値はすべて tick 数(1 tick = ゲーム内の 1 秒。docs/spec/time-spec.md)。
水分・Energy は「満タンから 0 まで何 tick か」、植物の Energy は「0 から満タンまで何 tick か」で定義し、
1 tick あたりの変化量は 100 ÷ tick 数。移動は move_interval_ticks(N tick で 1 ボクセル)。
prey は種キーで定義する(設計書の「Entity IDで定義」を種単位に読み替え)。
"""
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple

VIVIPAROUS, OVIPAROUS, FISSION = "viviparous", "oviparous", "fission"

# 動物の水分の消費は気温だけで決まる(docs/spec/animal-states-spec.md §6.9)。1 tick あたり、満タンに対する割合:
#   FLOOR + R40 × exp(K × (T − 40))   (Player 基準。種の係数 hydration_rate_factor を掛ける)
# 0 ℃以下は FLOOR にほぼ一定(80 時間)、40 ℃で 5 時間、35 ℃から急に立ち上がり、100 ℃以上では 1 tick で蒸発。
HYDRATION_RATE_FLOOR = 1 / 288_000
HYDRATION_RATE_40 = 1 / 19_200
HYDRATION_TEMP_K = 0.2


@lru_cache(maxsize=4096)
def hydration_rate_fraction(temperature_c: float) -> float:
    return HYDRATION_RATE_FLOOR + HYDRATION_RATE_40 * math.exp(min(50.0, HYDRATION_TEMP_K * (temperature_c - 40.0)))


@dataclass(frozen=True)
class ReproductionDef:
    """繁殖ルール。時間はすべて tick(絶対値)単位。

    check_interval_ticks ごと(tick_no % N == 0)に、条件を満たす個体が success_prob で子作りに成功する。
    - 胎生/卵生: 成功すると妊娠し、gestation_step_ticks ごとに gestation_energy_cost を消費して
      育つ。gestation_period_ticks 経過で出産(胎生: 子が生まれる / 卵生: 卵を産み、
      incubation_ticks 後に孵化)。Energy 不足なら流産(卵は失われる)。
    - 分裂: 成功すると即座に Energy を 1/2 ずつにして 2 体になる。
    """
    type: str
    min_age_ticks: int            # 産める最低年齢
    min_interval_ticks: int       # 前回の出産(分裂)からの最短間隔
    min_energy: float             # 子作り判定に必要な最低 Energy
    check_interval_ticks: int     # 子作り判定の周期
    success_prob: float           # 判定 1 回あたりの成功率(0..1)
    gestation_period_ticks: int = 0
    gestation_step_ticks: int = 1
    gestation_energy_cost: float = 0.0   # 育つ 1 ステップあたりの消費 Energy
    litter_size: int = 1
    offspring_energy: float = 40.0       # 生まれた子の初期 Energy
    incubation_ticks: int = 0            # 卵生のみ: 産卵から孵化まで


@dataclass(frozen=True)
class SpeciesDef:
    key: str
    cls: str                      # core.factory の CLASSES のキー
    max_hydration: float = 100.0
    max_energy: float = 100.0
    max_health: float = 100.0
    start_hydration: float = 80.0
    start_energy: float = 80.0
    hydration_empty_ticks: int = 0      # 水分が満タンから 0 になるまで(0 = 減らない)
    energy_empty_ticks: int = 0         # Energy が満タンから 0 になるまで(0 = 減らない)
    energy_gain_full_ticks: int = 0     # 植物: Energy が 0 から満タンになるまで(0 = 増えない)
    deficiency_death_ticks: int = 32_400    # 水分または Energy が 0 の間、Health 100 → 0 まで(欠乏 1 つ。両方なら半分)
    regen_full_ticks: int = 129_600         # 両方 > 0 の間、Health 0 → 100 まで
    # Animal
    move_interval_ticks: int = 0        # N tick で 1 ボクセル(0 = 動かない)
    move_energy_cost: float = 0.0       # 1 歩あたり
    prey: Tuple[str, ...] = ()
    # 食べ物ごとの吸収性(0..1)と嗜好(`ate` の reward)。種の標準値(個体の発生時にコピーする)。docs/spec/animal-memory-spec.md §9
    absorption: float = 0.0               # 食べ物に共通の既定値(得る Energy・毒性(将来)を吸収する割合)
    absorption_by_food: Tuple[Tuple[str, float], ...] = ()    # 食べ物の種ごとの上書き
    preference: float = 0.0               # 嗜好の既定値(好きなら正、嫌いなら負)。`ate` の reward になる
    preference_by_food: Tuple[Tuple[str, float], ...] = ()
    # 体制の係数と、できる action(体制 sleep_stance。種の標準値を、個体の発生時に、個体の dict としてコピーする。
    # 将来は遺伝子)。名前は <状態名>_factors / <状態名>_actions。仕様: docs/spec/animal-states-spec.md、core/state.py
    sleep_stance_factors: Tuple[Tuple[str, str], ...] = (("wakefulness", "x * 0.2"),)     # パラメータ名 -> 式(x = 適用前の値)
    sleep_stance_actions: Tuple[Tuple[str, bool], ...] = (("move", False), ("eat", False), ("drink", False))
    perception_range: int = 0           # 知覚の範囲(セル)。個体の発生時にコピー(将来は遺伝子)。有効な範囲 = これ × 覚醒度の割合
    perception_interval_ticks: int = 20  # 知覚して地図を更新する周期。個体の発生時にコピー
    thirst_threshold: float = 50.0
    hunger_threshold: float = 60.0
    drink_amount: float = 5.0
    # --- 動物の状態・能力値(docs/spec/animal-states-spec.md)。wakefulness_per_mind が 0 の種は睡眠しない ---
    strength: float = 0.0
    mind: float = 0.0                       # 初期精神力(覚醒度の最大値 = mind × wakefulness_per_mind)
    attack_coef: float = 0.0
    defense_coef: float = 0.0
    wakefulness_per_mind: float = 0.0
    wakefulness_decay_ticks: int = 0        # 覚醒度が満タンから 0 になるまで(覚醒中。行動によらず一定)
    wakefulness_recovery_ticks: int = 0     # 覚醒度が 0 から満タンになるまで(睡眠中)
    sleep_check_interval_ticks: int = 600   # 入眠・起床の判定の間隔
    consciousness_regain_level: float = 0.1     # 意識消失から睡眠に移る覚醒度の割合
    # 入眠圧(§5.2)
    sleep_cycle_ticks: int = 86_400             # 標準入眠周期(地球の動物は 1 日)
    sleep_cycle_amplitude: float = 0.3
    sleep_pressure_exponent: float = 20.0
    sleep_probability_gain: float = 0.1
    wakefulness_pressure_gain: float = 1.0
    cycle_pressure_gain: float = 1.0
    sleep_standard_onset_tick: int = 0          # 周期内の位置(earth では tick 0 = 00:00 なので時刻の位置)
    sleep_standard_duration_ticks: int = 28_800  # 簡略計算モード(将来)用
    # 起床確率(§5.3)
    wake_midpoint: float = 0.95
    wake_steepness: float = 35.0
    wake_hunger_weight: float = 0.1
    wake_thirst_weight: float = 0.15
    # スタミナ・休憩(§6)
    rest_regen_full_ticks: int = 28_800     # 休憩・睡眠中に体力が 0 → 満タンになるまで(スタミナは 10 倍の速さ)
    stamina_recovery_multiplier: float = 10.0
    stamina_cost_move: float = 0.005
    stamina_cost_eat: float = 0.025
    stamina_cost_drink: float = 0.01
    idle_after_ticks: int = 60              # 行動してからこの tick 以上たつと「静止」
    rest_start_ratio: float = 0.3           # NPC: スタミナまたは体力がこの割合(対 max_health)未満で休憩
    rest_end_ratio: float = 0.95            # NPC: スタミナと体力がこの割合以上で休憩を終える
    # 記憶(docs/spec/animal-memory-spec.md)。種の標準値を、発生時に個体へコピーする(将来は遺伝子)。当面は全種同じ
    recall_tau: float = 5.0                 # 場所による想起の確率 p = 1 − exp(−強さ/τ) の τ
    recall_max: int = 3                     # 一度に思い出す記憶の件数の上限
    recall_refractory_ticks: int = 600      # 同じ記憶を再び想起しない期間
    recall_distance_exponent: float = 0.0   # 目標指向の想起の距離の割引 κ(0 = 強さだけに比例)
    forgetting_factor: float = 0.9          # 入眠のたびに、長期記憶の強さ・件数・報酬の合計にかける係数(1 = 忘れない)
    forgetting_threshold: float = 0.5       # 強さがこれを下回った長期記憶を削除
    # 気温(§6.9)
    hydration_rate_factor: float = 0.0      # > 0 なら水分は気温の指数関数で減る(0 = hydration_empty_ticks の一定の減少)
    comfort_low_c: float = 10.0             # 快適な気温の範囲。外れた度数に比例してスタミナを消費
    comfort_high_c: float = 30.0
    stamina_temp_drain_per_degree: float = 0.0005
    # Plant / SeedPlant
    seed_dispersal_range: int = 0
    seed_dispersal_interval_ticks: int = 0
    seed_cost: float = 0.0
    water_radius: int = 0         # この距離内に水があれば Hydration が満たされる
    # 発芽(Seed が参照)
    germination_ticks: int = 0
    # 繁殖(Animal のみ。None なら繁殖しない)
    repro: Optional[ReproductionDef] = None

    # --- 1 tick あたりの変化量(導出値) ---
    @property
    def hydration_decay(self) -> float:
        return self.max_hydration / self.hydration_empty_ticks if self.hydration_empty_ticks else 0.0

    @property
    def energy_decay(self) -> float:
        """正なら減少、負なら増加(光合成)。"""
        if self.energy_gain_full_ticks:
            return -self.max_energy / self.energy_gain_full_ticks
        return self.max_energy / self.energy_empty_ticks if self.energy_empty_ticks else 0.0

    def absorption_of(self, food: str) -> float:
        return dict(self.absorption_by_food).get(food, self.absorption)

    def preference_of(self, food: str) -> float:
        return dict(self.preference_by_food).get(food, self.preference)

    @property
    def sleeps(self) -> bool:
        return self.wakefulness_per_mind > 0

    @property
    def wakefulness_max(self) -> float:
        return self.mind * self.wakefulness_per_mind

    @property
    def wakefulness_decay_per_tick(self) -> float:
        return self.wakefulness_max / self.wakefulness_decay_ticks if self.wakefulness_decay_ticks else 0.0

    @property
    def wakefulness_recovery_per_tick(self) -> float:
        return self.wakefulness_max / self.wakefulness_recovery_ticks if self.wakefulness_recovery_ticks else 0.0

    @property
    def rest_regen_per_tick(self) -> float:
        return self.max_health / self.rest_regen_full_ticks

    @property
    def damage_per_tick(self) -> float:
        return self.max_health / self.deficiency_death_ticks

    @property
    def regen_per_tick(self) -> float:
        return self.max_health / self.regen_full_ticks


# 値は docs/spec/time-spec.md §4.3(現行の秒の値 × 648)と docs/spec/animal-states-spec.md。動物の水分は気温で決まる(§6.9)。暫定でバランス調整の対象。
SPECIES = {s.key: s for s in [
    SpeciesDef("Rabbit", "Animal", energy_empty_ticks=162_000, hydration_rate_factor=1.2,
               strength=10, mind=20, attack_coef=0.5, defense_coef=0.3, wakefulness_per_mind=5.0,
               wakefulness_decay_ticks=91_800, wakefulness_recovery_ticks=41_400,
               sleep_standard_onset_tick=36_000, sleep_standard_duration_ticks=25_200,
               rest_regen_full_ticks=25_200, comfort_low_c=5.0, comfort_high_c=28.0,
               move_interval_ticks=20, move_energy_cost=0.0185,
               prey=("Wheat", "Grass"), absorption=0.5, perception_range=6,
               repro=ReproductionDef(VIVIPAROUS, min_age_ticks=777_600, min_interval_ticks=388_800,
                                     min_energy=60, check_interval_ticks=16_200, success_prob=0.3,
                                     gestation_period_ticks=194_400, gestation_step_ticks=16_200,
                                     gestation_energy_cost=2.0, litter_size=2,
                                     offspring_energy=40)),
    SpeciesDef("Fox", "Animal", energy_empty_ticks=129_600, hydration_rate_factor=1.2,
               strength=35, mind=25, attack_coef=1.2, defense_coef=0.6, wakefulness_per_mind=4.0,
               wakefulness_decay_ticks=88_200, wakefulness_recovery_ticks=46_800,
               sleep_standard_onset_tick=28_800, sleep_standard_duration_ticks=28_800,
               comfort_low_c=0.0, comfort_high_c=28.0,
               move_interval_ticks=12, move_energy_cost=0.0223,
               prey=("Rabbit", "Wheat", "Grass"), absorption=0.6, perception_range=8,
               repro=ReproductionDef(VIVIPAROUS, min_age_ticks=1_296_000, min_interval_ticks=1_036_800,
                                     min_energy=70, check_interval_ticks=32_400, success_prob=0.2,
                                     gestation_period_ticks=324_000, gestation_step_ticks=32_400,
                                     gestation_energy_cost=3.0, litter_size=1,
                                     offspring_energy=50)),
    SpeciesDef("Player", "Player", start_hydration=100, start_energy=100,
               energy_empty_ticks=216_000, hydration_rate_factor=1.0,
               strength=50, mind=50, attack_coef=1.0, defense_coef=0.8, wakefulness_per_mind=2.0,
               wakefulness_decay_ticks=88_200, wakefulness_recovery_ticks=46_800,
               sleep_standard_onset_tick=79_200, sleep_standard_duration_ticks=28_800,
               move_interval_ticks=5, move_energy_cost=0.0062,
               prey=("Fox", "Rabbit", "Wheat"), absorption=0.6,
               perception_range=20, drink_amount=10.0),
    SpeciesDef("Grass", "SeedPlant", start_hydration=80, start_energy=40,
               hydration_empty_ticks=1_296_000, energy_gain_full_ticks=129_600,
               seed_dispersal_range=2, seed_dispersal_interval_ticks=97_200, seed_cost=10.0,
               water_radius=6, germination_ticks=64_800),
    SpeciesDef("Wheat", "SeedPlant", start_hydration=80, start_energy=60,
               hydration_empty_ticks=1_296_000, energy_gain_full_ticks=162_000,
               seed_dispersal_range=2, seed_dispersal_interval_ticks=162_000, seed_cost=15.0,
               water_radius=6, germination_ticks=97_200),
]}

PLANT_KEYS = ("Grass", "Wheat")
