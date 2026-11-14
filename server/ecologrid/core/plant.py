"""Plant / SeedPlant: 移動不可の生物。状態は遅延評価し、毎 tick の更新をしない。

水分・Energy・Health は、最後に更新した tick(`_state_tick`)からの経過 tick 数から、読み取るとき
(プロパティ)と、予約した周期(`plant_cycle`)にだけ計算する。植物は個体数が多い(数千〜数万)ので、
毎 tick の処理をなくして tick の負荷を下げる(docs/spec/time-spec.md §9)。

- 近く(water_radius 以内)に水があれば水分は満タンのまま。無ければ spec の率で減る(水は静的なので、
  水の近さは Region に束縛した時点で 1 回だけ判定する)。
- Energy は spec の率で増える(光合成)。0 になることはない。
- 水分が 0 の間は Health が減る。死亡の判定と除去は `plant_cycle`(イテレーション中の変更を避けるため、
  プロパティの読み取りでは除去しない)。
- SeedPlant は `plant_cycle`(個体単位の定期アクション。誕生 tick を基準に seed_dispersal_interval_ticks ごと)で
  種子を散布する。
"""
from typing import List, Tuple

from .actions import ENTITY, GLOBAL_SCOPE, START_MANUAL, START_SPAWN, ScheduledAction, register_entity_handler
from .living import LivingBeing
from .seed import GERMINATE, germinate_action_id
from .species import PLANT_KEYS, SPECIES

PLANT_CYCLE = "plant_cycle"


class Plant(LivingBeing):
    _region = None          # bind() で Region に束縛される
    _state_tick = 0
    _near_water = False
    _doomed = False         # 体力が尽きた(除去は plant_cycle で行う)
    _h = _e = _hp = 0.0

    # --- 遅延評価される状態(読み書きの前に経過 tick 分を反映する) ---
    @property
    def hydration(self) -> float:
        self._sync()
        return self._h

    @hydration.setter
    def hydration(self, v: float) -> None:
        self._sync()
        self._h = v

    @property
    def energy(self) -> float:
        self._sync()
        return self._e

    @energy.setter
    def energy(self, v: float) -> None:
        self._sync()
        self._e = v

    @property
    def health(self) -> float:
        self._sync()
        return self._hp

    @health.setter
    def health(self, v: float) -> None:
        self._sync()
        self._hp = v

    def bind(self, region) -> None:
        """Region に追加されたとき: 現在の tick を基準にし、水の近さを 1 回だけ判定する。"""
        self._region = region
        self._state_tick = region.tick_no
        self._near_water = bool(self.spec.water_radius
                                and region.water_within(self.grid_position, self.spec.water_radius))

    def _sync(self, now=None) -> None:
        r = self._region
        if r is None or self.dead:
            return
        now = r.tick_no if now is None else now
        dt = now - self._state_tick
        if dt <= 0:
            return
        self._state_tick = now
        s = self.spec
        self._e = min(s.max_energy, max(0.0, self._e - self._energy_decay * dt))
        deficient = dt if self._e <= 0 else 0           # 欠乏していた tick 数(水分 + Energy)
        if self._near_water:
            self._h = s.max_hydration
        elif self._hydration_decay > 0:
            left = self._h - self._hydration_decay * dt
            if left > 0:
                self._h = left
            else:
                deficient += dt - self._h / self._hydration_decay   # 水分が 0 だった tick 数
                self._h = 0.0
        elif self._h <= 0:
            deficient += dt
        if deficient > 0:
            self._hp -= self._damage * deficient
        else:
            self._hp = min(s.max_health, self._hp + self._regen * dt)
        if self._hp <= 0:
            self._hp = 0.0
            self._doomed = True

    def tick(self, region) -> None:
        """毎 tick の処理はしない(状態は遅延評価、種子散布は plant_cycle)。"""


class SeedPlant(Plant):
    def __init__(self, spec, entity_id, grid_position, entity_name=""):
        super().__init__(spec, entity_id, grid_position, entity_name)
        self.birth_tick = 0   # 誕生 tick。plant_cycle(種子散布の周期)の基準

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["birth_tick"] = self.birth_tick
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self.birth_tick = int(d.get("birth_tick", 0))


@register_entity_handler(PLANT_CYCLE)
def plant_cycle(region, action: ScheduledAction, tick_no: int, p) -> None:
    """誕生 tick から seed_dispersal_interval_ticks ごと: 状態を反映し、枯れていれば除去、
    Energy が足りれば種子を散布する。"""
    p._sync(tick_no)
    if p._doomed:
        region.remove(p, "death")
        return
    s = p.spec
    if p.energy >= s.seed_cost:
        p.energy -= s.seed_cost
        r = s.seed_dispersal_range
        dx, dy = region.rng.randint(-r, r), region.rng.randint(-r, r)
        if dx or dy:
            region.sow(p.species, p.grid_position._replace(x=p.grid_position.x + dx,
                                                           y=p.grid_position.y + dy))


def default_plant_actions() -> List[Tuple[str, ScheduledAction]]:
    """SeedPlant の種ごとに、種子散布の周期アクション(entity モード、誕生 tick 基準)と、
    種子の発芽アクション(entity モード、種子が落ちたときに Region がタイマーを開始する)を登録する。"""
    out = [(GLOBAL_SCOPE, ScheduledAction(f"{PLANT_CYCLE}:{key}", PLANT_CYCLE,
                                          SPECIES[key].seed_dispersal_interval_ticks,
                                          mode=ENTITY, species=key, start=START_SPAWN))
           for key in PLANT_KEYS if SPECIES[key].seed_dispersal_interval_ticks]
    out += [(GLOBAL_SCOPE, ScheduledAction(germinate_action_id(key), GERMINATE,
                                           SPECIES[key].germination_ticks,
                                           mode=ENTITY, species="Seed", start=START_MANUAL))
            for key in PLANT_KEYS if SPECIES[key].germination_ticks]
    return out
