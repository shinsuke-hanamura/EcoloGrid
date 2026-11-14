"""Matter: 物質。Phase 1 は固定(IsStatic=True)。

Phase 1 の岩・水は地形(core.terrain)のセル種別として表現し、全セルを Entity 化しない
(1 万を超えるため)。このクラスは Phase 2 以降でメッシュ単位の物質を扱うときの受け皿。
"""
from .entity import Entity


class Matter(Entity):
    matter_type = "Matter"
    is_static = True

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(matter_type=self.matter_type, is_static=self.is_static)
        return d


class Rock(Matter):
    species = "Rock"
    matter_type = "Rock"


class Water(Matter):
    species = "Water"
    matter_type = "Water"
