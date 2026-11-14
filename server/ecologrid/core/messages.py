"""コマンド(入力)とメッセージ(Region 間)の型。"""
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Command:
    command_id: str            # クライアント生成の UUID。同じ ID は 1 度しか処理しない
    player_id: str
    type: str                  # move / eat / drink / rest / wait / respawn
    args: Dict[str, Any] = field(default_factory=dict)


COMMAND_TYPES = ("move", "eat", "drink", "rest", "wait", "respawn")
