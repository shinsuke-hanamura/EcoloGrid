"""ASCII 描画。自分を中心とした表示範囲 + ステータスバー + ログ。"""
import re
import unicodedata
from typing import List, Optional

from .view import PRIORITY, WorldView, tick_label

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
LEGEND_KEYS = ["wasd/arrows move", "e eat    r drink", "z rest    . wait", "x respawn", "c config (name, tick)", "t time/tick", "h this help", "q quit"]
from .panel import PANEL_W, config_lines
LEGEND_W = PANEL_W


def legend_lines(view: WorldView, color: bool) -> List[str]:
    out = ["Help: Legend (h: close)", f" Tick: {tick_label(view.tick_interval_ms)}   (change: c)"]
    me = my_glyph(view)
    out.append(f" \x1b[{SELF_STYLE}m{me}\x1b[0m you (1st char of name)" if color else f" {me} you (1st char of name)")
    out += [f" {_paint(g, color) if g != ' ' else '_'} {d}" for g, d in LEGEND]
    out += ["", "Keys"] + [" " + k for k in LEGEND_KEYS]
    return out


def bar(value: float, width: int = 10, maximum: float = 100) -> str:
    n = max(0, min(width, round(width * value / maximum)))
    return "[" + "#" * n + "-" * (width - n) + "]"


ANSI = re.compile(r"\x1b\[[0-9;]*m")
FOOTERS = [" wasd/arrows:move  e:eat  r:drink  z:rest  .:wait  x:respawn  c:config  t:time/tick  h:help  q:quit",
           " wasd:move e:eat r:drink z:rest .:wait x:respawn c:config t:time h:help q:quit",
           " wasd e r z . x | c:config h:help q:quit",
           " h:help q:quit"]


def char_width_of(s: str) -> int:
    return sum(char_width(c) for c in s)


RESERVED_ROWS = 11          # 地図以外の行: 見出し 1 + 状態 2 + 人数 1 + Here 1 + ログ 4 + 死亡 1 + 最下行のキー 1


def clip(line: str, width: int) -> str:
    """表示幅 width 桁に収める(色の指定は桁に数えない。途中で切ったら、色を戻す)。端末の幅を超えると折り返して、行がずれる。"""
    out, w, cut = [], 0, False
    for tok in re.split(r"(\x1b\[[0-9;]*m)", line):
        if ANSI.fullmatch(tok):
            out.append(tok)
            continue
        for ch in tok:
            cw = char_width(ch)
            if w + cw > width:
                cut = True
                break
            out.append(ch)
            w += cw
        if cut:
            break
    return "".join(out) + ("\x1b[0m" if cut and any(ANSI.fullmatch(t) for t in out) else "")


def odd_at_most(n: int, lo: int = 5) -> int:
    """n 以下の最大の奇数(中心のセルが決まるように、地図の幅・高さは奇数にする)。n を超えない。"""
    n = max(lo, n)
    return n if n % 2 else n - 1


def render(view: WorldView, cols: int = 80, rows: int = 30, name: str = "",
           color: bool = False, cell_w: int = 2) -> str:
    """cols x rows の端末に収まる 1 フレーム。cell_w=2 は縦横比のため 1 セルを 2 文字幅で描く。"""
    legend = (view.show_help or view.panel_mode is not None) and cols >= 40 + PANEL_W    # サイドパネル(Help / Config)
    map_w = odd_at_most((cols - (PANEL_W if legend else 0)) // cell_w)       # 幅を超えない(折り返すと、行がずれる)
    map_h = odd_at_most(rows - RESERVED_ROWS)                                  # 高さを超えない(超えると、画面がスクロールする)
    cx, cy = view.me_pos or (view.region_size * 2, view.region_size * 2)
    x0, y0 = cx - map_w // 2, cy - map_h // 2
    lines: List[str] = []
    st = view.stats
    bw = 10 if cols >= 85 else 5 if cols >= 75 else 3        # 状態バーの幅(狭い端末では縮めて、行が切れないようにする)
    temp = view.temperature()
    lines.append(f" EcoloGrid  {name}  pos={cx},{cy}  {view.time_label()}"
                 + (f"  {temp:.0f}C" if temp is not None else ""))
    side = (config_lines(view, color) if view.panel_mode is not None else legend_lines(view, color)) if legend else []
    for i, y in enumerate(range(y0, y0 + map_h)):   # 知覚の範囲の外は、空白(r = 0 のときは、自分のセルだけ)
        row = "".join(draw_cell(view, x, y, color, cell_w) if view.in_view(x, y) else " " * cell_w
                      for x in range(x0, x0 + map_w))
        if legend:
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
    lines.append(next((f for f in FOOTERS if char_width_of(f) <= cols), FOOTERS[-1]))     # 幅に収まる、最も詳しいもの
    return "\n".join(clip(l, cols) for l in lines)
