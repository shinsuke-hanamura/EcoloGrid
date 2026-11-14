"""手続き生成地形。seed から決まる高さ場(Perlin ノイズ)を陸/水/岩に分類する。

高さ場は seed だけで決まる純関数。水位より低いセルは水(静的に窪みへ溜まる)、
高いセルは岩(通行不可)。Phase 3 以降でメッシュに置き換える。
"""
import math
import random
from typing import Dict, List, Tuple

from .grid import Pos

LAND, WATER, ROCK, OUT = ".", "~", "#", " "

WATER_LEVEL = 0.40   # これ未満は水
ROCK_LEVEL = 0.64    # これ超は岩
_SCALE = 0.07
_OCTAVES = 3

_GRADS = [(1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)]


def _fade(t: float) -> float:
    return t * t * t * (t * (t * 6 - 15) + 10)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class Perlin:
    def __init__(self, seed: int):
        p = list(range(256))
        random.Random(seed).shuffle(p)
        self._p = p + p

    def noise(self, x: float, y: float) -> float:
        xi, yi = math.floor(x), math.floor(y)
        xf, yf = x - xi, y - yi
        xi &= 255
        yi &= 255
        p = self._p

        def g(h: int, dx: float, dy: float) -> float:
            gx, gy = _GRADS[h & 7]
            return gx * dx + gy * dy

        aa, ab = p[p[xi] + yi], p[p[xi] + yi + 1]
        ba, bb = p[p[xi + 1] + yi], p[p[xi + 1] + yi + 1]
        u, v = _fade(xf), _fade(yf)
        x1 = _lerp(g(aa, xf, yf), g(ba, xf - 1, yf), u)
        x2 = _lerp(g(ab, xf, yf - 1), g(bb, xf - 1, yf - 1), u)
        return _lerp(x1, x2, v)


class Terrain:
    def __init__(self, seed: int, width: int, height: int):
        self.seed, self.width, self.height = seed, width, height
        self._perlin = Perlin(seed)
        self._cache: Dict[Tuple[int, int], str] = {}

    def height_at(self, x: int, y: int) -> float:
        total, amp, freq, norm = 0.0, 1.0, _SCALE, 0.0
        for _ in range(_OCTAVES):
            total += amp * self._perlin.noise(x * freq + 0.5, y * freq + 0.5)
            norm += amp
            amp *= 0.5
            freq *= 2
        return max(0.0, min(1.0, 0.5 + 0.5 * total / norm * 1.6))

    def kind(self, x: int, y: int) -> str:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return OUT
        k = (x, y)
        c = self._cache.get(k)
        if c is None:
            h = self.height_at(x, y)
            c = WATER if h < WATER_LEVEL else ROCK if h > ROCK_LEVEL else LAND
            self._cache[k] = c
        return c

    def walkable(self, p: Pos) -> bool:
        return self.kind(p.x, p.y) == LAND

    def cells_of_kind(self, x0: int, y0: int, size: int, kind: str) -> List[Pos]:
        return [Pos(x, y) for y in range(y0, y0 + size)
                for x in range(x0, x0 + size) if self.kind(x, y) == kind]
