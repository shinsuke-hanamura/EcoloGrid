"""NPC 動物(Fox / Rabbit)の AI。個体が知覚した地図(PerceptMap)を見て判断(decide)し、実行(execute)する。

世界(Region)へ直接問い合わせず、地図(+ 現在の知覚)だけで判断する(docs/spec/animal-memory-spec.md §3.1・§4.4.1)。
判断は副作用のない関数(decide)で、行動の提案(Decision)を返す。NPC は execute で実行する。
PC(人間)は decide の結果を実行せず、「NPC ならこう行動する」という助言として使う(docs/tasks/1_19)。

優先順位: 休憩の継続/開始 → 喉が渇いていれば水へ → 空腹なら(食事が可能なとき)獲物へ → それ以外は徘徊。
目標へは、地図の既知の陸と、未知のセル(通れると仮定する)を通る経路探索(BFS)で向かう(障害物を迂回できる)。捕食は「空腹で、かつ食事が可能
(can_eat)」なときだけ(docs/spec/animal-states-spec.md §1)。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .grid import Pos
from .pathfinding import bfs_path
from .perception import perceive, scan_mobile
from .species import PLANT_KEYS


ROUTE_RETRY_TICKS = 300     # 経路が見つからなかった目標を、再探索するまでの tick


@dataclass
class Decision:
    kind: str                                   # rest_continue / rest_start / drink / eat / move / wander
    goal: str = ""                              # water / food / prey / recalled
    reason: str = ""                            # 判断の理由(助言に使う)
    target: Optional[Tuple[int, int]] = None    # 目標のセル(経路がないときの貪欲な移動に使う)
    path: Optional[List[str]] = None            # 経路探索の結果(方向 n/e/s/w のリスト)
    goal_key: Optional[tuple] = None            # 経路を使い回す目標の識別
    end_rest: bool = False                      # 休憩を終えてから、この行動をする
    materials: dict = field(default_factory=dict)   # 判断の材料(欲求・知覚の範囲・見えたもの・思い出した記憶)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "goal": self.goal, "reason": self.reason,
                "target": list(self.target) if self.target else None,
                "path_length": None if self.path is None else len(self.path), "end_rest": self.end_rest,
                "materials": self.materials}


def _route(a, region, goal_key, is_goal, use_cache: bool) -> Optional[List[str]]:
    """地図の既知の陸だけを通る、最寄りの目標までの経路。目標が同じ間は、前回の経路(残り)を使い回す。"""
    m = a.map
    if m is None:
        return None
    if use_cache:
        if a.path and a.path_goal == goal_key:
            return list(a.path)
        until = a.route_fail.get(goal_key)
        if until is not None and region.tick_no < until:
            return None                                # 最近、この目標への経路が見つからなかった(再探索を間引く)
    pos = a.grid_position
    path = bfs_path((pos.x, pos.y), m.is_passable_optimistic, is_goal, max_nodes=2000)   # 未知のセルは、通れると仮定する
    if path is None and use_cache:
        if len(a.route_fail) > 32:
            a.route_fail.clear()
        a.route_fail[goal_key] = region.tick_no + ROUTE_RETRY_TICKS
    return path


def _go_to(a, region, goal: str, key: tuple, target: Tuple[int, int], reason: str, use_cache: bool, mat: dict) -> Decision:
    path = _route(a, region, key, lambda x, y: (x, y) == target, use_cache)
    return Decision("move", goal=goal, reason=reason, target=target, path=path or None, goal_key=key, materials=mat)


def _thirst(a, region, mat: dict, use_cache: bool) -> Optional[Decision]:
    m = a.map
    pos = a.grid_position
    if m is not None:
        if m.next_to_water(pos.x, pos.y):
            return Decision("drink", goal="water", reason="水の隣にいる", materials=mat)
        path = _route(a, region, ("water",), lambda x, y: m.next_to_water(x, y), use_cache)
        if path:
            mat["known_water_cells"] = m.known_water_cells()
            return Decision("move", goal="water", reason="地図の最寄りの水へ(経路探索)", path=path,
                            goal_key=("water",), materials=mat)
    rec = a.recall_goal(region, "drink", None) if hasattr(a, "recall_goal") else None
    if rec is not None:
        mat["recalled_goal"] = list(rec)
        return _go_to(a, region, "recalled", ("recall", "drink", rec[0], rec[1]), rec,
                      "覚えている水の場所へ(想起)", use_cache, mat)
    return None


def _hunger(a, region, mat: dict, use_cache: bool) -> Optional[Decision]:
    s = a.spec
    m = a.map
    pos = a.grid_position
    here = [e for e in region.entities_at(pos) if e is not a and e.species in s.prey and not e.dead]
    if here and a.can_eat():
        return Decision("eat", goal="food", reason="足元に獲物がいる", materials=mat)
    mobile = scan_mobile(a, region)               # 現在の知覚(動く個体は地図に残さない)
    mat["seen_mobile"] = [(sp, x, y) for sp, x, y, _ in mobile[:5]]
    for sp in s.prey:                             # prey の優先順
        if sp in PLANT_KEYS:
            if m is None:
                continue
            res = m.nearest_resource((sp,), pos.x, pos.y)
            if res is not None:
                return _go_to(a, region, "food", ("food", res[0], res[1]), (res[0], res[1]),
                              f"地図の最寄りの {sp} へ", use_cache, mat)
        else:
            for msp, x, y, _ in mobile:
                if msp == sp:
                    return _go_to(a, region, "prey", ("prey", sp, x, y), (x, y), f"{sp} を追う", use_cache, mat)
    rec = a.recall_goal(region, "eat", None) if hasattr(a, "recall_goal") else None
    if rec is not None:
        mat["recalled_goal"] = list(rec)
        return _go_to(a, region, "recalled", ("recall", "eat", rec[0], rec[1]), rec,
                      "覚えている食べ物の場所へ(想起)", use_cache, mat)
    return None


def decide(a, region, use_cache: bool = True) -> Decision:
    """行動を判断する(副作用なし)。use_cache = True のとき、個体が持つ経路(a.path)を使い回す。"""
    s = a.spec
    mat = {"r": a.effective_range(region), "hydration": round(a.hydration, 1), "energy": round(a.energy, 1),
           "stamina": round(a.stamina, 1), "health": round(a.health, 1)}
    end_rest = False
    full = s.rest_end_ratio * a.max_health
    if a.resting:
        if a.is_hungry() or a.is_thirsty() or not a.is_safe() or (a.stamina >= full and a.health >= full):
            end_rest = True
        else:
            return Decision("rest_continue", reason="休憩を続ける", materials=mat)
    else:
        low = s.rest_start_ratio * a.max_health
        exhausted = a.stamina < s.stamina_cost_move       # 動けないほど疲れている
        if a.is_safe() and (exhausted or ((a.stamina < low or a.health < low)
                                          and not a.is_hungry() and not a.is_thirsty())):
            return Decision("rest_start", reason="スタミナ・体力が低い", materials=mat)
    for need, fn in ((a.is_thirsty, _thirst), (a.is_hungry, _hunger)):
        if need():
            d = fn(a, region, mat, use_cache)
            if d is not None:
                d.end_rest = end_rest
                return d
    return Decision("wander", reason="欲求なし(または目標が分からない)", end_rest=end_rest, materials=mat)


def execute(a, region, d: Decision) -> None:
    if d.end_rest:
        a.stop_rest(region)
    k = d.kind
    if k == "rest_continue":
        return
    if k == "rest_start":
        a.start_rest(region)
    elif k == "drink":
        a.path = []
        a.drink(region)
    elif k == "eat":
        a.path = []
        a.eat_here(region, record_failure=True)
    elif k == "move":
        if d.path:
            a.path, a.path_goal = list(d.path), d.goal_key
            if a.try_step(a.path[0], region):
                a.path.pop(0)
            else:
                a.path = []                           # 塞がれた・動けない → 次回は再計算
        elif d.target is not None:
            a.path = []
            a.step_toward(Pos(*d.target), region)     # 経路が見つからないときの、軸方向の貪欲な移動
    else:                                             # wander
        a.path = []
        if region.rng.random() < 0.6:
            a.random_step(region)


def think(a, region) -> None:
    if a.map is None:                                 # 最初の判断の前に、いまの知覚で地図を作る
        perceive(a, region)
        a.next_perception_tick = region.tick_no + max(1, a.perception_interval_ticks)
    pos = a.grid_position
    a.recalled = a.memory.recall_place(region.cfg.seed, a.entity_id, region.tick_no, pos.x, pos.y)   # 場所による想起
    d = decide(a, region)
    if a.recalled:
        d.materials["recalled"] = [e.to_dict() for e in a.recalled]
    before = (pos.x, pos.y)
    execute(a, region, d)
    if region.cfg.memorise_all_steps:                 # 全ステップのログ(RL の学習用。長期記憶には統合しない)
        np = a.grid_position
        moved = (np.x, np.y) != before
        state = a._memory_state(region) + ((a.map.window(np.x, np.y, a.perception_range, a.effective_range(region))
                                            if a.map is not None else b""),)
        a.memory.record_step(region.tick_no, "move" if moved else "idle", "moved" if moved else "idle", np.x, np.y, state)
