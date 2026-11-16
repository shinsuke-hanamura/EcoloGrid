"""サイドパネルのページの中身: Help・Config。仕組みは panel.py。"""
import textwrap
from typing import List

from . import panel
from .api import ApiError
from .keymap import help_key_lines
from .style import SELF_STYLE, my_glyph, paint
from .textutil import fit, tail
from .view import WorldView, tick_label

LEGEND = [("P", "other player (1st char of name)"), ("f", "Fox (F: asleep)"), ("r", "Rabbit (R: asleep)"),
          ("o", "Egg"), ("w", "Wheat"), ('"', "Grass"), (",", "Seed"), ("~", "water"), ("#", "wall"),
          (".", "land"), (" ", "out of view")]
NAME_MAX = 24
TICK_MAX_LEN = 4                  # 0〜1000 と max(4 文字)


class HelpPage:
    """凡例・現在の進行の速さ・キーの一覧。入力は受け取らない(ゲームのキーは、そのまま効く)。"""
    title = "Help"

    def captures_input(self, view: WorldView) -> bool:
        return False

    def handle_key(self, view: WorldView, session, key: str) -> None:
        pass

    def hint(self, view: WorldView) -> str:
        return "h/Esc:close  c:Config"

    def body(self, view: WorldView, width: int, color: bool) -> List[str]:
        me = my_glyph(view)
        out = [f" Tick: {tick_label(view.tick_interval_ms)}   (change: c)", "", " Legend",
               f" \x1b[{SELF_STYLE}m{me}\x1b[0m you (1st char of name)" if color else f" {me} you (1st char of name)"]
        out += [f" {paint(g, color) if g != ' ' else '_'} {d}" for g, d in LEGEND]
        out += ["", " Keys"] + help_key_lines()
        return out


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


class ConfigPage:
    """設定: 1) 名前の変更  2) 進行の速さ(tick の間隔)の変更。パネルの中で、キーを 1 つずつ受け取って入力する。

    st.sub: "menu"(項目の選択)/ "name" / "tick"(入力中)。入力中は、h・c・q も文字。
    """
    title = "Config"

    def on_open(self, view: WorldView) -> None:
        view.panel.sub = "menu"

    def captures_input(self, view: WorldView) -> bool:
        return True

    def hint(self, view: WorldView) -> str:
        return "1/2:select  h:Help  Esc:close" if view.panel.sub == "menu" else "Enter:set  Esc:back"

    def body(self, view: WorldView, width: int, color: bool) -> List[str]:
        st = view.panel
        if st.sub == "name":
            out = [f" Now: {fit(view.my_name, width - 6)}", " New name (max 24 characters):",
                   " > " + tail(st.buf, width - 5) + "_"]
        elif st.sub == "tick":
            out = [f" Now: {tick_label(view.tick_interval_ms)}", " 0 to 1000 ms. 0 or max = fastest",
                   " This changes the speed for ALL players.", " > " + st.buf + "_"]
        else:
            out = [f" 1) Name : {fit(view.my_name, width - 12)}", f" 2) Tick : {tick_label(view.tick_interval_ms)}",
                   "", " Press 1 or 2"]
        if st.msg:
            out.append("")
            out += [" " + l for l in textwrap.wrap(st.msg, max(10, width - 2))]
        return out

    def handle_key(self, view: WorldView, session, key: str) -> None:
        st = view.panel
        if st.sub == "menu":
            if key == "1":
                st.sub, st.buf, st.msg, st.scroll = "name", "", "", 0
            elif key == "2":
                st.sub, st.buf, st.msg, st.scroll = "tick", "", "", 0
            elif key == "h":                           # 項目の選択から、Help へ
                panel.open_page(view, "help")
            elif key in ("ESC", "ENTER", "c"):
                panel.close(view)
            return
        if key == "ESC":                               # 項目の選択に戻る
            st.sub, st.buf, st.msg, st.scroll = "menu", "", "", 0
        elif key == "BACKSPACE":
            st.buf = st.buf[:-1]
        elif key == "ENTER":
            self._submit(view, session)
        elif len(key) == 1 and key.isprintable():
            limit = NAME_MAX if st.sub == "name" else TICK_MAX_LEN
            if len(st.buf) < limit and (st.sub == "name" or key.isalnum()):
                st.buf += key

    def _submit(self, view: WorldView, session) -> None:
        st = view.panel
        kind, text = st.sub, st.buf.strip()
        if not text:                                   # 空のまま Enter: 項目の選択に戻る
            st.sub, st.buf, st.msg, st.scroll = "menu", "", "", 0
            return
        try:
            if kind == "name":
                session.change_name(text)
                msg = f"Name set: {session.name}"
            else:
                raw = "0" if text.lower() == "max" else text
                if not raw.isdigit() or not 0 <= int(raw) <= 1000:
                    st.msg = "Enter an integer from 0 to 1000, or 'max'."
                    return
                session.change_tick_interval(int(raw))
                msg = f"Tick interval set: {tick_label(view.tick_interval_ms)}"
        except Exception as e:                         # 入力は残して、再入力させる
            st.msg = _error_text(e, kind)
            return
        st.sub, st.buf, st.msg, st.scroll = "menu", "", msg, 0


panel.register("help", HelpPage())
panel.register("config", ConfigPage())
