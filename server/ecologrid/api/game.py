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

from ecologrid.core.config import WorldConfig, check_tick_interval
from ecologrid.core.messages import Command
from ecologrid.core.world import World

from .settings import Settings

log = logging.getLogger("ecologrid")

GC_FULL_INTERVAL_TICKS = 100_000    # 世代 2 の完全収集を、tick の最中ではなく、この間隔で手動実行する
FAST_YIELD_SEC = 0.005              # 最速(tick_interval_ms = 0)のとき、実時間でこの間隔ごとに、イベントループへ制御を返す
NOTIFY_MIN_SEC = 0.020              # tick_interval_ms がこれ(20 ms)未満のとき、ロングポーリングを起こす最短の間隔
TICK_CHANGE_MIN_SEC = 1.0           # tick_interval_ms の変更の最短の間隔(全プレイヤーを通じて)


class TickIntervalError(Exception):
    """tick_interval_ms を変更できない。code: tick_interval_invalid(422)/ tick_interval_forbidden(403)/ tick_interval_rate_limited(429)。"""
    def __init__(self, code: str, detail: str = "", retry_after: float = 0.0):
        super().__init__(detail or code)
        self.code, self.retry_after = code, retry_after


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
        self._rebase = False                  # tick_interval_ms が変わった(ループの基準の時刻を合わせ直す)
        self._tick_change_at = -1e9           # 直近の変更(time.monotonic())。全プレイヤーを通じて 1 秒に 1 回まで
        self._last_notify = 0.0               # 直近にロングポーリングを起こした時刻(loop.time())

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

    # --- 進行速度(tick_interval_ms)の変更 ---
    @property
    def tick_interval_ms(self) -> int:
        return self.world.cfg.tick_interval_ms

    def can_change_tick_interval(self, pid: str) -> bool:
        """tick_interval_ms を変更できるか。今は、全プレイヤーに許可。将来は、プレイヤーの合意・管理者の変更に差し替える。"""
        return pid in self.world.players

    def set_tick_interval(self, pid: str, ms) -> Tuple[int, int]:
        """プレイヤー pid が tick_interval_ms を変更する。戻り値 (前の値, 新しい値)。変更は全員にイベントで通知する。"""
        err = check_tick_interval(ms)
        if err:
            raise TickIntervalError("tick_interval_invalid", err)
        if not self.can_change_tick_interval(pid):
            raise TickIntervalError("tick_interval_forbidden", "not allowed to change the tick interval")
        now = time.monotonic()
        wait = TICK_CHANGE_MIN_SEC - (now - self._tick_change_at)
        if wait > 0:
            raise TickIntervalError("tick_interval_rate_limited", "changed too recently", retry_after=wait)
        prev = self.tick_interval_ms
        self._tick_change_at = now
        if ms == prev:
            return prev, ms
        self.world.cfg.tick_interval_ms = ms
        self._rebase = True
        self.world.emit_global("tick_interval_changed", player_id=pid, name=self.world.players[pid].name,
                               previous=prev, tick_interval_ms=ms)
        self._notify()                                      # 待機中のクライアントを、すぐ起こす
        return prev, ms

    def _notify(self) -> None:
        """ロングポーリング中のリクエストを起こす。"""
        ev, self._tick_event = self._tick_event, asyncio.Event()
        if ev is not None:
            ev.set()
        try:
            self._last_notify = asyncio.get_running_loop().time()
        except RuntimeError:
            pass

    def _reap_idle(self) -> None:
        limit = time.monotonic() - self.settings.player_idle_timeout_sec
        for pid in [p for p, t in self._last_seen.items() if t < limit]:
            log.info("player %s timed out", pid)
            self.leave(pid)

    # --- tick ---
    def step(self, notify: bool = True) -> None:
        try:
            self.world.tick()
            self._reap_idle()
            if self._gc_managed and self.world.tick_no % GC_FULL_INTERVAL_TICKS == 0:
                gc.collect()
                gc.freeze()      # 生き残ったオブジェクトを以後の完全収集の対象から外す
        except Exception:
            self.tick_errors += 1
            log.exception("tick failed")
        if notify:
            self._notify()    # ロングポーリング待機中のリクエストを起こす

    async def run(self) -> None:
        """固定間隔ループ。処理が間に合わない場合は追いつき実行せず、基準時刻を現在に合わせる。"""
        loop = asyncio.get_running_loop()
        self._tick_event = asyncio.Event()
        # GC の制御: 完全収集(世代 2)は数十 ms かかることがあり、tick の予算(標準 50 ms)に響く。
        # 生成済みのオブジェクトを凍結し、世代 2 の自動収集を実質止めて、手動で低頻度に実行する。
        old_threshold = gc.get_threshold()
        gc.collect()
        gc.freeze()
        gc.set_threshold(old_threshold[0], old_threshold[1], 1_000_000)
        self._gc_managed = True
        nxt = loop.time()
        slice_start = time.perf_counter()
        try:
            while True:
                dt_ms = self.world.cfg.tick_interval_ms
                if self._rebase:                       # 速さが変わった: 基準の時刻を現在に合わせ直す
                    self._rebase = False
                    nxt = loop.time()
                # 短い間隔(20 ms 未満)では、ロングポーリングを起こすのを、実時間で 20 ms に 1 回にまとめる
                self.step(notify=dt_ms >= NOTIFY_MIN_SEC * 1000 or loop.time() - self._last_notify >= NOTIFY_MIN_SEC)
                if dt_ms == 0:
                    # 最速: 待たない。実時間で約 5 ms ごとに、1 回だけ制御を返す(HTTP リクエストを処理するため)
                    nxt = loop.time()
                    if time.perf_counter() - slice_start >= FAST_YIELD_SEC:
                        await asyncio.sleep(0)
                        slice_start = time.perf_counter()
                    continue
                nxt += dt_ms / 1000.0
                delay = nxt - loop.time()
                if delay < 0:
                    self.behind_count += 1
                    nxt = loop.time()
                    delay = 0
                await asyncio.sleep(delay)
                slice_start = time.perf_counter()
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
