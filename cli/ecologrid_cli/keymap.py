"""キーの表(KEYMAP): 対話プレイのキーの定義を、ここだけに書く。

ホットキーの振り分け(play.py)・Help の「Keys」・最下行は、すべてこの表から作る。キーを足すときは、表に 1 行足す。
種類(kind):
    move     移動(wasd・矢印)         command  1 キーでコマンドを送る(e・r・z・.・x)
    panel    サイドパネルのページを開く/閉じる(h・c)    toggle   表示の切り替え(t)    quit  終了(q)
将来、ホットキーを割り当てない action は、Actions ページの一覧にだけ出す(別の issue)。
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class KeyDef:
    key: str                                   # 押すキー(1 文字)
    kind: str
    label: str                                 # 表示するキー名
    desc: str                                  # 説明
    command: Optional[Tuple[str, dict]] = None      # kind が move・command のとき: (コマンドの種類, 引数)
    page: Optional[str] = None                 # kind が panel のとき: ページ名
    group: str = ""                            # 同じ group は、1 つにまとめて表示する(最初の項目が代表)
    short: str = ""                            # 狭い端末での説明(空なら desc)
    hidden: bool = False                       # 表示しない別名(スペースキーなど)


KEYMAP: List[KeyDef] = [
    KeyDef("w", "move", "wasd/arrows", "move", ("move", {"dir": "n"}), group="move", short="move"),
    KeyDef("a", "move", "wasd/arrows", "move", ("move", {"dir": "w"}), group="move"),
    KeyDef("s", "move", "wasd/arrows", "move", ("move", {"dir": "s"}), group="move"),
    KeyDef("d", "move", "wasd/arrows", "move", ("move", {"dir": "e"}), group="move"),
    KeyDef("e", "command", "e", "eat", ("eat", {})),
    KeyDef("r", "command", "r", "drink", ("drink", {})),
    KeyDef("z", "command", "z", "rest", ("rest", {})),
    KeyDef(".", "command", ".", "wait", ("wait", {}), group="wait"),
    KeyDef(" ", "command", ".", "wait", ("wait", {}), group="wait", hidden=True),
    KeyDef("x", "command", "x", "respawn", ("respawn", {})),
    # 入眠体制(仮のホットキー。呼び出し画面(Actions ページ)ができたら、そちらに移して、外す)
    KeyDef("l", "command", "l", "sleep", ("sleep", {})),
    KeyDef("L", "command", "L", "cancel_sleep", ("cancel_sleep", {}), short="cancel"),
    KeyDef("c", "panel", "c", "config", page="config"),
    KeyDef("t", "toggle", "t", "time/tick", short="time"),
    KeyDef("h", "panel", "h", "help", page="help"),
    KeyDef("q", "quit", "q", "quit"),
]

BY_KEY: Dict[str, KeyDef] = {k.key: k for k in KEYMAP}
HOTKEYS: Dict[str, Tuple[str, dict]] = {k.key: k.command for k in KEYMAP if k.command}


def shown() -> List[KeyDef]:
    """表示する項目(group は、最初の項目だけ。別名は除く)。"""
    seen, out = set(), []
    for k in KEYMAP:
        if k.hidden:
            continue
        gid = k.group or k.key
        if gid in seen:
            continue
        seen.add(gid)
        out.append(k)
    return out


def help_key_lines() -> List[str]:
    """Help の「Keys」の行。"""
    return [f" {k.label:<12} {k.desc}" for k in shown()]


def footer_variants() -> List[str]:
    """最下行の候補(詳しいものから)。端末の幅に収まる、最初のものを使う。"""
    ks = shown()
    full = " " + "  ".join(f"{k.label}:{k.desc}" for k in ks)
    mid = " " + " ".join(f"{k.label.split('/')[0]}:{k.short or k.desc}" for k in ks)
    acts = " ".join(k.label.split("/")[0] for k in ks if k.kind in ("move", "command"))
    tail_ = " ".join(f"{k.label}:{k.desc}" for k in ks if k.kind in ("panel", "quit") and k.page != "config")
    compact = f" {acts} | c:config {tail_}"
    return [full, mid, compact, " h:help q:quit"]
