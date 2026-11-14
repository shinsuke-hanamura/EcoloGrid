"""LivingBeing: Hydration / Energy / Health を持つ生物。"""
from .entity import Entity
from .species import SpeciesDef


class LivingBeing(Entity):
    toxicity = 0.0     # 毒性(将来。食べたとき、吸収性に応じて吸収される。当面は効果なし)

    def __init__(self, spec: SpeciesDef, entity_id, grid_position, entity_name=""):
        self.spec = spec
        self.species = spec.key
        super().__init__(entity_id, grid_position, entity_name)
        self.hydration = spec.start_hydration
        self.energy = spec.start_energy
        self.max_health = spec.max_health      # 体力(個体の値。現状は種の値で一定)
        self.health = self.max_health
        self.dead = False
        # 1 tick あたりの変化量(spec の tick 数から導出)
        self._hydration_decay = spec.hydration_decay
        self._energy_decay = spec.energy_decay
        self._damage = spec.damage_per_tick
        self._regen = spec.regen_per_tick

    def tick(self, region) -> None:
        self._metabolize(region)

    def _metabolize(self, region) -> None:
        """1 tick 分の代謝。水分・Energy は tick 数で決まる率で減り(植物の Energy は増え)、
        0 の間は 1 つにつき deficiency_death_ticks で Health 100 → 0(両方なら 2 倍の速さ)。"""
        s = self.spec
        self.hydration = min(s.max_hydration, max(0.0, self.hydration - self._hydration_decay_now(region)))
        self.energy = min(s.max_energy, max(0.0, self.energy - self._energy_decay))
        damage = 0.0
        if self.hydration <= 0:           # 脱水症状
            damage += self._damage
        if self.energy <= 0:              # 飢餓
            damage += self._damage
        if damage:
            self.health -= damage
        else:
            self.health = min(self.max_health, self.health + self._regen_now(region))
        if self.health <= 0 and not self.dead:
            self.health = 0.0
            self.dead = True
            self.on_death(region)

    def _hydration_decay_now(self, region) -> float:
        """この tick の水分の減少量。既定は種の一定の減少(動物は気温で決まる: Animal が上書き)。"""
        return self._hydration_decay

    def _regen_now(self, region) -> float:
        """この tick の体力の回復量(欠乏がないとき)。動物は状態で変わる: Animal が上書き。"""
        return self._regen

    def on_death(self, region) -> None:
        region.remove(self, "death")

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(hydration=round(self.hydration, 2), energy=round(self.energy, 2),
                 health=round(self.health, 2))
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self.hydration, self.energy, self.health = d["hydration"], d["energy"], d["health"]
