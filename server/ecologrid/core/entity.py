"""Entity: 全エンティティの基底クラス。

設計書のプロパティ名との対応: EntityName=entity_name / EntityId=entity_id /
GridPosition=grid_position。species は種キー(prey 判定・シリアライズ用)。
"""
from typing import Dict

from .grid import Pos


class Entity:
    species = "Entity"
    ticks = False      # True の Entity だけが毎 tick Region から tick を呼ばれる(動物・Player)。
                       # 植物・種子・卵は毎 tick の処理を持たない(遅延評価・タイマー)

    def __init__(self, entity_id: str, grid_position: Pos, entity_name: str = ""):
        self.entity_id = entity_id
        self.entity_name = entity_name or self.species
        self.grid_position = Pos(*grid_position)
        # 個体単位の定期アクションの基準 tick(action_id -> anchor)。Region 越境でも引き継ぐ
        self.timers: Dict[str, int] = {}

    def tick(self, region) -> None:
        """1 tick 進める。既定では何もしない。"""

    def to_dict(self) -> dict:
        return {
            "id": self.entity_id,
            "name": self.entity_name,
            "species": self.species,
            "pos": [self.grid_position.x, self.grid_position.y],
            **({"timers": dict(self.timers)} if self.timers else {}),
        }

    def to_public(self) -> dict:
        """他のプレイヤーに見せてよい項目(知覚の範囲内の個体として返す)。内部状態は含めない。"""
        return {"id": self.entity_id, "name": self.entity_name, "species": self.species,
                "pos": [self.grid_position.x, self.grid_position.y]}

    def to_state(self) -> dict:
        """内部状態を含む全状態(Region 越境・永続化用)。公開用の to_dict() とは分ける(記憶・嗜好などの内部状態は含めない)。"""
        return self.to_dict()

    def load_state(self, d: dict) -> None:
        self.entity_name = d.get("name", self.entity_name)
        self.grid_position = Pos(*d["pos"])
        self.timers = {k: int(v) for k, v in d.get("timers", {}).items()}
