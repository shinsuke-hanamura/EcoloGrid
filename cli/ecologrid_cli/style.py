"""描画の記号と色。地図・サイドパネルの Help の凡例が、同じ定義を使う。"""
from .view import WorldView

GLYPH = {"Fox": "f", "Rabbit": "r", "Egg": "o", "Wheat": "w", "Grass": '"', "Seed": ","}
TERRAIN = {".": ".", "~": "~", "#": "#", " ": " "}
COLOR = {"@": "1;97", "P": "1;95", "F": "91", "R": "93", "f": "91", "r": "93", "o": "37", "w": "33", '"': "32",
         ",": "90", "~": "34", "#": "90", ".": "90"}
SELF_STYLE = "30;47"          # 自分: 白背景・黒文字


def paint(ch: str, color: bool) -> str:
    c = COLOR.get(ch)
    return f"\x1b[{c}m{ch}\x1b[0m" if color and c else ch


def my_glyph(view: WorldView) -> str:
    return view.my_name[:1] or "@"        # プレイヤーを示す文字は、名前の 1 文字目
