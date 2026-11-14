"""クライアント側のローカル世界モデル。snapshot で初期化し、events を適用して追従する。"""
from collections import deque
from typing import Deque, Dict, Iterable, List, Optional, Tuple

XY = Tuple[int, int]

# 描画の優先順位(セルに複数いるとき、先頭ほど優先)
STATE_EVENTS = {"fell_asleep": "sleeping", "woke_up": "awake", "fainted": "unconscious"}
STATE_MESSAGES = {"fell_asleep": "You fell asleep.", "woke_up": "You woke up.", "fainted": "You fainted."}
PRIORITY = ["Player", "Fox", "Rabbit", "Egg", "Wheat", "Grass", "Seed"]


class WorldView:
    def __init__(self):
        self.region_size = 32
        self.tick = 0                       # 絶対 tick(世界の開始からのゲーム内の秒数)
        self.game_time: Optional[dict] = None   # サーバが返した、自分の居る Region の惑星の時計
        self.my_name = ""                   # 自分の名前(マップ上の表示は、その 1 文字目)
        self.show_help = False              # h キーで、マップ横の凡例(文字の説明)を出す
        self.time_format = "clock"          # "clock"(Day 3 14:05:30)/ "tick"(Tick 259,200)
        self.rows: Dict[Tuple[int, int], List[str]] = {}          # Region -> 地形の行
        self.entities: Dict[str, dict] = {}                       # id -> {species,name,pos,...}
        self.cells: Dict[XY, Dict[str, None]] = {}                # セル -> {entity id}
        self.by_species: Dict[str, Dict[str, None]] = {}
        self.player_id: Optional[str] = None
        self.me_id: Optional[str] = None
        self.alive = True
        self.stats = {"hydration": 0, "energy": 0, "health": 0}
        self.vitals = {"stamina": 100, "wakefulness": 100, "state": "awake", "resting": False}   # スタミナ・覚醒度(%)・状態
        # PC の知覚の範囲だけを返す view を使うとき: 範囲(自分のセル + 距離 view_r)の地形。None = 全体(snapshot)
        self.view_r: Optional[int] = None
        self.win: Optional[Dict[XY, str]] = None
        self.window_dirty = False                 # 自分が動いた・範囲が変わった → view を取り直す
        self.temps: Dict[Tuple[int, int], float] = {}             # Region -> 気温(℃。snapshot の値)
        self.log: Deque[str] = deque(maxlen=6)

    # --- 初期化 ---
    def load(self, snapshot: dict) -> None:
        self.tick = snapshot["tick"]
        self.region_size = snapshot["region_size"]
        self.rows.clear(); self.entities.clear(); self.cells.clear(); self.by_species.clear()
        for r in snapshot["regions"]:
            self.rows[tuple(r["region"])] = r["terrain"]
            if "temperature_c" in r:
                self.temps[tuple(r["region"])] = r["temperature_c"]
            for e in r["entities"]:
                self._add(e)
        me = self.entities.get(self.me_id) if self.me_id else None
        if me:
            self._set_stats(me)

    def load_view(self, v: dict, region_size: Optional[int] = None) -> None:
        """`GET /players/{id}/view` の結果を取り込む。範囲の外の地形・個体は持たない(サーバが返さない)。"""
        self.tick = v["tick"]
        self.game_time = v.get("game_time", self.game_time)
        if region_size:
            self.region_size = region_size
        self.view_r = v["r"]
        ox, oy = v["origin"]
        self.win = {(ox + i, oy + j): ch for j, row in enumerate(v["rows"]) for i, ch in enumerate(row)}
        self.entities.clear(); self.cells.clear(); self.by_species.clear()
        for e in v["entities"]:
            self._add(e)
        me = v["self"]
        self.player_id, self.me_id, self.alive = me.get("player_id", self.player_id), me["id"], True
        self._add(me)
        self._set_stats(me)
        self.temps[tuple(v["region"])] = v["temperature_c"]
        self.window_dirty = False

    def in_view(self, x: int, y: int) -> bool:
        """知覚の範囲の中か。(snapshot を使うときは、常に True)"""
        if self.view_r is None or self.me_pos is None:
            return True
        return max(abs(x - self.me_pos[0]), abs(y - self.me_pos[1])) <= self.view_r

    def toggle_help(self) -> bool:
        self.show_help = not self.show_help
        return self.show_help

    def toggle_time_format(self) -> str:
        self.time_format = "tick" if self.time_format == "clock" else "clock"
        return self.time_format

    def time_label(self) -> str:
        """ヘッダの時間表記。時刻表記(その Region の惑星の時計)か、tick 表記(絶対 tick)。"""
        if self.time_format == "tick" or not self.game_time:
            return f"Tick {self.tick:,}"
        return f"{self.game_time['text']} ({self.game_time['planet']})"

    def set_me(self, status: dict) -> None:
        """GET /players/{id} の結果から自分の状態を設定。"""
        self.tick = status.get("tick", self.tick)
        self.game_time = status.get("game_time", self.game_time)
        self.player_id = status["player_id"]
        self.alive = status["alive"]
        self.my_name = status.get("name", self.my_name)
        ent = status.get("entity")
        self.me_id = ent["id"] if ent else None
        if ent:
            self._set_stats(ent)

    def _set_stats(self, e: dict) -> None:
        self.stats = {k: int(e[k]) for k in ("hydration", "energy", "health")}
        wmax = e.get("wakefulness_max") or 0
        self.vitals = {"stamina": int(e.get("stamina", 100)),
                       "wakefulness": int(100 * e["wakefulness"] / wmax) if wmax and e.get("wakefulness") is not None else 100,
                       "state": e.get("state", "awake"), "resting": bool(e.get("resting", False))}

    def temperature(self) -> Optional[float]:
        """自分の居る Region の気温(snapshot の値)。"""
        if self.me_pos is None:
            return None
        return self.temps.get((self.me_pos[0] // self.region_size, self.me_pos[1] // self.region_size))

    # --- 地形 ---
    def kind(self, x: int, y: int) -> str:
        if self.win is not None:                  # view(知覚の範囲だけ)。範囲の外は空白
            return self.win.get((x, y), " ")
        rows = self.rows.get((x // self.region_size, y // self.region_size))
        if rows is None or x < 0 or y < 0:
            return " "
        return rows[y % self.region_size][x % self.region_size]

    def adjacent_water(self, p: XY) -> bool:
        return any(self.kind(p[0] + dx, p[1] + dy) == "~" for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)))

    def nearest_cell(self, p: XY, kind: str, radius: int) -> Optional[XY]:
        best, bd = None, 10 ** 9
        for y in range(p[1] - radius, p[1] + radius + 1):
            for x in range(p[0] - radius, p[0] + radius + 1):
                if self.kind(x, y) == kind:
                    d = abs(x - p[0]) + abs(y - p[1])
                    if d < bd:
                        best, bd = (x, y), d
        return best

    # --- Entity ---
    @property
    def me(self) -> Optional[dict]:
        return self.entities.get(self.me_id) if self.me_id else None

    @property
    def me_pos(self) -> Optional[XY]:
        m = self.me
        return m["pos"] if m else None

    def at(self, xy: XY) -> List[dict]:
        return [self.entities[i] for i in self.cells.get(xy, ())]

    def of_species(self, species: Iterable[str]) -> List[dict]:
        return [self.entities[i] for s in species for i in self.by_species.get(s, ())]

    def _add(self, e: dict) -> None:
        e = dict(e)
        e["pos"] = tuple(e["pos"])
        self.entities[e["id"]] = e
        self.cells.setdefault(e["pos"], {})[e["id"]] = None
        self.by_species.setdefault(e["species"], {})[e["id"]] = None

    def _remove(self, eid: str) -> Optional[dict]:
        e = self.entities.pop(eid, None)
        if e is None:
            return None
        c = self.cells.get(e["pos"])
        if c is not None:
            c.pop(eid, None)
            if not c:
                del self.cells[e["pos"]]
        self.by_species.get(e["species"], {}).pop(eid, None)
        return e

    def _move(self, eid: str, to: XY) -> None:
        e = self.entities.get(eid)
        if e is None:
            return
        c = self.cells.get(e["pos"])
        if c is not None:
            c.pop(eid, None)
            if not c:
                del self.cells[e["pos"]]
        e["pos"] = to
        self.cells.setdefault(to, {})[eid] = None

    # --- イベント適用 ---
    def apply(self, events: Iterable[dict], tick: Optional[int] = None, game_time: Optional[dict] = None) -> None:
        for ev in events:
            t = ev["type"]
            if t == "entity_spawned":
                e = ev["entity"]
                self._remove(e["id"])
                self._add(e)
                if e["species"] == "Player" and e.get("player_id") == self.player_id:
                    self.me_id, self.alive = e["id"], True
                    self._set_stats(e)
                    self.log.append("You spawned.")
            elif t == "entity_removed":
                gone = self._remove(ev["id"])
                if ev.get("by") and ev["by"] == self.me_id and gone:
                    self.log.append(f"You ate a {gone['species']}.")
                elif ev["id"] == self.me_id and ev.get("reason") == "death":
                    self.me_id = None
            elif t == "entity_moved":
                if ev["id"] not in self.entities and self.win is not None:       # 知覚の範囲に新しく入ってきた個体
                    self._add({"id": ev["id"], "species": ev.get("species", "?"), "name": ev.get("species", "?"),
                               "pos": ev["to"], "state": "awake"})
                self._move(ev["id"], tuple(ev["to"]))
                if ev["id"] == self.me_id:
                    self.window_dirty = True          # 自分が動いた → 範囲(窓)が動いたので、view を取り直す
            elif t == "player_renamed":
                if ev.get("player_id") == self.player_id:
                    self.my_name = ev["name"]
                e = self.entities.get(ev["id"])
                if e is not None:
                    e["name"] = ev["name"]
            elif t == "stats_changed":
                if ev.get("player_id") == self.player_id:
                    self.stats = {k: ev[k] for k in ("hydration", "energy", "health")}
                    self.vitals = {"stamina": ev.get("stamina", self.vitals["stamina"]),
                                   "wakefulness": ev.get("wakefulness_pct", self.vitals["wakefulness"]),
                                   "state": ev.get("state", self.vitals["state"]),
                                   "resting": ev.get("resting", self.vitals["resting"])}
            elif t in STATE_EVENTS:
                e = self.entities.get(ev["id"])
                if e is not None:
                    e["state"] = STATE_EVENTS[t]
                    e["resting"] = False
                if ev["id"] == self.me_id:
                    self.vitals["state"] = STATE_EVENTS[t]
                    self.log.append(STATE_MESSAGES[t])
            elif t in ("rest_started", "rest_ended"):
                e = self.entities.get(ev["id"])
                if e is not None:
                    e["resting"] = t == "rest_started"
                if ev["id"] == self.me_id:
                    self.vitals["resting"] = t == "rest_started"
                    self.log.append("You are resting." if t == "rest_started" else "You stopped resting.")
            elif t == "player_died":
                if ev.get("player_id") == self.player_id:
                    self.alive = False
                    self.log.append("You died. Press x to respawn.")
            elif t == "command_rejected":
                if ev.get("player_id") == self.player_id:
                    self.log.append(f"Rejected: {ev.get('reason')}")
        if tick is not None:
            self.tick = tick
        if game_time is not None:
            self.game_time = game_time

    def census(self) -> Dict[str, int]:
        """個体数(種ごと)。view(知覚の範囲だけ)のときは、範囲の中のものだけ数える。"""
        if self.view_r is None:
            return {s: len(d) for s, d in self.by_species.items() if d}
        out: Dict[str, int] = {}
        for e in self.entities.values():
            if self.in_view(*e["pos"]):
                out[e["species"]] = out.get(e["species"], 0) + 1
        return out
