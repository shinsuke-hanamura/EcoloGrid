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
from . import panel
from .api import ApiError
from .session import Session
from .view import tick_label

KEYS = {"w": ("move", {"dir": "n"}), "s": ("move", {"dir": "s"}), "a": ("move", {"dir": "w"}),
        "d": ("move", {"dir": "e"}), "e": ("eat", {}), "r": ("drink", {}), "z": ("rest", {}), ".": ("wait", {}),
        " ": ("wait", {}), "x": ("respawn", {})}
ARROWS = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}      # ESC [ A/B/C/D
ARROW_TO_MOVE = {"UP": "w", "DOWN": "s", "RIGHT": "d", "LEFT": "a"}


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


def rename_screen(session: Session, message: str = "", input_fn=input, out=print, joined: bool = True) -> bool:
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


def _tick_error_text(e: ApiError) -> str:
    if e.status == 429:
        return "Changed too recently (the interval can be changed once per second). Try again in a second."
    if e.status == 403:
        return "You are not allowed to change the tick interval."
    if e.status == 422:
        return "The interval must be an integer from 0 to 1000 (0 = max speed)."
    return str(e)


def tick_screen(session: Session, message: str = "", input_fn=input, out=print) -> bool:
    """進行の速さ(1 tick の実時間 ms)の変更画面。全プレイヤーのゲームが同じ割合で速く/遅くなる。0〜1000、0 または max = 最速。"""
    lim = session.config.get("limits", {})
    lo, hi = lim.get("tick_interval_ms_min", 0), lim.get("tick_interval_ms_max", 1000)
    while True:
        out("\n=== Config: tick interval (game speed) ===")
        out(f"Current: {tick_label(session.tick_interval_ms)}   (standard: 50 ms)")
        out(f"Range: {lo} to {hi} ms. 0 (or 'max') = no waiting, the fastest. This changes the speed for ALL players.")
        if message:
            out(message)
        raw = input_fn("New interval in ms (Enter = cancel): ").strip().lower()
        if not raw:
            return False
        if raw == "max":
            raw = "0"
        if not raw.isdigit() or not lo <= int(raw) <= hi:
            message = f"Enter an integer from {lo} to {hi}, or 'max'."
            continue
        try:
            session.change_tick_interval(int(raw))
            out(f"Tick interval set: {tick_label(session.tick_interval_ms)}")
            return True
        except ApiError as e:
            message = _tick_error_text(e)


def config_screen(session: Session, input_fn=input, out=print) -> bool:
    """設定画面(c キー): 項目を選ぶ。1 = 名前、2 = 進行の速さ(tick の間隔)。空入力で中止。"""
    while True:
        out("\n=== Config ===")
        out(f"1) Player name    : {session.name}")
        out(f"2) Tick interval  : {tick_label(session.tick_interval_ms)}")
        choice = input_fn("Select 1-2 (Enter = close): ").strip()
        if not choice:
            return False
        if choice == "1":
            rename_screen(session, input_fn=input_fn, out=out)
        elif choice == "2":
            tick_screen(session, input_fn=input_fn, out=out)
        else:
            out("Enter 1 or 2.")


def start_interactive(session: Session, input_fn=input, out=print) -> bool:
    """参加。名前が使用中なら、名前の変更画面で再設定させる。中止したら False。"""
    try:
        session.start()
        return True
    except ApiError as e:
        if e.code != "name_taken":
            raise
        return rename_screen(session, _name_error_text(e, session.name), input_fn, out, joined=False)


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
            if panel.captures_input(session.view):     # サイドパネルの Config 中は、キーをパネルが受け取る
                if k == "\x03":
                    break
                if k is not None:
                    panel.handle_key(session.view, session, k)
                    dirty.set()
                k = None
            k = ARROW_TO_MOVE.get(k, k) if k else k
            if k == "q" or k == "\x03":
                break
            if k == TOGGLE_TIME_KEY:
                session.view.toggle_time_format()
                dirty.set()
            if k == HELP_KEY:
                session.view.toggle_help()
                dirty.set()
            if k == CONFIG_KEY and shutil.get_terminal_size((100, 32)).columns >= 40 + panel.PANEL_W:
                panel.open_config(session.view)       # サイドパネルに Config を出す
                dirty.set()
            elif k == CONFIG_KEY:                     # 端末が狭い: 通常の入力モードに戻して、設定の画面を出す(従来の方式)
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
