"""ASCII 描画。自分を中心とした表示範囲 + ステータスバー + ログ + サイドパネル(Help・Config)。"""
from typing import List

from . import panel
from .keymap import footer_variants
from .style import COLOR, GLYPH, SELF_STYLE, TERRAIN, my_glyph, paint
from .textutil import char_width, clip, odd_at_most, text_width
from .view import PRIORITY, WorldView


def draw_cell(view: WorldView, x: int, y: int, color: bool, cell_w: int) -> str:
    """1 セル分(cell_w 桁)。自分は白背景・黒文字(全角文字は 2 桁、半角は 1 桁 + 余白)。"""
    g = cell_glyph(view, x, y)
    pad = " " * max(0, cell_w - char_width(g))
    if view.me_id and (x, y) == view.me_pos and g == my_glyph(view):
        return f"\x1b[{SELF_STYLE}m{g}{pad}\x1b[0m" if color else g + pad
    if g in COLOR or len(g) != 1 or char_width(g) == 1 and g not in "?":
        return paint(g, color) + pad
    return g + pad


def cell_glyph(view: WorldView, x: int, y: int) -> str:
    ents = view.at((x, y))
    if ents:
        if view.me_id and any(e["id"] == view.me_id for e in ents):
            return my_glyph(view)
        for sp in PRIORITY:
            for e in ents:
                if e["species"] == sp:
                    if sp == "Player":
                        return (e.get("name") or "P")[:1]
                    g = GLYPH[sp]
                    return g.upper() if e.get("state", "awake") != "awake" and g.isalpha() else g   # 眠っている動物は大文字
    return TERRAIN.get(view.kind(x, y), "?")


def bar(value: float, width: int = 10, maximum: float = 100) -> str:
    n = max(0, min(width, round(width * value / maximum)))
    return "[" + "#" * n + "-" * (width - n) + "]"


FOOTERS = footer_variants()      # 最下行の候補(keymap.py の表から作る)


RESERVED_ROWS = 11          # 地図以外の行: 見出し 1 + 状態 2 + 人数 1 + Here 1 + ログ 4 + 死亡 1 + 最下行のキー 1


def render(view: WorldView, cols: int = 80, rows: int = 30, name: str = "",
           color: bool = False, cell_w: int = 2) -> str:
    """cols x rows の端末に収まる 1 フレーム。cell_w=2 は縦横比のため 1 セルを 2 文字幅で描く。"""
    is_open = panel.is_open(view)
    side_by_side = is_open and cols >= panel.SIDE_MIN_COLS       # 広い端末: 地図の横に出す
    overlay = is_open and not side_by_side                       # 狭い端末: 地図の上に重ねて、全面に出す(Esc で戻る)
    map_w = odd_at_most((cols - (panel.PANEL_W if side_by_side else 0)) // cell_w)       # 幅を超えない(折り返すと、行がずれる)
    map_h = odd_at_most(rows - RESERVED_ROWS)                                  # 高さを超えない(超えると、画面がスクロールする)
    cx, cy = view.me_pos or (view.region_size * 2, view.region_size * 2)
    x0, y0 = cx - map_w // 2, cy - map_h // 2
    lines: List[str] = []
    st = view.stats
    bw = 10 if cols >= 85 else 5 if cols >= 75 else 3        # 状態バーの幅(狭い端末では縮めて、行が切れないようにする)
    temp = view.temperature()
    lines.append(f" EcoloGrid  {name}  pos={cx},{cy}  {view.time_label()}"
                 + (f"  {temp:.0f}C" if temp is not None else ""))
    side = (panel.compose(view, panel.PANEL_W - 2, map_h, color) if side_by_side
            else panel.compose(view, max(10, cols - 1), map_h, color) if overlay else [])
    for i, y in enumerate(range(y0, y0 + map_h)):   # 知覚の範囲の外は、空白(r = 0 のときは、自分のセルだけ)
        if overlay:
            lines.append(side[i])
            continue
        row = "".join(draw_cell(view, x, y, color, cell_w) if view.in_view(x, y) else " " * cell_w
                      for x in range(x0, x0 + map_w))
        if side_by_side:
            row += "  " + (side[i] if i < len(side) else "")
        lines.append(row)
    lines.append(f" Hydration {bar(st['hydration'], bw)} {st['hydration']:3d}   "
                 f"Energy {bar(st['energy'], bw)} {st['energy']:3d}   "
                 f"Health {bar(st['health'], bw)} {st['health']:3d}")
    vt = view.vitals
    state = {"awake": "Resting" if vt["resting"] else "Awake", "sleeping": "Sleeping",
             "unconscious": "Unconscious"}.get(vt["state"], vt["state"])
    lines.append(f" Stamina   {bar(vt['stamina'], bw)} {vt['stamina']:3d}   "
                 f"Wakeful {bar(vt['wakefulness'], bw)} {vt['wakefulness']:3d}   State {state}"
                 + (f"   Range {view.view_r}" if view.view_r is not None else ""))
    c = view.census()
    lines.append((" World: " if view.view_r is None else " In view: ") + "  ".join(f"{k} {c.get(k, 0)}" for k in ("Player", "Fox", "Rabbit", "Wheat", "Grass")))
    here = [e["species"] for e in view.at((cx, cy)) if e["id"] != view.me_id]
    lines.append(" Here: " + (", ".join(here) if here else "-"))
    for m in list(view.log)[-4:]:
        lines.append(" > " + m)
    if not view.alive:
        lines.append(" *** YOU DIED ***  press x to respawn")
    lines.append(next((f for f in FOOTERS if text_width(f) <= cols), FOOTERS[-1]))     # 幅に収まる、最も詳しいもの
    return "\n".join(clip(l, cols) for l in lines)
