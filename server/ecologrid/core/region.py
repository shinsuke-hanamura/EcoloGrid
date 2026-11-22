"""Region: World を分割した 1 区画(既定 32x32)。tick を独立に実行できる単位。

他 Region のオブジェクトは直接触らない。越境(移動・種子散布)は outbox のメッセージで
World が配送する(将来は別サーバへのリクエストに置き換わる)。
"""
from typing import Dict, List, Optional, Tuple

from . import factory
from .actions import (ActionStore, InMemoryActionStore, ScheduledAction, TimerWheel,
                      START_SPAWN, ENTITY, WORLD, get_entity_handler, get_handler, run_due_actions,
                      scopes_for_region)
from .grid import DIRS, Pos, RegionId, chebyshev, manhattan, region_of
from .commands import check_command
from .messages import Command
from .rng import make_rng, new_id
from .seed import germinate_action_id
from .percept_map import BUCKET
from .species import PLANT_KEYS
from .state import CANCEL_ACTIONS, ENTER_ACTIONS
from .terrain import LAND, WATER, Terrain


class ActionPlan:
    """Region が、ストアの内容(アクション一覧)を解決した結果。ストアの版番号が変わらない限り使い回す。

    steps: 実行の手順。従来(run_due_actions)と同じ順序: スコープ順(global → region)で、各スコープの
      「world モードのアクション → entity モードのアクション」。各要素は次のリスト:
        world:  [False, act, None,  interval, offset % interval, handler or None]
        entity: [True,  act, wheel, 0,        0,                 handler or None]
      handler は、最初に実行されるときに解決してキャッシュする(未登録のハンドラは、実行されるときだけ KeyError)。
    by_species: 種 → その種の個体単位アクション(Region.add の、start="spawn" のタイマー開始に使う)。
    by_id: action_id → 個体単位アクション(Region.find_action に使う。先に出たスコープが優先)。
    """
    __slots__ = ("version", "steps", "by_species", "by_id")

    def __init__(self, version):
        self.version = version
        self.steps: List[list] = []
        self.by_species: Dict[str, List[ScheduledAction]] = {}
        self.by_id: Dict[str, ScheduledAction] = {}


