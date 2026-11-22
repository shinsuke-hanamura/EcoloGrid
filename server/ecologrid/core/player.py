"""Player: ユーザーが操作する Animal。移動は move コマンドの pending_move で行う。"""
from typing import Optional

from .animal import Animal


class Player(Animal):
    def __init__(self, spec, entity_id, grid_position, entity_name="", player_id: str = ""):
        super().__init__(spec, entity_id, grid_position, entity_name)
        self.player_id = player_id
        self.pending_move: Optional[str] = None
        self._last_stats = None

    def act(self, region) -> None:
        if not self.action_allowed("move"):
            self.pending_move = None               # 眠っている・入眠体制の間は動けない(未処理の移動は破棄)
        elif self.pending_move and (self.energy <= 0 or self.stamina < self.spec.stamina_cost_move):
            self.pending_move = None
        elif self.pending_move and region.tick_no >= self.next_move_tick:
            self.try_step(self.pending_move, region)   # 失敗(壁)でも消費して破棄
            self.pending_move = None
        wmax = self.wakefulness_max
        pct = int(100 * self.effective("wakefulness", self.wakefulness / wmax)) if wmax else 100   # 体制の係数をかけた後の値
        stats = (int(self.hydration), int(self.energy), int(self.health), int(self.stamina),
                 pct, tuple(self.state.active()), self.resting)
        if stats != self._last_stats and not self.dead:
            self._last_stats = stats
            region.emit("stats_changed", [region.rid], player_id=self.player_id,
                        id=self.entity_id, hydration=stats[0], energy=stats[1],
                        health=stats[2], stamina=stats[3], wakefulness_pct=stats[4],
                        state=list(stats[5]), resting=stats[6])

    def after_enter(self, region) -> None:
        pass   # Player の捕食は eat コマンドで明示的に行う

    def to_public(self) -> dict:
        d = super().to_public()
        d["player_id"] = self.player_id
        return d

    def on_death(self, region) -> None:
        super().on_death(region)
        region.emit("player_died", [region.rid], player_id=self.player_id, id=self.entity_id)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["player_id"] = self.player_id
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self.player_id = d.get("player_id", "")
