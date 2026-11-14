"""知覚: 個体の有効な範囲(自分のセル + 距離 r まで)を読み取り、知覚した地図(PerceptMap)を更新する。

docs/spec/animal-memory-spec.md §4.1・§10。知覚の範囲は担当 Region 内(現状のまま)。距離はチェビシェフ距離。
地形は既知のセルを飛ばす。資源(植物)は区画ごとの件数・代表のセルを更新する。動く個体は、地図に残さず、
現在の知覚(`scan_mobile`)だけを返す。
"""
from typing import List, Tuple

from .percept_map import BUCKET, CODE, PerceptMap
from .species import PLANT_KEYS


def ensure_map(a, region) -> PerceptMap:
    if a.map is None:
        a.map = PerceptMap(region.cfg.width, region.cfg.height)
    return a.map


def visible_bounds(region, x: int, y: int, r: int) -> Tuple[int, int, int, int]:
    """範囲(自分のセル + 距離 r)と、この Region の交わり: (x_min, x_max, y_min, y_max)。"""
    return (max(x - r, region.x0), min(x + r, region.x0 + region.size - 1),
            max(y - r, region.y0), min(y + r, region.y0 + region.size - 1))


def perceive(a, region) -> bool:
    """知覚して地図を更新する。変化があれば True(地図の版番号を増やし、記憶のログに更新を残す)。"""
    r = a.effective_range(region)
    m = ensure_map(a, region)
    pos = a.grid_position
    key = (pos.x, pos.y, r, region.plant_version)
    if a._perceive_key == key:                        # 同じ位置・範囲で、植物にも変化がない → 地図は変わらない
        return False
    a._perceive_key = key
    x_min, x_max, y_min, y_max = visible_bounds(region, pos.x, pos.y, r)
    terrain, changed, w, tmap = region.terrain, False, m.width, m.terrain
    new_cells: List[Tuple[int, int, int]] = []
    for y in range(y_min, y_max + 1):
        row = y * w
        seg = tmap[row + x_min: row + x_max + 1]
        if 0 in seg:                                  # 既知の行は、まとめて飛ばす(地形は静的)
            for i, v in enumerate(seg):
                if v == 0:
                    x = x_min + i
                    code = CODE[terrain.kind(x, y)]
                    tmap[row + x] = code
                    m.known += 1
                    changed = True
                    new_cells.append((x, y, code))
    # 資源の層: 範囲内の植物を、区画ごとに数える(代表のセル = 自分に最も近い植物)
    seen = {}            # (bx, by) -> {species: [count, best_dist, rx, ry]}
    for e in region.plants_in_range(pos, r):
        ex, ey = e.grid_position.x, e.grid_position.y
        d = max(abs(ex - pos.x), abs(ey - pos.y))
        ent = seen.setdefault((ex // BUCKET, ey // BUCKET), {}).setdefault(e.species, [0, 10 ** 9, 0, 0])
        ent[0] += 1
        if (d, ex, ey) < (ent[1], ent[2], ent[3]):
            ent[1], ent[2], ent[3] = d, ex, ey
    tick = region.tick_no
    res_changes = []
    for by in range(y_min // BUCKET, y_max // BUCKET + 1):
        for bx in range(x_min // BUCKET, x_max // BUCKET + 1):
            full = (bx * BUCKET >= x_min and bx * BUCKET + BUCKET - 1 <= x_max
                    and by * BUCKET >= y_min and by * BUCKET + BUCKET - 1 <= y_max)
            here = seen.get((bx, by), {})
            old = m.resources.get((bx, by), {})
            for sp in PLANT_KEYS:
                cur = here.get(sp)
                prev = old.get(sp)
                if cur is None:
                    if full and prev is not None:           # 区画全体が見えて、植物がない → 削除
                        m.set_resource((bx, by), sp, 0, tick, 0, 0)
                        res_changes.append((bx, by, sp, 0))
                        changed = True
                    continue
                count = cur[0] if full else max(prev[0] if prev else 0, cur[0])
                if prev is None or prev[0] != count or (prev[2], prev[3]) != (cur[2], cur[3]):
                    changed = True
                    res_changes.append((bx, by, sp, count))
                m.set_resource((bx, by), sp, count, tick, cur[2], cur[3])
    if changed:
        m.version += 1
        hook = getattr(a, "on_map_update", None)
        if hook is not None:
            hook(region, m.version, new_cells, res_changes)
    return changed


def scan_mobile(a, region) -> List[Tuple[str, int, int, str]]:
    """現在の知覚(有効な範囲内)の、動く個体(植物・種子・卵以外)。地図には残さない。(種, x, y, id) の、近い順。"""
    r = a.effective_range(region)
    pos = a.grid_position
    out = []
    for e in region.entities_in_range(pos, r):
        if e is a or getattr(e, "dead", False) or e.species in PLANT_KEYS or e.species in ("Seed", "Egg"):
            continue
        out.append((max(abs(e.grid_position.x - pos.x), abs(e.grid_position.y - pos.y)),
                    e.species, e.grid_position.x, e.grid_position.y, e.entity_id))
    out.sort()
    return [(sp, x, y, i) for _, sp, x, y, i in out]
