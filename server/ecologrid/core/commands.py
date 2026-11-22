"""コマンドの受理の判定を、1 つの関数にまとめる(check_command)。Region のコマンド処理が使う。
(次の issue の、呼び出し画面(Actions ページ)用の API も、同じ判定を使う予定。)

拒否の理由:
    止めている状態の名前   asleep・unconscious・sleep_stance など(登録表 core/state.py の actions が False の状態)
    exhausted              スタミナ不足(move・eat・drink)
    bad_dir                move の方向が不正
    unsafe                 安全でないので、sleep できない(戦闘中・落下中)
    sleep_disabled         この Player は、睡眠が無効
    unknown_command        知らない種類
実行して初めて分かる理由(no_prey・no_water・unsafe(rest))は、実行側(Region)が返す。
"""
from typing import Any, Dict, Optional

from .grid import DIRS
from .messages import COMMAND_TYPES


def check_command(p, region, ctype: str, args: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """拒否の理由。受理できれば None。副作用はない(状態を変えない)。"""
    blocker = p.blocking_state(ctype)           # 眠っている・体制などが、その action を止めている
    if blocker is not None:
        return blocker
    if ctype not in COMMAND_TYPES:
        return "unknown_command"
    sp = p.spec
    cost = {"move": sp.stamina_cost_move, "eat": sp.stamina_cost_eat, "drink": sp.stamina_cost_drink}.get(ctype)
    if cost is not None and p.stamina < cost:
        return "exhausted"
    if ctype == "move" and (args or {}).get("dir") not in DIRS:
        return "bad_dir"
    if ctype == "sleep":
        if not p.sleep_enabled(region):
            return "sleep_disabled"
        if not p.is_safe():
            return "unsafe"
    return None
