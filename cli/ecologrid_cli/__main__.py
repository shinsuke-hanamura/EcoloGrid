"""Ecologrid CLI クライアント。

  python -m ecologrid_cli check                       # サーバ疎通確認
  python -m ecologrid_cli play --name alice           # 対話プレイ(キー入力、実時間)
  python -m ecologrid_cli play --name alice --script "dddss.e"   # キー列を送って最終フレームを表示
  python -m ecologrid_cli bot --name bot1 --seconds 60           # 自動プレイ(負荷試験)
"""
import argparse
import os
import sys

from .api import ApiClient, ApiError
from .bot import Bot
from .play import frame, play, run_script, start_interactive
from .session import Session


def _client(a) -> ApiClient:
    return ApiClient(a.server, join_key=a.join_key)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ecologrid_cli")
    p.add_argument("--server", default=os.environ.get("ECOLOGRID_SERVER", "http://localhost:8000"))
    p.add_argument("--join-key", default=os.environ.get("ECOLOGRID_JOIN_KEY"))
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("check")
    pp = sub.add_parser("play")
    pp.add_argument("--name", default=os.environ.get("USER", "player"))
    pp.add_argument("--script", help="キー列(w/a/s/d/e/r/./x/t/h)を順に送り、最終フレームを表示して終了")
    pp.add_argument("--no-color", action="store_true")
    pp.add_argument("--time-format", choices=("clock", "tick"), default="clock",
                    help="ヘッダの時間表記: clock(Day 3 14:05:30)/ tick(Tick 259,200)。実行中は t キーで切り替え")
    pb = sub.add_parser("bot")
    pb.add_argument("--name", default="bot")
    pb.add_argument("--seconds", type=float, default=30)
    pb.add_argument("--seed", type=int)
    a = p.parse_args(argv)
    a.cmd = a.cmd or "check"

    try:
        if a.cmd == "check":
            h = _client(a).health()
            print(h)
            return 0
        s = Session(_client(a), a.name, on_info=lambda m: print(m, file=sys.stderr))
        if a.cmd == "play" and a.script is None and sys.stdin.isatty():
            if not start_interactive(s):            # 名前が使用中 → 名前の変更画面で再設定
                return 1
        else:
            s.start()
        s.view.time_format = getattr(a, "time_format", "clock")
        if a.cmd == "bot":
            try:
                counts = Bot(s, a.seed).run(a.seconds)
            finally:
                s.close()
            print("bot finished:", counts, "reconnects:", s.reconnects)
            return 0
        if a.script is not None:
            try:
                run_script(s, a.script)
                print(frame(s, 100, 32, color=False))
            finally:
                s.close()
            return 0
        if not sys.stdin.isatty():
            print("play requires a terminal (use --script for non-interactive)", file=sys.stderr)
            s.close()
            return 2
        play(s, color=not a.no_color)
        return 0
    except ApiError as e:
        print(f"API error: {e}", file=sys.stderr)
        return 1
    except Exception as e:                    # 接続不可など
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
