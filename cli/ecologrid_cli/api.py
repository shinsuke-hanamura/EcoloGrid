"""Ecologrid WebAPI の薄いクライアント。サーバのコードは import せず HTTP のみで通信する。"""
import uuid
from typing import Any, Dict, Optional

import httpx

PREFIX = "/api/v1"


class ApiError(Exception):
    def __init__(self, status: int, detail: Any):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail

    @property
    def code(self) -> Optional[str]:
        """410 などの detail.code(world_reset / events_expired)。"""
        return self.detail.get("code") if isinstance(self.detail, dict) else None


class ApiClient:
    def __init__(self, base_url: str = "http://localhost:8000", http: Optional[httpx.Client] = None,
                 join_key: Optional[str] = None, timeout: float = 45.0):
        # http を渡せばテスト(TestClient)や共有コネクションに使える
        self.http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.join_key = join_key
        self.token: Optional[str] = None
        self.player_id: Optional[str] = None

    def _req(self, method: str, path: str, auth: bool = True, **kw) -> Any:
        headers = dict(kw.pop("headers", {}))
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        r = self.http.request(method, PREFIX + path, headers=headers, **kw)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise ApiError(r.status_code, detail)
        return r.json() if r.content else None

    # --- 認証不要 ---
    def health(self) -> dict:
        return self._req("GET", "/healthz", auth=False)

    def config(self) -> dict:
        return self._req("GET", "/config", auth=False)

    def join(self, name: str) -> dict:
        """名前が使用中なら ApiError(409, code=name_taken)。"""
        headers = {"X-Join-Key": self.join_key} if self.join_key else {}
        j = self._req("POST", "/players", auth=False, json={"name": name}, headers=headers)
        self.token, self.player_id = j["token"], j["player_id"]
        return j

    # --- 認証必須 ---
    def status(self) -> dict:
        return self._req("GET", f"/players/{self.player_id}")

    def leave(self) -> None:
        self._req("DELETE", f"/players/{self.player_id}")
        self.token = None

    def command(self, type_: str, args: Optional[Dict] = None, command_id: Optional[str] = None) -> dict:
        body = {"command_id": command_id or uuid.uuid4().hex, "type": type_, "args": args or {}}
        return self._req("POST", f"/players/{self.player_id}/commands", json=body)

    def rename(self, name: str) -> dict:
        """名前を変更。使用中なら ApiError(409, code=name_taken)。"""
        return self._req("PUT", f"/players/{self.player_id}/name", json={"name": name})

    def view(self) -> dict:
        """自分のセル + 有効な知覚の範囲までの、現在の地形・個体(範囲の外は返らない)。"""
        return self._req("GET", f"/players/{self.player_id}/view")

    def events(self, since: int, wait: float = 0) -> dict:
        """自分に関するイベントと、有効な知覚の範囲内のイベントだけ。"""
        return self._req("GET", f"/players/{self.player_id}/events", params={"since": since, "wait": wait})

    def memory(self, **params) -> dict:
        return self._req("GET", f"/players/{self.player_id}/memory", params=params)

    def map(self, **params) -> dict:
        return self._req("GET", f"/players/{self.player_id}/map", params=params)

    def advice(self) -> dict:
        return self._req("GET", f"/players/{self.player_id}/advice")

    # --- 観戦・管理用(X-Admin-Key が必要。PC の通常の遊びでは使わない) ---
    def snapshot(self, admin_key: str, region: Optional[str] = None) -> dict:
        return self._req("GET", "/world/snapshot", params={"region": region} if region else None,
                         headers={"X-Admin-Key": admin_key})
