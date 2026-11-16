"""文字列の表示幅の扱い(全角は 2 桁)。端末の幅を超えると折り返して、行がずれるので、幅に収める道具を集める。"""
import re
import unicodedata

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(s: str) -> int:
    """色の指定(ANSI)を桁に数えない、表示幅。"""
    return sum(char_width(c) for c in ANSI.sub("", s))


def fit(s: str, width: int) -> str:
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


def clip(line: str, width: int) -> str:
    """表示幅 width 桁に収める(色の指定は桁に数えない。途中で切ったら、色を戻す)。"""
    out, w, cut = [], 0, False
    for tok in re.split(r"(\x1b\[[0-9;]*m)", line):
        if ANSI.fullmatch(tok):
            out.append(tok)
            continue
        for ch in tok:
            cw = char_width(ch)
            if w + cw > width:
                cut = True
                break
            out.append(ch)
            w += cw
        if cut:
            break
    return "".join(out) + ("\x1b[0m" if cut and any(ANSI.fullmatch(t) for t in out) else "")


def odd_at_most(n: int, lo: int = 5) -> int:
    """n 以下の最大の奇数(中心のセルが決まるように、地図の幅・高さは奇数にする)。n を超えない。"""
    n = max(lo, n)
    return n if n % 2 else n - 1