class Region:
    def __init__(self, rid: RegionId, cfg, terrain: Terrain, actions: Optional[ActionStore] = None):
        self.rid = rid
        self.actions = actions if actions is not None else InMemoryActionStore()
        self.cache_actions = True              # False: 毎 tick アクションを解決し直す(基準の実装。テスト用)
        self._plan_cache: Optional[ActionPlan] = None
        self.plan_builds = 0                   # ActionPlan を作った回数(確認用)
        self.tick_no = 0   # World が毎 tick 設定する絶対 tick 番号
        self.cfg = cfg
        self.planet = cfg.planet_of(rid)   # この Region の惑星(重力・1 日の長さ・時計・気温)
        self.temperature_c = self.planet.temperature_at(0)   # 現在の気温(tick の最初に更新)
        self.terrain = terrain
        self.size = cfg.region_size
        self.x0, self.y0 = rid[0] * self.size, rid[1] * self.size
        self.rng = make_rng(cfg.seed, "region", rid[0], rid[1])
        self.entities: Dict[str, object] = {}
        self._cells: Dict[Pos, List[object]] = {}
        self._by_species: Dict[str, Dict[str, object]] = {}
        self._buckets: Dict[Tuple[int, int], Dict[str, object]] = {}   # 区画(BUCKET セル四方)ごとの個体の索引(知覚の走査用)
        self._plant_buckets: Dict[Tuple[int, int], Dict[str, object]] = {}   # 同、植物だけ(資源の層の走査用。植物は静的)
        self.plant_version = 0                 # 植物が増減するたびに増える(知覚の走査を省く判定に使う)
        self._tickers: Dict[str, object] = {}       # 毎 tick の tick を呼ぶ Entity(動物・Player)だけ。植物・種子・卵は含まない
        self._wheels: Dict[str, TimerWheel] = {}   # action_id -> 個体単位アクションのタイマーホイール
        self.events: List[dict] = []
        self.outbox: List[Tuple[RegionId, dict]] = []
        self.inbox: List[dict] = []
        self._water_near: Dict[Tuple[Pos, int], bool] = {}
        self.water_cells = set(terrain.cells_of_kind(self.x0, self.y0, self.size, WATER))
        self.land_cells = terrain.cells_of_kind(self.x0, self.y0, self.size, LAND)

    @property
    def gravity(self) -> float:
        return self.planet.gravity

    def game_time(self):
        from .clock import game_time
        return game_time(self.tick_no, self.planet)

    # --- イベント ---
    def emit(self, type_: str, regions=None, **data) -> None:
        self.events.append({"type": type_, "regions": regions or [self.rid], **data})

    def new_id(self) -> str:
        return new_id(self.rng)

    # --- Entity 管理 ---
    def _index(self, e) -> None:
        self.entities[e.entity_id] = e
        self._cells.setdefault(e.grid_position, []).append(e)
        self._by_species.setdefault(e.species, {})[e.entity_id] = e
        if e.ticks:
            self._tickers[e.entity_id] = e
        self._buckets.setdefault((e.grid_position.x // BUCKET, e.grid_position.y // BUCKET), {})[e.entity_id] = e
        if e.species in PLANT_KEYS:
            self.plant_version += 1
            self._plant_buckets.setdefault((e.grid_position.x // BUCKET, e.grid_position.y // BUCKET), {})[e.entity_id] = e
        for aid, anchor in e.timers.items():
            if aid in self._wheels:
                self._wheels[aid].add(e.entity_id, anchor)

    def _unindex(self, e) -> None:
        for aid, anchor in e.timers.items():
            if aid in self._wheels:
                self._wheels[aid].discard(e.entity_id, anchor)
        del self.entities[e.entity_id]
        self._tickers.pop(e.entity_id, None)
        if e.species in PLANT_KEYS:
            self.plant_version += 1
        bk = (e.grid_position.x // BUCKET, e.grid_position.y // BUCKET)
        for index in (self._buckets, self._plant_buckets):
            b = index.get(bk)
            if b is not None:
                b.pop(e.entity_id, None)
                if not b:
                    del index[bk]
        cell = self._cells[e.grid_position]
        cell.remove(e)
        if not cell:
            del self._cells[e.grid_position]
        del self._by_species[e.species][e.entity_id]

    def add(self, e, announce: bool = True) -> None:
        # AI 動物の行動の位相を散らす(全個体が同じ tick に考える負荷の集中を避ける。決定論的)
        if getattr(e, "next_action_tick", 0) < 0 and e.spec.cls != "Player":
            e.next_action_tick = self.tick_no + self.rng.randrange(max(1, e.spec.move_interval_ticks))
        if hasattr(e, "bind"):
            e.bind(self)              # 植物: Region の tick を基準に遅延評価を始める
        self._index(e)
        for act in self._entity_actions_for(e.species):   # start="spawn" のタイマーを開始
            self._attach(e, act, self._wheel_for(act))
        if announce:
            self.emit("entity_spawned", entity=e.to_dict())

    # --- 個体単位の定期アクション(タイマー) ---
    def _plan(self) -> ActionPlan:
        """現在のアクション計画。ストアの版番号が変わっていれば作り直す(版番号のないストアは毎回)。"""
        version = getattr(self.actions, "version", None)
        plan = self._plan_cache
        if plan is None or version is None or plan.version != version:
            plan = self._plan_cache = self._build_plan(version)
            self.plan_builds += 1
        return plan

    def _build_plan(self, version) -> ActionPlan:
        store = self.actions
        plan = ActionPlan(version)
        for scope in scopes_for_region(self.rid):
            acts = store.list(scope)
            for a in acts:                      # world モード(有効なもの)。従来の store.due と同じ条件
                if a.enabled and a.mode == WORLD:
                    plan.steps.append([False, a, None, a.interval_ticks, a.offset % a.interval_ticks, None])
            for a in acts:                      # entity モード(有効なもの)。従来の store.entity_actions と同じ条件
                if a.enabled and a.mode == ENTITY:
                    plan.steps.append([True, a, self._wheel_for(a), 0, 0, None])
                    plan.by_species.setdefault(a.species, []).append(a)
                    plan.by_id.setdefault(a.action_id, a)
        return plan

    def clear_action_cache(self, rebuild_wheels: bool = False) -> None:
        """アクション計画のキャッシュを捨てる(次の tick か、次の参照で作り直す)。
        rebuild_wheels=True: タイマーホイールも捨て、各個体の timers から作り直す(ホイールの不整合の復旧用)。
        個体の timers(基準 tick)は状態なので消さない。"""
        self._plan_cache = None
        if rebuild_wheels:
            self._wheels.clear()

    def _entity_actions_for(self, species: str) -> List[ScheduledAction]:
        return self._plan().by_species.get(species, [])

    def find_action(self, action_id: str) -> ScheduledAction:
        act = self._plan().by_id.get(action_id)
        if act is None:
            raise KeyError(f"no such entity action: {action_id!r}")
        return act

    def _run_actions(self, tick_no: int) -> None:
        """この tick に該当するアクションを順に実行する(ActionPlan を使う。キャッシュなしの基準は run_due_actions)。"""
        if not self.cache_actions:
            run_due_actions(self, self.actions, tick_no)
            return
        for st in self._plan().steps:
            if st[0]:                                       # entity モード: 実行する個体がいるスロットだけ処理
                wheel = st[2]
                if tick_no % wheel.n in wheel.slots:
                    self._run_wheel(st, wheel, tick_no)
            elif tick_no % st[3] == st[4]:                  # world モード
                handler = st[5]
                if handler is None:
                    handler = st[5] = get_handler(st[1].handler)
                handler(self, st[1], tick_no)

    def _run_wheel(self, st: list, wheel: TimerWheel, tick_no: int) -> None:
        act = st[1]
        handler = st[5]
        if handler is None:
            handler = st[5] = get_entity_handler(act.handler)
        for eid in wheel.due(tick_no):
            e = self.entities.get(eid)
            if e is None or getattr(e, "dead", False):
                continue
            anchor = e.timers.get(act.action_id)
            if anchor is None or anchor >= tick_no:         # 開始 tick 自身では実行しない
                continue
            handler(self, act, tick_no, e)

    def _wheel_for(self, act: ScheduledAction) -> TimerWheel:
        """アクション用ホイール。未作成/間隔が変わった場合は既存個体から作り直す(後から登録したアクションにも対応)。"""
        w = self._wheels.get(act.action_id)
        if w is None or w.n != act.interval_ticks:
            w = self._wheels[act.action_id] = TimerWheel(act.interval_ticks)
            for e in self.entities_of(act.species):
                self._attach(e, act, w)
        return w

    def _attach(self, e, act: ScheduledAction, wheel: TimerWheel) -> None:
        if act.action_id not in e.timers and act.start == START_SPAWN:
            e.timers[act.action_id] = getattr(e, "birth_tick", self.tick_no)
        if act.action_id in e.timers:
            wheel.add(e.entity_id, e.timers[act.action_id])

    def start_timer(self, e, action_id: str, anchor: Optional[int] = None) -> None:
        """個体 e の action_id タイマーを anchor(既定: 現在 tick)から開始。以後 anchor+N, +2N… に実行。"""
        wheel = self._wheel_for(self.find_action(action_id))
        old = e.timers.get(action_id)
        if old is not None:
            wheel.discard(e.entity_id, old)
        e.timers[action_id] = self.tick_no if anchor is None else anchor
        wheel.add(e.entity_id, e.timers[action_id])

    def stop_timer(self, e, action_id: str) -> None:
        anchor = e.timers.pop(action_id, None)
        if anchor is not None and action_id in self._wheels:
            self._wheels[action_id].discard(e.entity_id, anchor)

    def run_entity_action(self, act: ScheduledAction, tick_no: int) -> None:
        wheel = self._wheels.get(act.action_id)
        if wheel is None or wheel.n != act.interval_ticks:
            wheel = self._wheel_for(act)
        if tick_no % wheel.n not in wheel.slots:     # この tick に実行する個体がいない(ほとんどの tick)
            return
        handler = get_entity_handler(act.handler)
        for eid in wheel.due(tick_no):
            e = self.entities.get(eid)
            if e is None or getattr(e, "dead", False):
                continue
            anchor = e.timers.get(act.action_id)
            if anchor is None or anchor >= tick_no:     # 開始 tick 自身では実行しない
                continue
            handler(self, act, tick_no, e)

    def entities_of(self, species: str) -> List[object]:
        return list(self._by_species.get(species, {}).values())

    def spawn(self, species: str, pos: Pos, init: Optional[dict] = None, **kw):
        """init: 生成直後(イベント発行前)に上書きする属性(energy, birth_tick など)。"""
        e = factory.create(species, self.new_id(), pos, **kw)
        for k, v in (init or {}).items():
            setattr(e, k, v)
        self.add(e)
        if hasattr(e, "remember"):
            e.remember(self, "none", "born")            # 生まれた場所(家)を記憶する
        return e

    def remove(self, e, reason: str, **extra) -> None:
        if e.entity_id not in self.entities:
            return
        self._unindex(e)
        e.dead = True
        self.emit("entity_removed", id=e.entity_id, species=e.species, reason=reason,
                  pos=[e.grid_position.x, e.grid_position.y], **extra)

    def move(self, e, new_pos: Pos, duration_ticks: int = 0) -> None:
        old = e.grid_position
        dst = region_of(new_pos, self.size)
        self._unindex(e)
        e.grid_position = new_pos
        if dst == self.rid:
            self._index(e)
            self.emit("entity_moved", id=e.entity_id, species=e.species, frm=[old.x, old.y], to=[new_pos.x, new_pos.y],
                      duration_ticks=duration_ticks)
        else:
            self.outbox.append((dst, {"type": "handoff", "entity": e.to_state()}))
            self.emit("entity_moved", [self.rid, dst], id=e.entity_id, species=e.species,
                      frm=[old.x, old.y], to=[new_pos.x, new_pos.y], duration_ticks=duration_ticks)

    # --- 問い合わせ ---
    def entities_at(self, pos: Pos) -> List[object]:
        return list(self._cells.get(pos, ()))

    def walkable(self, pos: Pos) -> bool:
        return self.terrain.walkable(pos)

    def adjacent_water(self, pos: Pos) -> bool:
        return any(self.terrain.kind(pos.x + dx, pos.y + dy) == WATER for dx, dy in DIRS.values())

    def water_within(self, pos: Pos, radius: int) -> bool:
        # 水は静的(Phase 1)なので位置と半径だけで決まる。結果をキャッシュする
        key = (pos, radius)
        hit = self._water_near.get(key)
        if hit is None:
            hit = self._water_near[key] = any(manhattan(pos, w) <= radius for w in self.water_cells)
        return hit

    def entities_in_range(self, pos: Pos, r: int):
        """この Region の、pos から Chebyshev 距離 r 以内(自分のセル + 距離 r まで)の個体。区画の索引で引く。"""
        x0, y0 = pos.x, pos.y
        for by in range((y0 - r) // BUCKET, (y0 + r) // BUCKET + 1):
            for bx in range((x0 - r) // BUCKET, (x0 + r) // BUCKET + 1):
                b = self._buckets.get((bx, by))
                if b:
                    for e in list(b.values()):
                        if max(abs(e.grid_position.x - x0), abs(e.grid_position.y - y0)) <= r:
                            yield e

    def plants_in_range(self, pos: Pos, r: int):
        """この Region の、pos から Chebyshev 距離 r 以内の植物(区画の索引で引く)。"""
        x0, y0 = pos.x, pos.y
        for by in range((y0 - r) // BUCKET, (y0 + r) // BUCKET + 1):
            for bx in range((x0 - r) // BUCKET, (x0 + r) // BUCKET + 1):
                b = self._plant_buckets.get((bx, by))
                if b:
                    for e in b.values():
                        if max(abs(e.grid_position.x - x0), abs(e.grid_position.y - y0)) <= r:
                            yield e

    def nearest_water(self, pos: Pos, r: int) -> Optional[Pos]:
        """Chebyshev 距離 r 以内の最寄りの水セル(この Region 内)。"""
        best = min((w for w in self.water_cells if chebyshev(pos, w) <= r),
                   key=lambda w: (chebyshev(pos, w), manhattan(pos, w), w), default=None)
        return best

    def nearest_prey(self, a):
        """prey の優先順(先に書かれた種ほど好む)で、視界内の最も近い 1 体。"""
        for sp in a.spec.prey:
            cands = [e for e in self._by_species.get(sp, {}).values()
                     if chebyshev(a.grid_position, e.grid_position) <= a.effective_range(self)]
            if cands:
                return min(cands, key=lambda e: (manhattan(a.grid_position, e.grid_position), e.entity_id))
        return None

    # --- 植物のライフサイクル ---
    def sow(self, plant_type: str, pos: Pos) -> None:
        dst = region_of(pos, self.size)
        if dst == self.rid:
            self._place_seed(plant_type, pos)
        else:
            self.outbox.append((dst, {"type": "seed", "plant_type": plant_type, "pos": list(pos)}))

    def _place_seed(self, plant_type: str, pos: Pos) -> None:
        if not self.terrain.walkable(pos):
            return
        if any(e.species == "Seed" or e.species in ("Grass", "Wheat") for e in self._cells.get(pos, ())):
            return   # 1 セルに植物/種子は 1 つ
        # 発芽はタイマー: 落ちた tick の 1 つ前を基準にして、germination_ticks - 1 tick 後(従来の毎 tick の
        # age 加算と同じ tick)に germinate_seed が実行される
        self.spawn("Seed", pos, init={"timers": {germinate_action_id(plant_type): self.tick_no - 1}},
                   plant_type=plant_type)

    def germinate(self, seed) -> None:
        pos, plant_type = seed.grid_position, seed.plant_type
        self.remove(seed, "germinated")
        if any(e.species in ("Grass", "Wheat") for e in self._cells.get(pos, ())):
            return
        plant = self.spawn(plant_type, pos, init={"birth_tick": self.tick_no})
        self.emit("seed_germinated", seed_id=seed.entity_id, plant=plant.to_dict(), pos=[pos.x, pos.y])

    def hatch(self, egg) -> None:
        from .species import SPECIES
        self.remove(egg, "hatched")
        rd = SPECIES[egg.parent_species].repro
        self.spawn(egg.parent_species, egg.grid_position,
                   init={"energy": rd.offspring_energy, "birth_tick": self.tick_no})

    # --- tick ---
    def tick(self, commands: List[Tuple[str, Command]], tick_no: Optional[int] = None) -> None:
        if tick_no is not None:
            self.tick_no = tick_no
        self.temperature_c = self.planet.temperature_at(self.tick_no)
        self.process_inbox()
        for entity_id, cmd in commands:
            self._apply_command(entity_id, cmd)
        self._run_actions(self.tick_no)
        for e in list(self._tickers.values()):   # 動物・Player だけ(植物・種子・卵は毎 tick の処理がない)
            if e.entity_id in self.entities:     # この tick で食べられた等で消えていないか
                e.tick(self)

    def process_inbox(self) -> None:
        inbox, self.inbox = self.inbox, []
        for m in inbox:
            if m["type"] == "handoff":
                self.add(factory.restore(m["entity"]), announce=False)
            elif m["type"] == "seed":
                self._place_seed(m["plant_type"], Pos(*m["pos"]))

    def _reject(self, cmd: Command, reason: str) -> None:
        self.emit("command_rejected", player_id=cmd.player_id,
                  command_id=cmd.command_id, reason=reason)

    def _apply_command(self, entity_id: str, cmd: Command) -> None:
        p = self.entities.get(entity_id)
        if p is None:
            return
        reason = check_command(p, self, cmd.type, cmd.args)     # 拒否の理由(止めている状態の名前・exhausted など)
        if reason is not None:
            self._reject(cmd, reason)
            return
        if cmd.type == "move":
            p.stop_rest(self)                     # 移動の指示で休憩を終える
            p.pending_move = cmd.args.get("dir")
        elif cmd.type == "eat":
            if not p.eat_here(self, record_failure=True):
                self._reject(cmd, "no_prey")
        elif cmd.type == "drink":
            if not p.drink(self):
                self._reject(cmd, "no_water")
        elif cmd.type == "rest":
            if not p.start_rest(self) and not p.resting:
                self._reject(cmd, "unsafe")
        elif cmd.type in ENTER_ACTIONS:           # 体制に入る(すでに入っていれば、何も起こらない)
            p.set_state(ENTER_ACTIONS[cmd.type], True, self, reason="entered")
        elif cmd.type in CANCEL_ACTIONS:          # 体制を解除する(その体制でなければ、何も起こらない)
            p.set_state(CANCEL_ACTIONS[cmd.type], False, self, reason="cancelled")

    # --- スナップショット ---
    def terrain_rows(self) -> List[str]:
        return ["".join(self.terrain.kind(self.x0 + x, self.y0 + y) for x in range(self.size))
                for y in range(self.size)]

    def snapshot(self) -> dict:
        return {"region": list(self.rid), "planet": self.planet.id, "temperature_c": self.temperature_c,
                "origin": [self.x0, self.y0], "size": self.size,
                "terrain": self.terrain_rows(),
                "entities": [e.to_dict() for e in self.entities.values()]}
