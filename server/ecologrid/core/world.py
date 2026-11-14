"""World: Region の集合。tick の進行・コマンド受付・イベントログを担う。

Phase 1 はメモリ上のみ(再起動でリセット)。永続化は将来 RegionStore 越しに行う。
"""
from collections import Counter, OrderedDict, deque
from typing import Deque, Dict, List, Optional, Tuple

from . import factory
from .actions import ActionStore, InMemoryActionStore
from .config import WorldConfig, validate_config
from .grid import Pos, RegionId, region_of
from .messages import COMMAND_TYPES, Command
from .region import Region
from .plant import default_plant_actions
from .reproduction import default_scheduled_actions
from .sleep import default_sleep_actions
from .rng import make_rng, new_id
from .species import SPECIES
from .terrain import Terrain


class EventsExpired(Exception):
    """since が古すぎてログから落ちている。snapshot を取り直す必要がある。"""


class PlayerRecord:
    def __init__(self, player_id: str, name: str):
        self.player_id, self.name = player_id, name
        self.entity_id: Optional[str] = None
        self.region: Optional[RegionId] = None
        self.queue: Deque[Command] = deque()

    @property
    def alive(self) -> bool:
        return self.entity_id is not None


class World:
    def __init__(self, cfg: Optional[WorldConfig] = None, actions: Optional[ActionStore] = None):
        self.cfg = cfg or WorldConfig()
        self.config_warnings = validate_config(self.cfg)   # 不正な値は ValueError、推奨外は警告
        self.terrain = Terrain(self.cfg.seed, self.cfg.width, self.cfg.height)
        self.rng = make_rng(self.cfg.seed, "world")
        if actions is None:
            actions = InMemoryActionStore()
            for scope, act in default_scheduled_actions() + default_plant_actions() + default_sleep_actions():
                actions.put(scope, act)
        self.actions = actions
        self.regions: Dict[RegionId, Region] = {
            (rx, ry): Region((rx, ry), self.cfg, self.terrain, self.actions)
            for ry in range(self.cfg.regions_y) for rx in range(self.cfg.regions_x)}
        self.tick_no = 0
        self.action_cache_clears = 0
        self.players: Dict[str, PlayerRecord] = {}
        self._events: Deque[dict] = deque(maxlen=self.cfg.event_log_size)
        self._next_seq = 1
        self._seen: "OrderedDict[str, None]" = OrderedDict()

    # --- 初期化 ---
    def generate(self) -> None:
        """初期個体を Region の RNG で配置する(決定論的)。"""
        for rid in sorted(self.regions):
            r = self.regions[rid]
            for species, density in self.cfg.initial_density.items():
                n = int(len(r.land_cells) * density + r.rng.random())
                rd = SPECIES[species].repro
                for pos in r.rng.sample(r.land_cells, min(n, len(r.land_cells))):
                    # 初期個体は成体。誕生 tick を散らして、周期処理の実行タイミングを分散させる
                    sp = SPECIES[species]
                    if rd:
                        init = {"birth_tick": -(rd.min_age_ticks + r.rng.randrange(max(1, rd.check_interval_ticks)))}
                    elif sp.seed_dispersal_interval_ticks:   # 植物: 種子散布の位相を散らす
                        init = {"birth_tick": -r.rng.randrange(sp.seed_dispersal_interval_ticks)}
                    else:
                        init = None
                    r.spawn(species, pos, init=init)
        self._generate_counted()
        for r in self.regions.values():
            r.events.clear()   # 初期配置は snapshot で配るのでイベントにしない

    def _generate_counted(self) -> None:
        """動物など、世界全体の頭数(initial_counts)で配置する種。陸セルから一様に選ぶ(決定論的)。
        初期個体は成体で、誕生 tick を散らして周期処理の実行タイミングを分散させる。"""
        cells = [(rid, pos) for rid in sorted(self.regions) for pos in self.regions[rid].land_cells]
        for species, n in self.cfg.initial_counts.items():
            rng = make_rng(self.cfg.seed, "initial", species)
            rd = SPECIES[species].repro
            for rid, pos in rng.sample(cells, min(n, len(cells))):
                init = ({"birth_tick": -(rd.min_age_ticks + rng.randrange(max(1, rd.check_interval_ticks)))}
                        if rd else None)
                self.regions[rid].spawn(species, pos, init=init)

    # --- プレイヤー ---
    def name_taken(self, name: str, player_id: Optional[str] = None) -> bool:
        """他のプレイヤーが同じ名前を使っているか(完全一致)。"""
        return any(r.name == name and r.player_id != player_id for r in self.players.values())

    def rename_player(self, player_id: str, name: str) -> str:
        """名前を変更する(マップ上の表示はその 1 文字目)。他のプレイヤーが使用中なら ValueError("name_taken")。"""
        name = name.strip()
        if not name:
            raise ValueError("name_invalid")
        if self.name_taken(name, player_id):
            raise ValueError("name_taken")
        rec = self.players[player_id]
        rec.name = name
        ent = self.player_entity(player_id)
        if ent is not None:
            ent.entity_name = name
            self.regions[rec.region].emit("player_renamed", player_id=player_id, id=ent.entity_id, name=name,
                                          pos=[ent.grid_position.x, ent.grid_position.y])
            self._collect_events()
        return name

    def add_player(self, name: str) -> str:
        if self.name_taken(name):
            raise ValueError("name_taken")
        pid = new_id(self.rng)
        rec = PlayerRecord(pid, name)
        self.players[pid] = rec
        self._spawn_player(rec)
        return pid

    def remove_player(self, player_id: str) -> None:
        rec = self.players.pop(player_id, None)
        if rec and rec.alive:
            r = self.regions[rec.region]
            r.remove(r.entities[rec.entity_id], "left")
            self._collect_events()

    def _spawn_player(self, rec: PlayerRecord) -> None:
        rids = sorted(self.regions)
        for _ in range(500):
            r = self.regions[self.rng.choice(rids)]
            if not r.land_cells:
                continue
            pos = self.rng.choice(r.land_cells)
            e = r.spawn("Player", pos, name=rec.name, player_id=rec.player_id)
            rec.entity_id, rec.region = e.entity_id, r.rid
            return
        raise RuntimeError("no land to spawn player")

    def player_entity(self, player_id: str):
        rec = self.players.get(player_id)
        if rec and rec.alive:
            return self.regions[rec.region].entities.get(rec.entity_id)
        return None

    # --- コマンド ---
    def submit(self, cmd: Command) -> Tuple[bool, str]:
        """コマンドをキューに積む。戻り値 (受理, 理由)。同じ command_id は 'duplicate'。"""
        rec = self.players.get(cmd.player_id)
        if rec is None:
            return False, "unknown_player"
        if cmd.command_id in self._seen:
            return False, "duplicate"
        if cmd.type not in COMMAND_TYPES:
            return False, "unknown_command"
        if len(rec.queue) >= self.cfg.command_queue_max:
            return False, "queue_full"
        self._seen[cmd.command_id] = None
        while len(self._seen) > self.cfg.command_dedup_size:
            self._seen.popitem(last=False)
        rec.queue.append(cmd)
        return True, "ok"

    # --- tick ---
    def tick(self) -> None:
        self.tick_no += 1
        per_region: Dict[RegionId, List[Tuple[str, Command]]] = {}
        for pid in sorted(self.players):
            rec = self.players[pid]
            if not rec.queue:
                continue
            cmd = rec.queue.popleft()
            if cmd.type == "respawn":
                if not rec.alive:
                    self._spawn_player(rec)
                continue
            if rec.alive:
                per_region.setdefault(rec.region, []).append((rec.entity_id, cmd))
        for rid in sorted(self.regions):
            self.regions[rid].tick(per_region.get(rid, []), self.tick_no)
        self._deliver()
        self._update_players()
        self._collect_events()

    def _deliver(self) -> None:
        """Region 間メッセージ(越境・種子)を配送し、移動先で即座に取り込む。

        取り込みを次 tick に持ち越すと、tick の合間(API の snapshot/status が処理される瞬間)に
        越境中の Entity がどの Region にも存在せず、snapshot から欠けたり Player が死亡扱いになる。
        そのため tick の最後までに登録を済ませる(次 tick から通常どおり tick される)。
        """
        for rid in sorted(self.regions):
            src = self.regions[rid]
            out, src.outbox = src.outbox, []
            for dst, msg in out:
                if dst not in self.regions:
                    continue   # 世界の外へ出る移動は walkable で弾かれるはずだが念のため
                self.regions[dst].inbox.append(msg)
                if msg["type"] == "handoff":
                    ent = msg["entity"]
                    rec = self.players.get(ent.get("player_id", ""))
                    if rec and rec.entity_id == ent["id"]:
                        rec.region = dst
        for rid in sorted(self.regions):
            self.regions[rid].process_inbox()

    def _update_players(self) -> None:
        for rec in self.players.values():
            if rec.alive and rec.entity_id not in self.regions[rec.region].entities:
                rec.entity_id = None   # 死亡(越境中の Entity は _deliver で登録済み)

    def _collect_events(self) -> None:
        for rid in sorted(self.regions):
            r = self.regions[rid]
            for ev in r.events:
                ev["seq"], ev["tick"] = self._next_seq, self.tick_no
                self._next_seq += 1
                self._events.append(ev)
            r.events = []

    # --- アクション計画のキャッシュ ---
    def clear_action_cache(self, rebuild_wheels: bool = False) -> dict:
        """全 Region のアクション計画のキャッシュを捨てる(管理 API から呼ぶ)。次の tick で作り直す。"""
        for r in self.regions.values():
            r.clear_action_cache(rebuild_wheels)
        self.action_cache_clears += 1
        return self.action_cache_status()

    def action_cache_status(self) -> dict:
        return {"store_version": getattr(self.actions, "version", None),
                "regions": len(self.regions),
                "cached_regions": sum(1 for r in self.regions.values() if r._plan_cache is not None),
                "plan_builds": sum(r.plan_builds for r in self.regions.values()),
                "clears": self.action_cache_clears}

    # --- PC の知覚の範囲だけを返す(view・イベント)。docs/spec/animal-memory-spec.md §10 ---
    PUBLIC_KEYS = ("id", "name", "species", "pos", "state", "player_id")

    def player_window(self, pid: str):
        """(エンティティ, Region, 中心 Pos, 有効な範囲 r)。死亡していれば None。"""
        rec = self.players.get(pid)
        ent = self.player_entity(pid)
        if ent is None:
            return None
        region = self.regions[rec.region]
        return ent, region, ent.grid_position, ent.effective_range(region)

    def player_view(self, pid: str) -> Optional[dict]:
        """自分のセル + 距離 r(チェビシェフ距離)までの、現在の地形と個体。範囲の外は含めない。r = 0 なら自分のセルだけ。"""
        win = self.player_window(pid)
        if win is None:
            return None
        ent, region, c, r = win
        rows = ["".join(self.terrain.kind(x, y) for x in range(c.x - r, c.x + r + 1)) for y in range(c.y - r, c.y + r + 1)]
        size = self.cfg.region_size
        entities = []
        for ry in range(max(0, (c.y - r) // size), min(self.cfg.regions_y - 1, (c.y + r) // size) + 1):
            for rx in range(max(0, (c.x - r) // size), min(self.cfg.regions_x - 1, (c.x + r) // size) + 1):
                for e in self.regions[(rx, ry)].entities_in_range(c, r):
                    if e is not ent:
                        entities.append(e.to_public())
        entities.sort(key=lambda d: (d["pos"][1], d["pos"][0], d["id"]))
        return {"tick": self.tick_no, "event_seq": self.last_seq, "center": [c.x, c.y], "r": r,
                "perception_range": ent.perception_range, "region": list(region.rid),
                "temperature_c": region.temperature_c, "origin": [c.x - r, c.y - r], "rows": rows,
                "entities": entities, "self": ent.to_dict()}

    def visible_events(self, pid: str, evs: List[dict]) -> List[dict]:
        """イベントのうち、本人に関するものと、有効な知覚の範囲内のものだけ。他の個体の内部状態は、公開項目だけにする。"""
        win = self.player_window(pid)
        if win is None:
            return [e for e in evs if e.get("player_id") == pid]
        ent, _, c, r = win

        def inside(pos) -> bool:
            return pos is not None and max(abs(pos[0] - c.x), abs(pos[1] - c.y)) <= r

        out = []
        for ev in evs:
            t = ev["type"]
            if ev.get("player_id") == pid or ev.get("id") == ent.entity_id or (
                    t == "entity_spawned" and ev["entity"].get("player_id") == pid):
                out.append(ev)
                continue
            if t == "entity_spawned":
                ok = inside(ev["entity"].get("pos"))
            elif t == "entity_moved":
                ok = inside(ev.get("frm")) or inside(ev.get("to"))
            elif t == "seed_germinated":
                ok = inside(ev.get("pos"))
            else:
                ok = inside(ev.get("pos"))
            if not ok:
                continue
            if t in ("entity_spawned", "seed_germinated"):
                ev = dict(ev)
                key = "entity" if t == "entity_spawned" else "plant"
                ev[key] = {k: v for k, v in ev[key].items() if k in self.PUBLIC_KEYS}
            out.append(ev)
        return out

    def find_entity(self, entity_id: str):
        for r in self.regions.values():
            e = r.entities.get(entity_id)
            if e is not None:
                return e
        return None

    # --- 記憶の統計(管理用) ---
    def memory_stats(self) -> dict:
        animals = [e for r in self.regions.values() for e in r.entities.values() if hasattr(e, "memory")]
        per = [e.memory.stats() for e in animals]
        maps = [e.map.stats() for e in animals if e.map is not None]
        return {"individuals": len(animals),
                "log_entries": sum(s["log"] for s in per), "long_entries": sum(s["long"] for s in per),
                "max_log": max((s["log"] for s in per), default=0), "max_long": max((s["long"] for s in per), default=0),
                "organized": sum(s["organized"] for s in per),
                "memory_bytes_estimate": sum(s["bytes_estimate"] for s in per),
                "map_bytes_estimate": sum(m["bytes"] for m in maps),
                "known_cells": sum(m["known_cells"] for m in maps)}

    # --- 参照 ---
    @property
    def absolute_tick(self) -> int:
        """世界の絶対 tick(同じ世界のすべてのサーバで同じ値。単一サーバでは tick_no)。"""
        return self.tick_no

    def game_time(self, rid=None):
        """Region の惑星の時計(rid 省略時は earth)。"""
        from .clock import game_time
        from .planets import EARTH
        planet = self.cfg.planet_of(rid) if rid is not None else EARTH
        return game_time(self.tick_no, planet)

    @property
    def last_seq(self) -> int:
        return self._next_seq - 1

    def events_since(self, since: int, region: Optional[RegionId] = None) -> Tuple[List[dict], int]:
        """seq > since のイベントと最新 seq。古すぎれば EventsExpired。"""
        if self._events and since < self._events[0]["seq"] - 1:
            raise EventsExpired(since)
        out = [e for e in self._events if e["seq"] > since
               and (region is None or tuple(region) in [tuple(x) for x in e["regions"]])]
        return out, self.last_seq

    def snapshot(self, region: Optional[RegionId] = None) -> dict:
        rids = [region] if region else sorted(self.regions)
        return {"tick": self.tick_no, "event_seq": self.last_seq,
                "seed": self.cfg.seed, "region_size": self.cfg.region_size,
                "regions": [self.regions[tuple(r)].snapshot() for r in rids]}

    def census(self) -> Dict[str, int]:
        c: Counter = Counter()
        for r in self.regions.values():
            for sp, d in r._by_species.items():
                c[sp] += len(d)
        return dict(c)
