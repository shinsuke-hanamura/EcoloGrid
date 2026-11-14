"""Egg: 卵生の動物が産む卵。incubation_ticks 経過で孵化する(Phase 1 では捕食・踏みつけ対象外)。

毎 tick の処理はしない。孵化は個体単位の定期アクション(`hatch:{親の種}`。entity モード、産卵の tick を基準に
ちょうど incubation_ticks 後に 1 回)で行う(産卵時に reproduction がタイマーを開始する)。
"""
from .actions import register_entity_handler
from .entity import Entity

HATCH = "hatch"


def hatch_action_id(parent_species: str) -> str:
    return f"{HATCH}:{parent_species}"


class Egg(Entity):
    species = "Egg"

    def __init__(self, entity_id, grid_position, parent_species: str, laid_tick: int,
                 incubation_ticks: int, entity_name=""):
        super().__init__(entity_id, grid_position, entity_name or f"Egg({parent_species})")
        self.parent_species = parent_species
        self.laid_tick = laid_tick
        self.incubation_ticks = incubation_ticks
        self.dead = False

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(parent_species=self.parent_species, laid_tick=self.laid_tick,
                 incubation_ticks=self.incubation_ticks)
        return d


@register_entity_handler(HATCH)
def hatch_egg(region, action, tick_no: int, egg) -> None:
    region.hatch(egg)
