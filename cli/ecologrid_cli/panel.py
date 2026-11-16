"""サイドパネル(Side Panel): マップの横の、情報と操作の領域。ページを切り替えて使う(仕組みだけ。中身は pages.py)。

ページ: Help(h)= 凡例・現在の進行の速さ・キーの一覧 / Config(c)= 名前・進行の速さの変更 / (将来)Actions。
構成: 見出し行(ページの名前。現在のページを [ ] で示す)+ 本文(はみ出したら ↑↓ でスクロール)+ 最下行(ヒント)。
端末が広いときは、地図の横に出す(幅 PANEL_W)。狭いときは、地図の上に重ねて、全面に出す。どちらも、同じ操作。
状態は WorldView.panel(PanelState)に持つ。ページは、次の形で、PAGES に登録する(pages.py):
    title                           見出しに出す名前
    body(view, width, color)        本文の行(複数)
    hint(view)                      最下行のヒント
    captures_input(view)            True の間は、キーをページが受け取る(移動・終了のキーも、入力として扱う)
    handle_key(view, session, key)  キー("ESC"・"ENTER"・"BACKSPACE"・"UP"… か、1 文字)
"""
from typing import Dict, List

from .textutil import clip

PANEL_W = 36                      # 横に並べるときの幅(桁)。端末の幅が、地図の最小の幅(40)+ これ以上なら、横に並べる
SIDE_MIN_COLS = 40 + PANEL_W

PAGES: Dict[str, object] = {}     # ページ名 -> ページ(pages.py が登録する)
ORDER = ("help", "config")        # 見出しの並び順


def register(name: str, page) -> None:
    PAGES[name] = page


def is_open(view) -> bool:
    return view.panel.page is not None


def captures_input(view) -> bool:
    """開いているページが、キーを受け取るか(Config の間は True)。"""
    p = view.panel.page
    return p is not None and bool(PAGES[p].captures_input(view))


def open_page(view, name: str) -> None:
    st = view.panel
    st.page, st.sub, st.buf, st.msg, st.scroll, st.max_scroll = name, "", "", "", 0, 0
    page = PAGES[name]
    if hasattr(page, "on_open"):
        page.on_open(view)


def close(view) -> None:
    open_page_state = view.panel
    open_page_state.page, open_page_state.sub, open_page_state.buf, open_page_state.msg = None, "", "", ""
    open_page_state.scroll = open_page_state.max_scroll = 0


def toggle(view, name: str) -> None:
    """そのページが開いていれば閉じ、そうでなければ(別のページが開いていても)そのページを開く。"""
    if view.panel.page == name:
        close(view)
    else:
        open_page(view, name)


def scroll(view, delta: int) -> None:
    st = view.panel
    st.scroll = max(0, min(st.max_scroll, st.scroll + delta))


def handle_key(view, session, key: str) -> bool:
    """パネルが開いているときのキー。パネルが使った(ゲームに渡さない)なら True。"""
    st = view.panel
    if st.page is None:
        return False
    if key in ("UP", "DOWN") and st.max_scroll > 0:       # はみ出している間だけ、↑↓ はスクロール(収まっていれば、移動に使う)
        scroll(view, -1 if key == "UP" else 1)
        return True
    page = PAGES[st.page]
    if page.captures_input(view):
        page.handle_key(view, session, key)
        return True
    if key == "ESC":                                      # 入力を受け取らないページ(Help): Esc で閉じる
        close(view)
        return True
    return False


def compose(view, width: int, height: int, color: bool = False) -> List[str]:
    """パネルの行(ちょうど height 行。各行は width 桁以内)。本文のスクロールの位置も、ここで決める。"""
    st = view.panel
    page = PAGES[st.page]
    tabs = " " + "  ".join(f"[{PAGES[n].title}]" if n == st.page else f" {PAGES[n].title} " for n in ORDER if n in PAGES)
    body = page.body(view, width, color)
    body_h = max(1, height - 2)
    st.max_scroll = max(0, len(body) - body_h)
    st.scroll = max(0, min(st.max_scroll, st.scroll))
    shown = body[st.scroll: st.scroll + body_h]
    shown += [""] * (body_h - len(shown))
    marks = ("^" if st.scroll > 0 else " ") + ("v" if st.scroll < st.max_scroll else " ")
    hint = ("Up/Down:scroll  " if st.max_scroll > 0 else "") + page.hint(view)
    lines = [tabs] + shown + [f" {marks} {hint}"]
    return [clip(l, width) for l in lines[:height]]


from . import pages  # noqa: E402,F401  (ページを登録する)
