"""グリッド座標・Region 座標のユーティリティ。1 セル = 1 ワールド単位。"""
from typing import NamedTuple, Tuple


class Pos(NamedTuple):
    x: int
    y: int


RegionId = Tuple[int, int]

# 北 = y-1(画面上方向)
DIRS = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}


def step(p: Pos, direction: str) -> Pos:
    dx, dy = DIRS[direction]
    return Pos(p.x + dx, p.y + dy)


def manhattan(a: Pos, b: Pos) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def chebyshev(a: Pos, b: Pos) -> int:
    return max(abs(a.x - b.x), abs(a.y - b.y))


def region_of(p: Pos, region_size: int) -> RegionId:
    return (p.x // region_size, p.y // region_size)
