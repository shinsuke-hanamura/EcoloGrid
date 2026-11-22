"""状態(state): 個体の状態を、名前 → 真偽の dict(StateDict)で持つ。仕様: docs/spec/animal-states-spec.md §4。

- **保存する状態**: asleep・unconscious・sleep_stance・in_combat・falling など。真のものだけを持つ(偽 = 書かれていない)。
- **導く状態**: awake(= not (asleep or unconscious))・hungry・thirsty。値から計算するので、保存せず、書き込めない。
- **体制(stance)と状態(condition)の区別は、保存には持ち込まず、登録表(STATES)の属性 kind で持つ**。
  体制 = 本人の action で入り、cancel_<action> で解除する。状態 = 出来事・しきい値・時間・確率で決まる。
- **排他のルール**(実装では強制しない): asleep と unconscious は、同時に真にならない(awake は導く)。他は独立。
  書き込むのは、遷移の関数だけ(Animal.set_state / Animal._become / load_state。tests/test_state.py が、ソースを調べて確認する)。
- 体制・状態は、できる action(actions。"*" はすべて)と、パラメータの係数(factors。式)を持つ。
  種のパラメータ <状態名>_factors / <状態名>_actions で、種ごとに上書きできる(個体の発生時にコピー)。
"""
import ast
import operator
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

CONSCIOUSNESS = ("awake", "asleep", "unconscious")
KINDS = ("stance", "condition")

# 体制・状態の係数が効くパラメータ(登録されたものだけ)と、その範囲
EFFECT_PARAMS: Dict[str, Tuple[float, float]] = {"wakefulness": (0.0, 1.0)}


@dataclass(frozen=True)
class StateDef:
    name: str
    kind: str                                   # "stance" | "condition"
    label: str                                  # 表示名
    public: bool = False                        # 他のプレイヤーに見せるか(描画に使う asleep・unconscious・awake など)
    actions: Dict[str, bool] = field(default_factory=dict)    # できる(True)・できない(False)。"*" = すべての action
    factors: Dict[str, str] = field(default_factory=dict)     # パラメータ名 -> 式(変数 x = 適用前の値)
    derive: Optional[Callable] = None           # あれば、導く状態(owner を受け取り、真偽を返す)
    enter_action: str = ""                      # 体制: 入る action
    cancel_action: str = ""                     # 体制: 解除する action(= "cancel_" + enter_action)
    ends_on: Tuple[str, ...] = ()               # 体制: 終わる出来事(asleep / unconscious / unsafe)
    emit_event: bool = True                     # 変化のとき、state_entered / state_left を出すか(asleep・unconscious は、
                                                # fell_asleep・woke_up・fainted が、すでにあるので出さない)


def _awake(owner) -> bool:
    s = owner.state
    return not (dict.get(s, "asleep", False) or dict.get(s, "unconscious", False))


def _hungry(owner) -> bool:
    return owner.is_hungry()


def _thirsty(owner) -> bool:
    return owner.is_thirsty()


# 登録表。**順序が意味を持つ**: できない action の理由は、この順で、最初に止めている状態の名前を返す。
STATES: Dict[str, StateDef] = {d.name: d for d in (
    # 意識の状態(排他のルール)
    StateDef("asleep", "condition", "Asleep", public=True, actions={"*": False}, emit_event=False),
    StateDef("unconscious", "condition", "Unconscious", public=True, actions={"*": False}, emit_event=False),
    # 体制
    StateDef("sleep_stance", "stance", "Ready to sleep", public=False,
             actions={"move": False, "eat": False, "drink": False},
             factors={"wakefulness": "x * 0.2"},
             enter_action="sleep", cancel_action="cancel_sleep", ends_on=("asleep", "unconscious", "unsafe")),
    # 状態(保存。戦闘・落下は未実装: docs/tasks/x_10。テスト用)
    StateDef("in_combat", "condition", "In combat"),
    StateDef("falling", "condition", "Falling"),
    StateDef("poisoned", "condition", "Poisoned"),       # 毒(効果は未実装。睡眠などと、独立に並ぶことを表せる。TODO)
    # 導く状態
    StateDef("awake", "condition", "Awake", public=True, derive=_awake),
    StateDef("hungry", "condition", "Hungry", derive=_hungry),
    StateDef("thirsty", "condition", "Thirsty", derive=_thirsty),
)}

ENTER_ACTIONS: Dict[str, str] = {d.enter_action: d.name for d in STATES.values() if d.kind == "stance"}
CANCEL_ACTIONS: Dict[str, str] = {d.cancel_action: d.name for d in STATES.values() if d.kind == "stance"}
STATE_ACTIONS = tuple(ENTER_ACTIONS) + tuple(CANCEL_ACTIONS)        # 状態を変える action(sleep・cancel_sleep)


_STORED = frozenset(n for n, d in STATES.items() if d.derive is None)          # 保存する状態の名前(get の高速な経路)


def is_stored(name: str) -> bool:
    return name in _STORED


