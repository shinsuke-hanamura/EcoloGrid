"""定期アクション: 「N tick ごとに実行される処理」の登録簿と実行ルール。

tick 番号は World が持つ絶対値 `tick_no`(1 始まり)。アクションには 2 種類の実行モードがある。

- **world モード**(世界全体の周期処理: 天候・昼夜・定期保存など):
  `tick_no % N == offset` のとき 1 回実行。offset で同じ N のアクションの実行 tick をずらせる。
- **entity モード**(個体ごとの周期処理: 妊娠の成長・子作り判定など):
  個体ごとの基準 tick(anchor)から数えて N tick ごと、すなわち
  `(tick_no - anchor) % N == 0` かつ `tick_no > anchor` のとき、その個体に対して実行。
  個体ごとに実行タイミングが分散し、「基準 tick から正確に N tick 後」になる。
  anchor は `start="spawn"` なら個体の誕生 tick(birth_tick)、`start="manual"` なら
  `Region.start_timer` を呼んだ tick(妊娠成功の tick など)。

entity モードは `(tick_no - anchor) % N == 0 ⇔ tick_no % N == anchor % N` を利用し、
`anchor % N` をスロット番号とする **タイマーホイール**(TimerWheel)に個体を登録する。
各 tick は `tick_no % N` のスロットだけを引くので、全個体の走査は不要。

アクションは **データ**(ScheduledAction)として ActionStore に保管する。処理本体(ハンドラ)は
名前でコード側のレジストリに登録する。ストアを DB(Valkey 等)に差し替えれば、再起動をまたいだ
管理や運用中の追加・無効化ができる(ホイールも `due:{scope}:{action_id}:{slot}` のような
キーで持てる)。ストアは **スコープ**(キー)ごとにアクション一覧を持つ:
`"global"`(全 Region 共通)と `"region:{x}:{y}"`(その Region のみ)。将来は
`"area:..."` のような単位を `scopes_for_region` に足すだけで分割・分散できる。
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol

GLOBAL_SCOPE = "global"
WORLD, ENTITY = "world", "entity"
START_SPAWN, START_MANUAL = "spawn", "manual"


def region_scope(rid) -> str:
    return f"region:{rid[0]}:{rid[1]}"


def scopes_for_region(rid) -> List[str]:
    """この Region が実行するアクションのスコープ(実行順)。"""
    return [GLOBAL_SCOPE, region_scope(rid)]


@dataclass(frozen=True)
class ScheduledAction:
    action_id: str                    # スコープ内で一意
    handler: str                      # レジストリ上のハンドラ名
    interval_ticks: int               # N。tick_no % N == 0 で実行
    params: Dict = field(default_factory=dict)
    enabled: bool = True
    mode: str = WORLD                 # "world" | "entity"
    offset: int = 0                   # world モード: tick_no % N == offset % N
    species: Optional[str] = None     # entity モード: 対象の種
    start: str = START_SPAWN          # entity モード: タイマー開始 "spawn"(誕生時)| "manual"

    def __post_init__(self):
        if self.interval_ticks < 1:
            raise ValueError("interval_ticks must be >= 1")
        if self.mode not in (WORLD, ENTITY):
            raise ValueError(f"bad mode: {self.mode!r}")
        if self.mode == ENTITY and not self.species:
            raise ValueError("entity mode requires species")
        if self.start not in (START_SPAWN, START_MANUAL):
            raise ValueError(f"bad start: {self.start!r}")

    def is_due(self, tick_no: int) -> bool:
        """world モードの実行判定。entity モードは個体ごとに判定するので常に False。"""
        return (self.enabled and self.mode == WORLD
                and tick_no % self.interval_ticks == self.offset % self.interval_ticks)

    def to_dict(self) -> dict:
        return {"action_id": self.action_id, "handler": self.handler,
                "interval_ticks": self.interval_ticks, "params": self.params,
                "enabled": self.enabled, "mode": self.mode, "offset": self.offset,
                "species": self.species, "start": self.start}


class ActionStore(Protocol):
    """アクション一覧の保管先。Phase 1 はメモリ、Phase 2 以降は Valkey 等に差し替える。

    `version`(整数、任意): put / remove のたびに増える版番号。Region は、これが変わらない限り、
    アクションの解決結果(ActionPlan)をキャッシュして使う。持たないストアは、毎回解決し直す(遅いが正しい)。
    """

    def put(self, scope: str, action: ScheduledAction) -> None: ...
    def remove(self, scope: str, action_id: str) -> bool: ...
    def list(self, scope: str) -> List[ScheduledAction]: ...
    def due(self, scope: str, tick_no: int) -> List[ScheduledAction]: ...   # world モードのみ
    def entity_actions(self, scope: str) -> List[ScheduledAction]: ...     # 有効な entity モード


class InMemoryActionStore:
    def __init__(self):
        self._data: Dict[str, Dict[str, ScheduledAction]] = {}
        self.version = 0     # 版番号(put / remove のたびに増える。Region の ActionPlan のキャッシュの検証に使う)

    def put(self, scope: str, action: ScheduledAction) -> None:
        self._data.setdefault(scope, {})[action.action_id] = action   # 同 ID は置換
        self.version += 1

    def remove(self, scope: str, action_id: str) -> bool:
        removed = self._data.get(scope, {}).pop(action_id, None) is not None
        if removed:
            self.version += 1
        return removed

    def list(self, scope: str) -> List[ScheduledAction]:
        return list(self._data.get(scope, {}).values())

    def due(self, scope: str, tick_no: int) -> List[ScheduledAction]:
        return [a for a in self._data.get(scope, {}).values() if a.is_due(tick_no)]

    def entity_actions(self, scope: str) -> List[ScheduledAction]:
        return [a for a in self._data.get(scope, {}).values() if a.mode == ENTITY and a.enabled]


class TimerWheel:
    """entity モード用。スロット = anchor % N。tick_no % N のスロットが、その tick の実行対象。"""

    def __init__(self, interval: int):
        self.n = interval
        self.slots: Dict[int, Dict[str, None]] = {}   # dict は挿入順 = 決定論的な実行順

    def add(self, entity_id: str, anchor: int) -> None:
        self.slots.setdefault(anchor % self.n, {})[entity_id] = None

    def discard(self, entity_id: str, anchor: int) -> None:
        slot = self.slots.get(anchor % self.n)
        if slot is not None:
            slot.pop(entity_id, None)
            if not slot:
                del self.slots[anchor % self.n]

    def due(self, tick_no: int) -> List[str]:
        return list(self.slots.get(tick_no % self.n, ()))


# --- ハンドラ・レジストリ ---
Handler = Callable[[object, ScheduledAction, int], None]                 # world: (region, action, tick_no)
EntityHandler = Callable[[object, ScheduledAction, int, object], None]   # entity: (+ entity)
_HANDLERS: Dict[str, Handler] = {}
_ENTITY_HANDLERS: Dict[str, EntityHandler] = {}


def register_handler(name: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[name] = fn
        return fn
    return deco


def register_entity_handler(name: str):
    def deco(fn: EntityHandler) -> EntityHandler:
        _ENTITY_HANDLERS[name] = fn
        return fn
    return deco


def get_handler(name: str) -> Handler:
    try:
        return _HANDLERS[name]
    except KeyError:
        raise KeyError(f"unknown action handler: {name!r}") from None


def get_entity_handler(name: str) -> EntityHandler:
    try:
        return _ENTITY_HANDLERS[name]
    except KeyError:
        raise KeyError(f"unknown entity action handler: {name!r}") from None


def run_due_actions(region, store: ActionStore, tick_no: int) -> int:
    """Region の担当スコープの、この tick に該当するアクションを順に実行。world モードの実行数を返す。

    キャッシュを使わない基準の実装(毎 tick、スコープとアクションを解決し直す)。Region は、既定では
    ActionPlan(`Region._run_actions`)を使う。`Region.cache_actions = False` のとき、またはテストの
    等価性の確認に、こちらを使う。"""
    n = 0
    for scope in scopes_for_region(region.rid):
        for act in store.due(scope, tick_no):
            get_handler(act.handler)(region, act, tick_no)
            n += 1
        for act in store.entity_actions(scope):
            region.run_entity_action(act, tick_no)
    return n
