"""ASCII 描画。自分を中心とした表示範囲 + ステータスバー + ログ。"""
import unicodedata
from typing import List, Optional

from .view import PRIORITY, WorldView

GLYPH = {"Fox": "f", "Rabbit": "r", "Egg": "o", "Wheat": "w", "Grass": '"', "Seed": ","}
TERRAIN = {".": ".", "~": "~", "#": "#", " ": " "}
COLOR = {"@": "1;97", "P": "1;95", "F": "91", "R": "93", "f": "91", "r": "93", "o": "37", "w": "33", '"': "32",
         ",": "90", "~": "34", "#": "90", ".": "90"}


SELF_STYLE = "30;47"          # 自分: 白背景・黒文字


def char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _paint(ch: str, color: bool) -> str:
    c = COLOR.get(ch)
    return f"\x1b[{c}m{ch}\x1b[0m" if color and c else ch


def my_glyph(view: WorldView) -> str:
    return view.my_name[:1] or "@"        # プレイヤーを示す文字は、名前の 1 文字目


def draw_cell(view: WorldView, x: int, y: int, color: bool, cell_w: int) -> str:
    """1 セル分(cell_w 桁)。自分は白背景・黒文字(全角文字は 2 桁、半角は 1 桁 + 余白)。"""
    g = cell_glyph(view, x, y)
    pad = " " * max(0, cell_w - char_width(g))
    if view.me_id and (x, y) == view.me_pos and g == my_glyph(view):
        return f"\x1b[{SELF_STYLE}m{g}{pad}\x1b[0m" if color else g + pad
    if g in COLOR or len(g) != 1 or char_width(g) == 1 and g not in "?":
        return _paint(g, color) + pad
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


LEGEND = [("P", "other player (1st char of name)"), ("f", "Fox (F: asleep)"), ("r", "Rabbit (R: asleep)"),
          ("o", "Egg"), ("w", "Wheat"), ('"', "Grass"), (",", "Seed"), ("~", "water"), ("#", "wall"),
          (".", "land"), (" ", "out of view")]
LEGEND_KEYS = ["wasd/arrows move", "e eat    r drink", "z rest    . wait", "x respawn", "c config (rename)", "t time/tick", "h this help", "q quit"]
LEGEND_W = 36


def legend_lines(view: WorldView, color: bool) -> List[str]:
    out = ["Legend (h: close)"]
    me = my_glyph(view)
    out.append(f" \x1b[{SELF_STYLE}m{me}\x1b[0m you (1st char of name)" if color else f" {me} you (1st char of name)")
    out += [f" {_paint(g, color) if g != ' ' else '_'} {d}" for g, d in LEGEND]
    out += ["", "Keys"] + [" " + k for k in LEGEND_KEYS]
    return out


def bar(value: float, width: int = 10, maximum: float = 100) -> str:
    n = max(0, min(width, round(width * value / maximum)))
    return "[" + "#" * n + "-" * (width - n) + "]"


def render(view: WorldView, cols: int = 80, rows: int = 30, name: str = "",
           color: bool = False, cell_w: int = 2) -> str:
    """cols x rows の端末に収まる 1 フレーム。cell_w=2 は縦横比のため 1 セルを 2 文字幅で描く。"""
    legend = view.show_help and cols >= 40 + LEGEND_W
    map_w = max(5, ((cols - (LEGEND_W if legend else 0)) // cell_w) | 1)
    map_h = max(5, (rows - 10) | 1)
    cx, cy = view.me_pos or (view.region_size * 2, view.region_size * 2)
    x0, y0 = cx - map_w // 2, cy - map_h // 2
    lines: List[str] = []
    st = view.stats
    temp = view.temperature()
    lines.append(f" EcoloGrid  {name}  pos={cx},{cy}  {view.time_label()}"
                 + (f"  {temp:.0f}C" if temp is not None else ""))
    side = legend_lines(view, color) if legend else []
    for i, y in enumerate(range(y0, y0 + map_h)):   # 知覚の範囲の外は、空白(r = 0 のときは、自分のセルだけ)
        row = "".join(draw_cell(view, x, y, color, cell_w) if view.in_view(x, y) else " " * cell_w
                      for x in range(x0, x0 + map_w))
        if legend:
            row += "  " + (side[i] if i < len(side) else "")
        lines.append(row)
    lines.append(f" Hydration {bar(st['hydration'])} {st['hydration']:3d}   "
                 f"Energy {bar(st['energy'])} {st['energy']:3d}   "
                 f"Health {bar(st['health'])} {st['health']:3d}")
    vt = view.vitals
    state = {"awake": "Resting" if vt["resting"] else "Awake", "sleeping": "Sleeping",
             "unconscious": "Unconscious"}.get(vt["state"], vt["state"])
    lines.append(f" Stamina   {bar(vt['stamina'])} {vt['stamina']:3d}   "
                 f"Wakeful {bar(vt['wakefulness'])} {vt['wakefulness']:3d}   State {state}"
                 + (f"   Range {view.view_r}" if view.view_r is not None else ""))
    c = view.census()
    lines.append((" World: " if view.view_r is None else " In view: ") + "  ".join(f"{k} {c.get(k, 0)}" for k in ("Player", "Fox", "Rabbit", "Wheat", "Grass")))
    here = [e["species"] for e in view.at((cx, cy)) if e["id"] != view.me_id]
    lines.append(" Here: " + (", ".join(here) if here else "-"))
    for m in list(view.log)[-4:]:
        lines.append(" > " + m)
    if not view.alive:
        lines.append(" *** YOU DIED ***  press x to respawn")
    lines.append(" wasd/arrows:move  e:eat  r:drink  z:rest  .:wait  x:respawn  c:config  t:time/tick  h:help  q:quit")
    return "\n".join(l[:cols * 2] if not color else l for l in lines)
