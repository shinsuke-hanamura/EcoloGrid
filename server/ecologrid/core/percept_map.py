"""知覚した地図(PerceptMap)= 状態(RL の信念状態)の一部。docs/spec/animal-memory-spec.md §3.1。

個体ごとに持つ、それまでに知覚したものの地図。見る(知覚する)ことで更新される。
- 地形の層: セル解像度。世界の大きさの 1 バイト配列(0 = 未知、1 = 陸、2 = 水、3 = 岩)。静的なので薄めない。
- 資源の層: 区画(BUCKET × BUCKET セル)解像度。食料(植物)の種別に、件数・見た tick・代表のセルを持つ。
  入眠のたびに、件数へ忘却係数をかけ、閾値を下回ったものを削除する。
動く個体(獲物など)は地図に残さない(現在の知覚だけ)。
"""
import base64
import zlib
from typing import Dict, List, Optional, Tuple

from .terrain import LAND, ROCK, WATER

UNKNOWN, T_LAND, T_WATER, T_ROCK = 0, 1, 2, 3
CODE = {LAND: T_LAND, WATER: T_WATER, ROCK: T_ROCK}
CHAR = {T_LAND: LAND, T_WATER: WATER, T_ROCK: ROCK}
BUCKET = 4                      # 資源の層の区画(セル)。丸めの距離 D = 4 と同じ

Bucket = Tuple[int, int]


class PerceptMap:
    __slots__ = ("width", "height", "terrain", "resources", "version", "known")

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.terrain = bytearray(width * height)
        # 区画 -> 種 -> [件数, 見た tick, 代表のセル x, 代表のセル y]
        self.resources: Dict[Bucket, Dict[str, List[float]]] = {}
        self.version = 0                  # 更新のたびに増える(経験の state が参照する版番号)
        self.known = 0                    # 既知のセル数

    # --- 地形 ---
    def terrain_at(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.terrain[y * self.width + x]
        return UNKNOWN

    def is_land(self, x: int, y: int) -> bool:
        return self.terrain_at(x, y) == T_LAND

    def is_passable_optimistic(self, x: int, y: int) -> bool:
        """経路計画用: 世界の中で、既知の陸、または未知のセル(通れると仮定する)。既知の水・岩は通れない。
        未知のセルは、歩いて知覚するたびに実際の地形に置き換わり、塞がれていれば再計画する。"""
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        v = self.terrain[y * self.width + x]
        return v == UNKNOWN or v == T_LAND

    def is_known(self, x: int, y: int) -> bool:
        return self.terrain_at(x, y) != UNKNOWN

    def set_terrain(self, x: int, y: int, code: int) -> bool:
        """未知のセルを既知にする。既知のセルは更新を飛ばす(地形は静的)。更新したら True。"""
        i = y * self.width + x
        if self.terrain[i] == UNKNOWN:
            self.terrain[i] = code
            self.known += 1
            return True
        return False

    def next_to_water(self, x: int, y: int) -> bool:
        return (self.terrain_at(x + 1, y) == T_WATER or self.terrain_at(x - 1, y) == T_WATER
                or self.terrain_at(x, y + 1) == T_WATER or self.terrain_at(x, y - 1) == T_WATER)

    def window(self, x: int, y: int, big_r: int, r: int) -> bytes:
        """自己中心の窓: (2R+1)×(2R+1) セルの地形コード(行ごと、北から南・西から東)。知覚の有効な範囲 r の外は 0(未知)。
        全ステップのログ(memorise_all_steps)の、経験の state に使う。"""
        out = bytearray((2 * big_r + 1) ** 2)
        i = 0
        for dy in range(-big_r, big_r + 1):
            for dx in range(-big_r, big_r + 1):
                if abs(dx) <= r and abs(dy) <= r:
                    out[i] = self.terrain_at(x + dx, y + dy)
                i += 1
        return bytes(out)

    def known_water_cells(self) -> int:
        return sum(1 for v in self.terrain if v == T_WATER)

    # --- 資源 ---
    def set_resource(self, bucket: Bucket, species: str, count: float, tick: int, rx: int, ry: int) -> None:
        if count <= 0:
            d = self.resources.get(bucket)
            if d is not None:
                d.pop(species, None)
                if not d:
                    del self.resources[bucket]
            return
        self.resources.setdefault(bucket, {})[species] = [count, tick, rx, ry]

    def nearest_resource(self, species: Tuple[str, ...], x: int, y: int, max_dist: int = 10**9
                         ) -> Optional[Tuple[int, int, str]]:
        """既知の資源のうち、最寄り(チェビシェフ距離)の代表のセル。species の優先順で、種ごとに最寄りを探す。"""
        for sp in species:
            best, bd = None, max_dist + 1
            for d in self.resources.values():
                e = d.get(sp)
                if e is None:
                    continue
                dist = max(abs(int(e[2]) - x), abs(int(e[3]) - y))
                if dist < bd or (dist == bd and best is not None and (int(e[2]), int(e[3])) < best[:2]):
                    best, bd = (int(e[2]), int(e[3]), sp), dist
            if best is not None:
                return best
        return None

    def decay(self, factor: float, threshold: float) -> int:
        """資源の層の件数に忘却係数をかけ、閾値を下回ったものを削除する。削除した数を返す。地形は薄めない。"""
        removed = 0
        for bucket in list(self.resources):
            d = self.resources[bucket]
            for sp in list(d):
                d[sp][0] *= factor
                if d[sp][0] < threshold:
                    del d[sp]
                    removed += 1
            if not d:
                del self.resources[bucket]
        if removed:
            self.version += 1
        return removed

    # --- シリアライズ(Region 越境・永続化。公開用 to_dict には含めない) ---
    def to_state(self) -> dict:
        return {"w": self.width, "h": self.height, "version": self.version,
                "terrain": base64.b64encode(zlib.compress(bytes(self.terrain), 6)).decode("ascii"),
                "resources": [[b[0], b[1], sp, e[0], e[1], e[2], e[3]]
                              for b, d in sorted(self.resources.items()) for sp, e in sorted(d.items())]}

    @classmethod
    def from_state(cls, d: dict) -> "PerceptMap":
        m = cls(d["w"], d["h"])
        m.terrain = bytearray(zlib.decompress(base64.b64decode(d["terrain"])))
        m.known = sum(1 for v in m.terrain if v)
        m.version = d.get("version", 0)
        for bx, by, sp, count, tick, rx, ry in d.get("resources", []):
            m.resources.setdefault((bx, by), {})[sp] = [count, tick, rx, ry]
        return m

    def stats(self) -> dict:
        return {"known_cells": self.known, "resource_buckets": len(self.resources), "version": self.version,
                "bytes": len(self.terrain) + 64 * sum(len(v) for v in self.resources.values())}
