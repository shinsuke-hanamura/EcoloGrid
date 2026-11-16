"""対話プレイ(端末のキー入力)。世界は実時間で進み続けるので、1 キー = 1 コマンド。

構成: イベント取得は別スレッド(ロングポーリング)、メインスレッドはキー入力と描画。
キーの定義は keymap.py の表、サイドパネル(Help・Config)は panel.py・pages.py。
"""
import os
import select
import shutil
import sys
import threading
import time
from typing import Optional

from . import keymap, panel
from .api import ApiError
from .render import render
from .session import Session

ARROWS = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}      # ESC [ A/B/C/D
ARROW_TO_MOVE = {"UP": "w", "DOWN": "s", "RIGHT": "d", "LEFT": "a"}
KEYS = keymap.HOTKEYS      # 互換: ホットキー(キー -> (コマンドの種類, 引数))。定義は keymap.py


def key_to_command(key: str):
    return keymap.HOTKEYS.get(key)


def dispatch_key(session: Session, key: str) -> str:
    """サイドパネルが受け取らなかった 1 キーを、キーの表(keymap.py)に従って処理する。
    戻り値: "quit"(終了)/ "queued"・"duplicate"・"busy"・"dead"(コマンドの結果)/ "handled"(表示の切り替え)/ ""(対象外)。"""
    kd = keymap.BY_KEY.get(key)
    view = session.view
    if kd is None:
        return ""
    if kd.kind == "quit":
        return "quit"
    if kd.kind == "toggle":
        view.toggle_time_format()
        return "handled"
    if kd.kind == "panel":
        panel.toggle(view, kd.page)
        return "handled"
    if kd.command:
        return session.send(kd.command[0], **kd.command[1])
    return ""


def _name_error_text(e: ApiError, name: str) -> str:
    if e.code == "name_taken":
        return f"The name '{name}' is already used by another player."
    if e.code == "name_invalid" or e.status == 422:
        return "That name is not valid (1 to 32 characters)."
    return str(e)


def rename_screen(session: Session, message: str = "", input_fn=input, out=print) -> bool:
    """起動時の名前の変更画面(名前が使用中だったとき。画面を描く前なので、通常の入力)。使用中の名前は、再入力させる
    (入力のたびにサーバで重複チェック)。決まったら、参加まで行う。空入力で中止(False)。
    プレイ中の名前の変更は、サイドパネルの Config(pages.py)。"""
    while True:
        out("\n=== Player name ===")
        out(f"Current name: {session.name}   (shown on the map as: {session.name[:1] or '@'})")
        if message:
            out(message)
        new = input_fn("New name (Enter = cancel): ").strip()
        if not new:
            return False
        try:
            session.name = new
            session.start()
            out(f"Name set: {session.name}")
            return True
        except ApiError as e:
            message = _name_error_text(e, new)


def start_interactive(session: Session, input_fn=input, out=print) -> bool:
    """参加。名前が使用中なら、名前の変更画面で再設定させる。中止したら False。"""
    try:
        session.start()
        return True
    except ApiError as e:
        if e.code != "name_taken":
            raise
        return rename_screen(session, _name_error_text(e, session.name), input_fn, out)


def run_script(session: Session, script: str, step_wait: float = 0.3, sleep=time.sleep) -> None:
    """非対話: script の各文字をキーとして順に送る(自動テスト・デモ用)。キーの定義は keymap.py(終了の q と、
    入力を受け取る設定 c は、スクリプトでは何もしない)。"""
    for ch in script:
        kd = keymap.BY_KEY.get(ch)
        if kd is not None and not (kd.kind == "quit" or (kd.kind == "panel" and kd.page == "config")):
            dispatch_key(session, ch)
        sleep(step_wait)
        session.poll(0)


def frame(session: Session, cols: int, rows: int, color: bool) -> str:
    return render(session.view, cols, rows, name=session.name, color=color)


def _read_key(fd: int, timeout: float) -> Optional[str]:
    """1 キー。名前つきのキー("ESC"・"ENTER"・"BACKSPACE"・"UP"・"DOWN"・"LEFT"・"RIGHT")か、UTF-8 の 1 文字。"""
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    b = os.read(fd, 1)
    if b == b"\x1b":
        r, _, _ = select.select([fd], [], [], 0.01)
        if not r:
            return "ESC"
        seq = os.read(fd, 2).decode(errors="ignore")
        if len(seq) == 2 and seq[0] == "[":
            return ARROWS.get(seq[1])
        return None
    if b in (b"\r", b"\n"):
        return "ENTER"
    if b in (b"\x7f", b"\x08"):
        return "BACKSPACE"
    lead = b[0]
    n = 4 if lead >= 0xF0 else 3 if lead >= 0xE0 else 2 if lead >= 0xC0 else 1
    if n > 1:
        b += os.read(fd, n - 1)
    return b.decode(errors="ignore") or None


def play(session: Session, color: bool = True) -> None:
    import termios
    import tty

    stop = threading.Event()
    lock = threading.Lock()
    dirty = threading.Event()

    def poller():
        while not stop.is_set():
            try:
                session.poll(wait=1.0)
                dirty.set()
            except Exception as e:                      # 一時的な通信エラーは表示して再試行
                with lock:
                    session.view.log.append(f"net: {e}")
                dirty.set()
                stop.wait(1.0)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    t = threading.Thread(target=poller, daemon=True)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l\x1b[2J")
        t.start()
        last_status = 0.0
        while not stop.is_set():
            k = _read_key(fd, 0.1)
            if k == "\x03":
                break
            if k is not None and panel.handle_key(session.view, session, k):    # パネルが使った(Config の入力・スクロール・Esc)
                k = None
                dirty.set()
            if k is not None:
                res = dispatch_key(session, ARROW_TO_MOVE.get(k, k))
                if res == "quit":
                    break
                if res == "busy":
                    session.view.log.append("Too fast (command queue full)")
                if res:
                    dirty.set()
            if time.monotonic() - last_status > 3:     # 状態の突き合わせ(生死・体力のずれ防止)
                try:
                    session.refresh_status()
                except Exception:
                    pass
                last_status = time.monotonic()
                dirty.set()
            if dirty.is_set():
                dirty.clear()
                size = shutil.get_terminal_size((100, 32))
                out = frame(session, size.columns, size.lines, color)
                sys.stdout.write("\x1b[H" + out.replace("\n", "\x1b[K\n") + "\x1b[K\x1b[J")
                sys.stdout.flush()
    finally:
        stop.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?25h\n")
        session.close()
