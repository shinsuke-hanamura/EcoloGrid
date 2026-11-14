"""サーバ設定。環境変数 ECOLOGRID_* から読む(未設定なら既定値)。"""
import os
from dataclasses import dataclass
from typing import Optional


def _env(name: str, default, cast=str):
    v = os.environ.get(f"ECOLOGRID_{name}")
    return default if v is None else cast(v)


@dataclass
class Settings:
    seed: int = 1
    regions_x: int = 4
    regions_y: int = 4
    region_size: int = 32                 # 1 Region の一辺(セル)。世界の大きさ = region_size × regions_x(y)
    tick_interval_ms: int = 50            # 1 tick の長さ(実時間)。サーバごとに調整。ゲームの進む速さはこれだけで決まる
    autotick: bool = True                 # False: tick ループを起動しない(テスト用)
    max_players: int = 100
    player_idle_timeout_sec: float = 300.0   # この間 API に触れないプレイヤーは退出扱い
    max_poll_wait_sec: float = 30.0       # events の wait 上限
    join_key: Optional[str] = None        # 設定時、POST /players に X-Join-Key を要求
    player_sleep_enabled: bool = True     # False: Player は睡眠・意識消失しない
    admin_key: Optional[str] = None       # 設定時のみ管理 API(/admin/...)が有効。X-Admin-Key ヘッダで認証(未設定なら常に 403)

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            seed=_env("SEED", d.seed, int), regions_x=_env("REGIONS_X", d.regions_x, int),
            regions_y=_env("REGIONS_Y", d.regions_y, int),
            region_size=_env("REGION_SIZE", d.region_size, int), tick_interval_ms=_env("TICK_INTERVAL_MS", d.tick_interval_ms, int),
            autotick=_env("AUTOTICK", d.autotick, lambda s: s.lower() not in ("0", "false", "no")),
            max_players=_env("MAX_PLAYERS", d.max_players, int),
            player_idle_timeout_sec=_env("IDLE_TIMEOUT", d.player_idle_timeout_sec, float),
            max_poll_wait_sec=_env("MAX_POLL_WAIT", d.max_poll_wait_sec, float),
            join_key=_env("JOIN_KEY", None) or None,
            admin_key=_env("ADMIN_KEY", None) or None,
            player_sleep_enabled=_env("PLAYER_SLEEP", d.player_sleep_enabled,
                                      lambda s: s.lower() not in ("0", "false", "no")))
