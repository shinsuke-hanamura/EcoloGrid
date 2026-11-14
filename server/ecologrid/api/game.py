"""GameServer: World を保持し、固定間隔で tick を進める。API 層とコアの接点。

スレッド方針: World に触るのは asyncio のイベントループ上だけ(ハンドラはすべて async def)。
tick もループ内で同期実行するので排他制御は不要。クライアントのコマンドは World.submit で
キューに積まれ、次の tick で処理される。
"""
import asyncio
import gc
import hashlib
import logging
import secrets
import time
import uuid
from typing import Dict, List, Optional, Tuple

from ecologrid.core.config import WorldConfig
from ecologrid.core.messages import Command
from ecologrid.core.world import World

from .settings import Settings

log = logging.getLogger("ecologrid")

GC_FULL_INTERVAL_TICKS = 100_000    # 世代 2 の完全収集を、tick の最中ではなく、この間隔で手動実行する


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class GameServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.world = World(WorldConfig(seed=settings.seed, regions_x=settings.regions_x,
                                       regions_y=settings.regions_y, region_size=settings.region_size,
                                       tick_interval_ms=settings.tick_interval_ms,
                                       player_sleep_enabled=settings.player_sleep_enabled))
        self.world.generate()
        self.world_id = str(uuid.uuid4())     # 起動ごとに変わる。再起動(世界リセット)の検出用
        self._tokens: Dict[str, str] = {}     # sha256(token) -> player_id
        self._last_seen: Dict[str, float] = {}
        self._tick_event: Optional[asyncio.Event] = None
        self.tick_errors = 0
        self.behind_count = 0
        self._gc_managed = False

    # --- プレイヤー/認証 ---
    @property
    def full(self) -> bool:
        return len(self.world.players) >= self.settings.max_players

    def join(self, name: str) -> Tuple[str, str]:
        pid = self.world.add_player(name)              # 名前が使用中なら ValueError("name_taken")
        token = secrets.token_urlsafe(32)
        self._tokens[_hash(token)] = pid
        self._last_seen[pid] = time.monotonic()
        return pid, token

    def authenticate(self, token: str) -> Optional[str]:
        pid = self._tokens.get(_hash(token))
        if pid is not None:
            self._last_seen[pid] = time.monotonic()
        return pid

    def leave(self, pid: str) -> None:
        self.world.remove_player(pid)
        self._last_seen.pop(pid, None)
        for h in [h for h, p in self._tokens.items() if p == pid]:
            del self._tokens[h]

    def game_time_for(self, pid: Optional[str] = None) -> dict:
        """プレイヤーが居る Region の惑星の時計(プレイヤーが居なければ earth)。"""
        rec = self.world.players.get(pid) if pid else None
        return self.world.game_time(rec.region if rec and rec.region else None).to_dict()

    def submit(self, cmd: Command) -> Tuple[bool, str]:
        return self.world.submit(cmd)

    def _reap_idle(self) -> None:
        limit = time.monotonic() - self.settings.player_idle_timeout_sec
        for pid in [p for p, t in self._last_seen.items() if t < limit]:
            log.info("player %s timed out", pid)
            self.leave(pid)

    # --- tick ---
    def step(self) -> None:
        try:
            self.world.tick()
            self._reap_idle()
            if self._gc_managed and self.world.tick_no % GC_FULL_INTERVAL_TICKS == 0:
                gc.collect()
                gc.freeze()      # 生き残ったオブジェクトを以後の完全収集の対象から外す
        except Exception:
            self.tick_errors += 1
            log.exception("tick failed")
        ev, self._tick_event = self._tick_event, asyncio.Event()
        if ev is not None:
            ev.set()          # ロングポーリング待機中のリクエストを起こす

    async def run(self) -> None:
        """固定間隔ループ。処理が間に合わない場合は追いつき実行せず、基準時刻を現在に合わせる。"""
        loop = asyncio.get_running_loop()
        self._tick_event = asyncio.Event()
        dt = self.settings.tick_interval_ms / 1000.0
        # GC の制御: 完全収集(世代 2)は数十 ms かかることがあり、tick の予算(標準 50 ms)に響く。
        # 生成済みのオブジェクトを凍結し、世代 2 の自動収集を実質止めて、手動で低頻度に実行する。
        old_threshold = gc.get_threshold()
        gc.collect()
        gc.freeze()
        gc.set_threshold(old_threshold[0], old_threshold[1], 1_000_000)
        self._gc_managed = True
        nxt = loop.time()
        try:
            while True:
                self.step()
                nxt += dt
                delay = nxt - loop.time()
                if delay < 0:
                    self.behind_count += 1
                    nxt = loop.time()
                    delay = 0
                await asyncio.sleep(delay)
        finally:
            self._gc_managed = False
            gc.unfreeze()
            gc.set_threshold(*old_threshold)

    async def wait_next_tick(self, timeout: float) -> None:
        if self._tick_event is None:
            self._tick_event = asyncio.Event()
        try:
            await asyncio.wait_for(self._tick_event.wait(), timeout)
        except asyncio.TimeoutError:
            pass
