"""記憶・知覚した地図・助言の API の応答を作る(本人用と管理用で共通)。docs/spec/animal-memory-spec.md §11。"""
import re
from typing import Optional

from ecologrid.core.memory import Experience
from ecologrid.core.percept_map import CHAR, UNKNOWN

MAX_LIMIT = 500
MAX_MAP = 128


def parse_near(near: Optional[str]):
    if near is None:
        return None
    if not re.fullmatch(r"-?\d+,-?\d+", near):
        raise ValueError("near must be 'x,y'")
    x, y = near.split(",")
    return int(x), int(y)


def memory_payload(ent, tick: int, kind: str = "all", action: Optional[str] = None, outcome: Optional[str] = None,
                   subject: Optional[str] = None, near=None, radius: int = 8, since_tick: Optional[int] = None,
                   limit: int = 100, offset: int = 0) -> dict:
    """個体の記憶(短期記憶 = 経験ログ、長期記憶)。絞り込み・ページネーション付き。"""
    limit = max(1, min(limit, MAX_LIMIT))

    def ok(e, last_tick) -> bool:
        if action and e.action != action or outcome and e.outcome != outcome or subject and e.subject != subject:
            return False
        if near is not None and max(abs(e.x - near[0]), abs(e.y - near[1])) > radius:
            return False
        return since_tick is None or last_tick >= since_tick

    out = {"tick": tick, "individual": ent.entity_id, "organized": ent.memory.organized,
           "last_organize_tick": ent.memory.last_organize_tick, "params": ent.memory.p.to_dict()}
    if kind in ("log", "all"):
        log = [e for e in ent.memory.log if isinstance(e, Experience) and ok(e, e.tick)]
        out["log_total"] = len(log)
        out["log"] = [e.to_dict() for e in log[offset:offset + limit]]
    if kind in ("long", "all"):
        long = sorted((e for e in ent.memory.long.values() if ok(e, e.last_tick)),
                      key=lambda e: (-e.strength, e.key))
        out["long_total"] = len(long)
        out["long"] = [e.to_dict() for e in long[offset:offset + limit]]
    return out


def map_payload(ent, tick: int, x: Optional[int] = None, y: Optional[int] = None, w: Optional[int] = None,
                h: Optional[int] = None) -> dict:
    """個体が知覚した地図(地形と資源)。未知のセルは空白。範囲を省略すると、既知のセルの外接の矩形。"""
    m = ent.map
    if m is None or m.known == 0:
        return {"tick": tick, "individual": ent.entity_id, "version": 0, "known_cells": 0, "origin": [0, 0],
                "rows": [], "resources": []}
    if x is None or y is None or w is None or h is None:
        xs, ys = [], []
        for i, v in enumerate(m.terrain):
            if v:
                xs.append(i % m.width)
                ys.append(i // m.width)
        x, y, w, h = min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    w, h = max(1, min(w, MAX_MAP)), max(1, min(h, MAX_MAP))
    rows = ["".join(CHAR.get(m.terrain_at(cx, cy), " ") for cx in range(x, x + w)) for cy in range(y, y + h)]
    res = [{"bx": b[0], "by": b[1], "species": sp, "count": round(e[0], 3), "tick": e[1], "x": int(e[2]), "y": int(e[3])}
           for b, d in sorted(m.resources.items()) for sp, e in sorted(d.items())
           if x <= e[2] < x + w and y <= e[3] < y + h]
    return {"tick": tick, "individual": ent.entity_id, "version": m.version, "known_cells": m.known,
            "origin": [x, y], "rows": rows, "resources": res}


def advice_payload(world, pid: str) -> Optional[dict]:
    """PC について、NPC なら次にどう行動するか(判断。実行しない)と、その判断の材料。"""
    from ecologrid.core.ai import decide
    win = world.player_window(pid)
    if win is None:
        return None
    ent, region, c, r = win
    d = decide(ent, region, use_cache=False)                  # 副作用なし(経路の保持・休憩の開始などをしない)
    recalled = ent.memory.recall_place(region.cfg.seed, ent.entity_id, region.tick_no, c.x, c.y, commit=False)
    out = d.to_dict()
    out.update({"tick": world.tick_no, "r": r, "perception_range": ent.perception_range,
                "recalled": [e.to_dict() for e in recalled],
                "known_cells": 0 if ent.map is None else ent.map.known,
                "needs": {"thirsty": ent.is_thirsty(), "hungry": ent.is_hungry(), "resting": ent.resting}})
    return out