class StateDict(dict):
    """名前 → 真偽。保存するのは、真のものだけ(偽にすると消える。入った順を保つ)。導く状態は get で返し、書き込めない。"""

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self.awake = True                           # get("awake") と同じ値(導く)。状態が変わるたびに計算し直す。毎 tick 何度も読む経路の高速化

    # --- 読む ---
    def get(self, name, default=False):
        if name in _STORED:                          # 保存する状態(最も多い)
            return dict.get(self, name, default)
        if name == "awake":
            return self.awake
        d = STATES.get(name)
        if d is None:
            raise KeyError(f"unknown state: {name!r}")
        if d.derive is not None:
            return bool(d.derive(self._owner))
        return dict.get(self, name, default)

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name) -> bool:
        return name in STATES and bool(self.get(name))

    # --- 書く(書いてよいのは、遷移の関数だけ) ---
    def __setitem__(self, name, value) -> None:
        d = STATES.get(name)
        if d is None:
            raise KeyError(f"unknown state: {name!r}")
        if d.derive is not None:
            raise TypeError(f"{name!r} is derived and cannot be set")
        if value:
            if not dict.get(self, name, False):
                dict.__setitem__(self, name, True)
                self._changed()
        elif dict.__contains__(self, name):
            dict.__delitem__(self, name)
            self._changed()

    def __delitem__(self, name) -> None:
        self[name] = False

    def reset(self, names=()) -> None:
        """すべて偽にして、names(保存する状態のみ)を真にする(load_state・生成時の初期化用)。"""
        dict.clear(self)
        for n in names:
            if is_stored(n):
                dict.__setitem__(self, n, True)
        self._changed()

    def _changed(self) -> None:
        self.awake = not (dict.get(self, "asleep", False) or dict.get(self, "unconscious", False))
        cache = getattr(self._owner, "_blocked_cache", None)
        if cache is not None:
            cache.clear()

    def _no(self, *a, **k):
        raise TypeError("use state[name] = value")

    update = setdefault = pop = popitem = clear = _no

    # --- 一覧 ---
    def stored(self) -> List[str]:
        """保存された真の状態(入った順)。"""
        return list(dict.keys(self))

    def active(self, public_only: bool = False) -> List[str]:
        """真の状態の名前(保存・導出の両方。登録表の順)。public_only なら、他のプレイヤーに見せてよいものだけ。"""
        return [n for n, d in STATES.items() if (d.public or not public_only) and self.get(n)]


# --- 式の評価(限られた構文だけ。eval は使わない) ---
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.Pow: operator.pow}
_FUNCS = {"min": min, "max": max}
_compiled: Dict[str, Callable[[float], float]] = {}


def compile_expr(src: str) -> Callable[[float], float]:
    """式(例 "x * 0.5")を、x を受け取る関数にする。数値・x・+ - * / **・括弧・min・max だけ。違反は ValueError。"""
    fn = _compiled.get(src)
    if fn is not None:
        return fn
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid expression {src!r}: {e.msg}") from None

    def build(node):
        if isinstance(node, ast.Expression):
            return build(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            v = float(node.value)
            return lambda x: v
        if isinstance(node, ast.Name) and node.id == "x":
            return lambda x: x
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            f = build(node.operand)
            return (lambda x: -f(x)) if isinstance(node.op, ast.USub) else f
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            a, b, op = build(node.left), build(node.right), _BINOPS[type(node.op)]
            return lambda x: op(a(x), b(x))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
                and node.args and not node.keywords):
            fs, fn_ = [build(a) for a in node.args], _FUNCS[node.func.id]
            return lambda x: fn_(*[f(x) for f in fs])
        raise ValueError(f"unsupported syntax in expression {src!r}: {type(node).__name__}")

    fn = build(tree)
    _compiled[src] = fn
    return fn


def apply_factor(src: str, value: float, param: str) -> float:
    lo, hi = EFFECT_PARAMS[param]
    try:
        return min(hi, max(lo, compile_expr(src)(value)))
    except ArithmeticError:                          # 起動時の検査(validate_factors)をすり抜けた式でも、tick を止めない
        return value


def validate_factors(owner_name: str, factors: Dict[str, str]) -> None:
    for param, src in factors.items():
        if param not in EFFECT_PARAMS:
            raise ValueError(f"{owner_name}: unknown parameter {param!r} (known: {sorted(EFFECT_PARAMS)})")
        if not isinstance(src, str):
            raise ValueError(f"{owner_name}.{param}: expression must be a string")
        lo, hi = EFFECT_PARAMS[param]
        fn = compile_expr(src)
        for x in (lo, 0.5 * (lo + hi), hi):           # 範囲の両端と中央で、評価できること(0 除算など)の確認
            try:
                fn(x)
            except ArithmeticError as e:
                raise ValueError(f"{owner_name}.{param}: expression {src!r} cannot be evaluated at x={x}: {e}") from None


def validate_registry() -> None:
    """登録表の検査: 名前の一意(辞書なので自明)・kind・体制の action 名・係数。import 時に呼ぶ。"""
    for d in STATES.values():
        if d.kind not in KINDS:
            raise ValueError(f"{d.name}: kind must be one of {KINDS}")
        if d.kind == "stance":
            if not d.enter_action or d.cancel_action != "cancel_" + d.enter_action:
                raise ValueError(f"{d.name}: cancel_action must be 'cancel_' + enter_action")
            if d.derive is not None:
                raise ValueError(f"{d.name}: a stance cannot be derived")
        validate_factors(d.name, d.factors)


validate_registry()


def check_state_rules(owner) -> List[str]:
    """排他のルールの検査(テスト用。実行時には強制しない)。違反の説明を返す(なければ空)。"""
    s, out = owner.state, []
    if dict.get(s, "asleep", False) and dict.get(s, "unconscious", False):
        out.append("asleep and unconscious are both true")
    for n in s.stored():
        if not is_stored(n):
            out.append(f"{n} is stored but not a stored state")
    return out
