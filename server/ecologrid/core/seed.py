"""Seed: 独立したエンティティ。落ちてから germination_ticks 後に PlantType の植物へ発芽する。

毎 tick の処理はしない。発芽は個体単位の定期アクション(`germinate:{植物}`。entity モード、落ちた tick の
1 つ前を基準にして、ちょうど germination_ticks 後に 1 回)で行う(Region._place_seed がタイマーを開始する)。
"""
from .actions import register_entity_handler
from .entity import Entity
from .species import SPECIES

GERMINATE = "germinate"


def germinate_action_id(plant_type: str) -> str:
    return f"{GERMINATE}:{plant_type}"


class Seed(Entity):
    species = "Seed"
    _region = None
    born_tick = 0       # 落ちた tick(Region に束縛されたときの tick)

    def __init__(self, entity_id, grid_position, plant_type: str, entity_name=""):
        super().__init__(entity_id, grid_position, entity_name or f"Seed({plant_type})")
        self.plant_type = plant_type
        self.germination_ticks = SPECIES[plant_type].germination_ticks
        self.dead = False

    def bind(self, region) -> None:
        self._region = region
        self.born_tick = region.tick_no

    @property
    def age(self) -> int:
        """落ちてからの tick 数(表示用。発芽の判定には使わない)。"""
        return max(0, self._region.tick_no - self.born_tick) if self._region is not None else 0

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(plant_type=self.plant_type, age=self.age, born_tick=self.born_tick)
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self.born_tick = int(d.get("born_tick", 0))


@register_entity_handler(GERMINATE)
def germinate_seed(region, action, tick_no: int, seed) -> None:
    region.germinate(seed)
