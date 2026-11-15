"""サイドパネル(Side Panel): マップの横に出す、情報と操作の領域。

中身は 2 つ(切り替え式):
    Help   (h)  凡例・現在の進行の速さ・キーの一覧
    Config (c)  設定: 1) 名前の変更  2) 進行の速さ(tick の間隔)の変更

Config は、パネルの中で、キーを 1 つずつ受け取って入力する(通常の入力モードに切り替えない)。状態は WorldView に持つ:
    view.panel_mode : None(Config を開いていない)/ "config"(項目の選択)/ "name" / "tick"(入力中)
    view.panel_buf  : 入力中の文字列      view.panel_msg : パネルに出すメッセージ(成功・エラー)
キーは、"ESC"・"ENTER"・"BACKSPACE"・"UP"…などの名前か、1 文字(UTF-8 の 1 文字)。
"""
import textwrap
from typing import List

from .api import ApiError
from .view import WorldView, tick_label

PANEL_W = 36                      # パネルの幅(桁)。これ以上の端末の幅(+ 地図の最小幅)がないと、パネルは出さない
NAME_MAX = 24
TICK_MAX_LEN = 4                  # 0〜1000 と max(4 文字)


def char_width(ch: str) -> int:
    import unicodedata
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def fit(s: str, width: int = PANEL_W - 2) -> str:
    """表示幅 width 桁に収まるように、後ろを切る。"""
    out, w = [], 0
    for ch in s:
        cw = char_width(ch)
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def tail(s: str, width: int) -> str:
    """表示幅 width 桁に収まるように、前を切る(入力中の文字列の、末尾を見せる)。"""
    out, w = [], 0
    for ch in reversed(s):
        cw = char_width(ch)
        if w + cw > width:
            break
        out.append(ch)
        w += cw
    return "".join(reversed(out))


def captures_input(view: WorldView) -> bool:
    """Config を開いている間は、キーをパネルが受け取る(移動・終了のキーも、入力として扱う)。"""
    return view.panel_mode is not None


def open_config(view: WorldView) -> None:
    view.panel_mode, view.panel_buf, view.panel_msg = "config", "", ""


def close(view: WorldView) -> None:
    view.panel_mode, view.panel_buf, view.panel_msg = None, "", ""


def _error_text(e: Exception, kind: str) -> str:
    if isinstance(e, ApiError):
        code = e.code
        if kind == "name":
            if code == "name_taken":
                return "That name is already used by another player."
            if code == "name_invalid" or e.status == 422:
                return "That name is not valid (1 to 24 characters)."
        else:
            if e.status == 429:
                return "Changed too recently (once per second). Try again."
            if e.status == 403:
                return "You are not allowed to change it."
            if e.status == 422:
                return "Enter an integer from 0 to 1000, or 'max'."
        return str(e)
    return f"Error: {type(e).__name__}: {e}"


def handle_key(view: WorldView, session, key: str) -> None:
    """Config 中のキー。session は、名前・速さを変える先(change_name・change_tick_interval)。"""
    mode = view.panel_mode
    if mode == "config":
        if key == "1":
            view.panel_mode, view.panel_buf, view.panel_msg = "name", "", ""
        elif key == "2":
            view.panel_mode, view.panel_buf, view.panel_msg = "tick", "", ""
        elif key in ("ESC", "ENTER", "c"):
            close(view)
        return
    if mode not in ("name", "tick"):
        return
    if key == "ESC":                                   # 項目の選択に戻る
        view.panel_mode, view.panel_buf, view.panel_msg = "config", "", ""
    elif key == "BACKSPACE":
        view.panel_buf = view.panel_buf[:-1]
    elif key == "ENTER":
        _submit(view, session)
    elif len(key) == 1 and key.isprintable():
        limit = NAME_MAX if mode == "name" else TICK_MAX_LEN
        if len(view.panel_buf) < limit and (mode == "name" or key.isalnum()):
            view.panel_buf += key


def _submit(view: WorldView, session) -> None:
    mode, text = view.panel_mode, view.panel_buf.strip()
    if not text:                                       # 空のまま Enter: 項目の選択に戻る
        view.panel_mode, view.panel_buf, view.panel_msg = "config", "", ""
        return
    try:
        if mode == "name":
            session.change_name(text)
            msg = f"Name set: {session.name}"
        else:
            raw = "0" if text.lower() == "max" else text
            if not raw.isdigit() or not 0 <= int(raw) <= 1000:
                view.panel_msg = "Enter an integer from 0 to 1000, or 'max'."
                return
            session.change_tick_interval(int(raw))
            msg = f"Tick interval set: {tick_label(view.tick_interval_ms)}"
    except Exception as e:                             # 入力は残して、再入力させる
        view.panel_msg = _error_text(e, "name" if mode == "name" else "tick")
        return
    view.panel_mode, view.panel_buf, view.panel_msg = "config", "", msg


def config_lines(view: WorldView, color: bool = False) -> List[str]:
    """Config のパネルの中身(PANEL_W 桁以内)。"""
    w = PANEL_W - 2
    mode = view.panel_mode
    if mode == "config":
        out = ["Config (Esc/c: close)", "", f" 1) Name : {fit(view.my_name, w - 12)}",
               f" 2) Tick : {tick_label(view.tick_interval_ms)}", "", " Press 1 or 2"]
    elif mode == "name":
        out = ["Config > Name", f" Now: {fit(view.my_name, w - 6)}", " New name (max 24 characters):",
               " > " + tail(view.panel_buf, w - 5) + "_", "", " Enter: set   Esc: back"]
    else:
        out = ["Config > Tick interval", f" Now: {tick_label(view.tick_interval_ms)}", " 0 to 1000 ms. 0 or max = fastest",
               " This changes the speed for ALL players.",
               " > " + view.panel_buf + "_", "", " Enter: set   Esc: back"]
    if view.panel_msg:
        out.append("")
        out += [" " + l for l in textwrap.wrap(view.panel_msg, w - 2)]
    return out
