"""対話プレイ(端末のキー入力)。世界は実時間で進み続けるので、1 キー = 1 コマンド。

構成: イベント取得は別スレッド(ロングポーリング)、メインスレッドはキー入力と描画。
"""
import os
import select
import shutil
import sys
import threading
import time
from typing import Optional

from .render import render
from .api import ApiError
from .session import Session

KEYS = {"w": ("move", {"dir": "n"}), "s": ("move", {"dir": "s"}), "a": ("move", {"dir": "w"}),
        "d": ("move", {"dir": "e"}), "e": ("eat", {}), "r": ("drink", {}), "z": ("rest", {}), ".": ("wait", {}),
        " ": ("wait", {}), "x": ("respawn", {})}
ARROWS = {"A": "w", "B": "s", "C": "d", "D": "a"}      # ESC [ A/B/C/D


CONFIG_KEY = "c"         # 設定画面(名前の変更)。マップ上のプレイヤーの表示は、名前の 1 文字目
HELP_KEY = "h"           # マップ横の凡例(文字の説明)の表示/非表示
TOGGLE_TIME_KEY = "t"      # ヘッダの時間表記を、時刻表記(Day 3 14:05:30)と tick 表記(Tick 259,200)で切り替える


def key_to_command(key: str):
    return KEYS.get(key)


def _name_error_text(e: ApiError, name: str) -> str:
    if e.code == "name_taken":
        return f"The name '{name}' is already used by another player."
    if e.code == "name_invalid" or e.status == 422:
        return "That name is not valid (1 to 32 characters)."
    return str(e)


def config_screen(session: Session, message: str = "", input_fn=input, out=print, joined: bool = True) -> bool:
    """名前の変更画面。使用中の名前は、再入力させる(入力のたびにサーバで重複チェック)。

    joined=False は起動時(まだ参加していない): 決まったら参加まで行う。空入力で中止(False)。
    """
    while True:
        out("\n=== Config: player name ===")
        out(f"Current name: {session.name}   (shown on the map as: {session.name[:1] or '@'})")
        if message:
            out(message)
        new = input_fn("New name (Enter = cancel): ").strip()
        if not new:
            return False
        if new == session.name and joined:
            return True
        try:
            if joined:
                session.change_name(new)
            else:
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
        return config_screen(session, _name_error_text(e, session.name), input_fn, out, joined=False)


def run_script(session: Session, script: str, step_wait: float = 0.3, sleep=time.sleep) -> None:
    """非対話: script の各文字をキーとして順に送る(自動テスト・デモ用)。"""
    for ch in script:
        if ch == TOGGLE_TIME_KEY:
            session.view.toggle_time_format()
            continue
        if ch == HELP_KEY:
            session.view.toggle_help()
            continue
        if ch == CONFIG_KEY:
            continue                                   # 設定画面は対話専用(スクリプトでは何もしない)
        cmd = key_to_command(ch)
        if cmd:
            session.send(cmd[0], **cmd[1])
        sleep(step_wait)
        session.poll(0)


def frame(session: Session, cols: int, rows: int, color: bool) -> str:
    return render(session.view, cols, rows, name=session.name, color=color)


def _read_key(fd: int, timeout: float) -> Optional[str]:
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    ch = os.read(fd, 1).decode(errors="ignore")
    if ch == "\x1b":
        r, _, _ = select.select([fd], [], [], 0.01)
        if r:
            seq = os.read(fd, 2).decode(errors="ignore")
            if len(seq) == 2 and seq[0] == "[":
                return ARROWS.get(seq[1])
        return None
    return ch


def play(session: Session, color: bool = True) -> None:
    import termios
    import tty

    stop = threading.Event()
    lock = threading.Lock()
    dirty = threading.Event()

    def poller():
        while not stop.is_set():
            try:
                with_wait = session.poll(wait=1.0)
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
            if k == "q" or k == "\x03":
                break
            if k == TOGGLE_TIME_KEY:
                session.view.toggle_time_format()
                dirty.set()
            if k == HELP_KEY:
                session.view.toggle_help()
                dirty.set()
            if k == CONFIG_KEY:                       # 通常の入力モードに戻して、名前の変更画面を出す
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                sys.stdout.write("\x1b[?25h\x1b[2J\x1b[H")
                sys.stdout.flush()
                try:
                    config_screen(session)
                except (EOFError, KeyboardInterrupt):
                    pass
                tty.setcbreak(fd)
                sys.stdout.write("\x1b[?25l\x1b[2J")
                dirty.set()
            cmd = key_to_command(k) if k else None
            if cmd:
                r = session.send(cmd[0], **cmd[1])
                if r == "busy":
                    session.view.log.append("Too fast (command queue full)")
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
