"""Session: 参加 → view(知覚の範囲だけ)→ events 追従の一連の流れと、切断・リセットからの復帰。"""
from typing import Callable, Optional

from .api import ApiClient, ApiError
from .view import WorldView


class Session:
    def __init__(self, api: ApiClient, name: str, on_info: Optional[Callable[[str], None]] = None):
        self.api, self.name = api, name
        self.view = WorldView()
        self.config: dict = {}
        self.next_seq = 0
        self.world_id: Optional[str] = None
        self.reconnects = 0
        self._info = on_info or (lambda m: None)

    @property
    def tick_interval_ms(self) -> int:
        return self.config.get("tick", {}).get("tick_interval_ms", 50)

    def step_seconds(self, species: str = "Player") -> float:
        """species が 1 歩進むのにかかる実時間(秒)= move_interval_ticks × 1 tick の長さ。"""
        n = self.config.get("species", {}).get(species, {}).get("move_interval_ticks", 5)
        return max(1, n) * self.tick_interval_ms / 1000.0

    # --- 開始・終了 ---
    def start(self) -> None:
        """設定値を 1 回取得し、参加して、世界の全量を取り込む。"""
        self.config = self.api.config()
        self.world_id = self.config["world_id"]
        self.api.join(self.name[: self.config["limits"]["name_max_length"]])   # 名前が使用中なら ApiError(409 name_taken)
        self.view = WorldView()
        self.view.player_id = self.api.player_id
        self._sync_state()

    def _sync_state(self) -> None:
        """自分の status と、知覚の範囲の view を取り込む(世界全体の snapshot は、PC には返らない)。"""
        status = self.api.status()
        self.view.set_me(status)
        try:
            v = self.api.view()
        except ApiError as e:
            if e.status != 409:        # 死亡中は view が無い(status の alive = False を使う)
                raise
            return
        self.view.load_view(v, self.config.get("world", {}).get("region_size"))
        self.world_id = v["world_id"]
        self.next_seq = v["event_seq"]

    def close(self) -> None:
        try:
            self.api.leave()
        except Exception:
            pass

    # --- 復帰 ---
    def _rejoin(self) -> None:
        self.reconnects += 1
        self._info("Reconnecting (server restarted or timed out)...")
        try:
            self.start()
        except ApiError as e:
            if e.code != "name_taken":
                raise
            base = self.name[: self.config["limits"]["name_max_length"] - 3]      # 名前が使われていたら、末尾に数字を付ける
            for i in range(2, 100):
                self.name = f"{base}{i}"
                try:
                    self.start()
                    break
                except ApiError as e2:
                    if e2.code != "name_taken":
                        raise
            self._info(f"Name was taken; you are now {self.name!r} (change with c).")

    def change_name(self, name: str) -> None:
        """名前を変更(サーバで重複チェック。使用中なら ApiError(409 name_taken))。成功したら保持する(再参加でも使う)。"""
        name = name.strip()[: self.config["limits"]["name_max_length"]]
        self._guard(lambda: self.api.rename(name))
        self.name = self.view.my_name = name

    def _guard(self, fn):
        """401(サーバ再起動/タイムアウトで退出済み)は再参加して 1 回だけやり直す。"""
        try:
            return fn()
        except ApiError as e:
            if e.status == 401:
                self._rejoin()
                return fn()
            raise

    # --- 進行 ---
    def poll(self, wait: float = 0.0) -> int:
        """events を取り込む。戻り値は適用したイベント数。410 は snapshot を取り直す。"""
        try:
            j = self._guard(lambda: self.api.events(self.next_seq, wait=wait))
        except ApiError as e:
            if e.status == 410:
                self._info(f"Resync ({e.code})")
                self._guard(self._sync_state)
                return 0
            raise
        if j["world_id"] != self.world_id:
            self._guard(self._sync_state)
            return 0
        self.view.apply(j["events"], tick=j["tick"], game_time=j.get("game_time"))
        self.next_seq = j["next_seq"]
        if self.view.window_dirty and self.view.alive:           # 自分が動いたので、新しい範囲の view を取り直す
            self._reload_view()
        return len(j["events"])

    def _reload_view(self) -> None:
        try:
            self.view.load_view(self._guard(self.api.view), self.config.get("world", {}).get("region_size"))
        except ApiError as e:
            if e.status != 409:
                raise
            self.view.alive = False

    def send(self, type_: str, **args) -> str:
        """コマンド送信。戻り値: queued / duplicate / busy(429) / dead(409)。"""
        try:
            r = self._guard(lambda: self.api.command(type_, args))
            return r["status"]
        except ApiError as e:
            if e.status == 429:
                return "busy"
            if e.status == 409:
                self.view.alive = False
                return "dead"
            raise

    def refresh_status(self) -> None:
        """状態の突き合わせ(生死・体力・知覚の範囲のずれ防止)。view を取り直す。"""
        self.view.set_me(self._guard(self.api.status))
        if self.view.alive:
            self._reload_view()
