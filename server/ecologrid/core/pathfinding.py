"""経路探索(BFS)。個体の知覚した地図の、既知の陸だけを通る。docs/spec/animal-memory-spec.md §4.4.1。

軸方向の貪欲な移動(`Animal.step_toward`)は、障害物の手前で詰まって迂回できない(渇きで死ぬ原因になった)。
BFS で、既知の陸を通る最短経路を求める。決定論的(方向の順序は固定)。
"""
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

# 方向の順序(決定論)。(dx, dy, 名前)
_DIRS = (("n", 0, -1), ("e", 1, 0), ("s", 0, 1), ("w", -1, 0))
Cell = Tuple[int, int]


def bfs_path(start: Cell, passable: Callable[[int, int], bool], is_goal: Callable[[int, int], bool],
             max_nodes: int = 3000) -> Optional[List[str]]:
    """start から、is_goal を満たす最も近いセルまでの、方向のリスト(n/e/s/w)。start が目標なら []。
    passable は、通れるセル(既知の陸)。到達できない・探索の上限(max_nodes)を超えたら None。"""
    if is_goal(*start):
        return []
    parent: Dict[Cell, Tuple[Cell, str]] = {}
    seen = {start}
    q = deque([start])
    n = 0
    while q:
        cur = q.popleft()
        n += 1
        if n > max_nodes:
            return None
        for name, dx, dy in _DIRS:
            nxt = (cur[0] + dx, cur[1] + dy)
            if nxt in seen or not passable(*nxt):
                continue
            seen.add(nxt)
            parent[nxt] = (cur, name)
            if is_goal(*nxt):
                path = []
                c = nxt
                while c != start:
                    c, d = parent[c]
                    path.append(d)
                path.reverse()
                return path
            q.append(nxt)
    return None
