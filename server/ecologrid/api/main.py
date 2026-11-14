"""Ecologrid WebAPI(FastAPI)。ゲームエンジンは core/、ここは薄いアダプタ。"""
import asyncio
import contextlib
import logging
import re
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ecologrid.core.clock import TICKS_PER_HOUR, TICKS_PER_MINUTE, TICKS_PER_SECOND
from ecologrid.core.grid import DIRS
from ecologrid.core.messages import COMMAND_TYPES, Command
from ecologrid.core.planets import PLANETS
from ecologrid.core.species import SPECIES
from ecologrid.core.terrain import LAND, ROCK, WATER
from ecologrid.core.world import EventsExpired

from . import memory_views, schemas
from .game import GameServer
from .settings import Settings

PREFIX = "/api/v1"
_bearer = HTTPBearer(auto_error=False)


def get_game(request: Request) -> GameServer:
    return request.app.state.game


def current_player(game: GameServer = Depends(get_game),
                   cred: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> str:
    """Bearer token からプレイヤー ID を引く(認証方式を差し替える場合はここだけ変える)。"""
    pid = game.authenticate(cred.credentials) if cred else None
    if pid is None:
        raise HTTPException(401, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"})
    return pid


def _parse_region(game: GameServer, region: Optional[str]):
    if region is None:
        return None
    if not re.fullmatch(r"\d+,\d+", region):
        raise HTTPException(422, "region must be 'x,y'")
    rid = tuple(int(v) for v in region.split(","))
    if rid not in game.world.regions:
        raise HTTPException(404, "no such region")
    return rid


log = logging.getLogger("ecologrid")
router = APIRouter(prefix=PREFIX)


def require_admin(game: GameServer = Depends(get_game), x_admin_key: Optional[str] = Header(None)) -> None:
    """管理者権限。サーバに ECOLOGRID_ADMIN_KEY が設定されていて、X-Admin-Key が一致するときだけ通す。
    未設定なら管理 API は無効(403)。プレイヤーのトークン(Bearer)では通らない。"""
    key = game.settings.admin_key
    if not key:
        raise HTTPException(403, "admin api is disabled")
    if not x_admin_key or not secrets.compare_digest(x_admin_key.encode(), key.encode()):
        raise HTTPException(403, "admin key required")


@router.get("/healthz", response_model=schemas.Health)
async def healthz(game: GameServer = Depends(get_game)):
    return {"status": "ok", "tick": game.world.absolute_tick, "game_time": game.game_time_for(),
            "players": len(game.world.players), "tick_errors": game.tick_errors}


@router.get("/config", response_model=schemas.SystemConfig)
async def system_config(game: GameServer = Depends(get_game)):
    """システム設定値。クライアントは起動時に 1 回取得してキャッシュする(認証不要)。
    サーバ再起動で変わりうるので、`world_id` が変わった(410 world_reset など)ときに取り直す。"""
    st, cfg = game.settings, game.world.cfg
    return {
        "api_version": "v1", "world_id": game.world_id,
        "join_key_required": bool(st.join_key),
        "tick": {"tick_interval_ms": cfg.tick_interval_ms},
        "time_units": {"ticks_per_second": TICKS_PER_SECOND, "ticks_per_minute": TICKS_PER_MINUTE,
                       "ticks_per_hour": TICKS_PER_HOUR},
        "planets": {p.id: {"name": p.name, "day_length_ticks": p.day_length_ticks,
                           "start_offset_ticks": p.start_offset_ticks, "gravity": p.gravity,
                           "temperature_c": p.temperature_c,
                           "temperature_amplitude_c": p.temperature_amplitude_c}
                    for p in PLANETS.values()},
        "species": {k: {f: getattr(sp, f) for f in schemas.SpeciesInfo.model_fields}
                    for k, sp in SPECIES.items()},
        "world": {"seed": cfg.seed, "width": cfg.width, "height": cfg.height,
                  "region_size": cfg.region_size, "regions_x": cfg.regions_x, "regions_y": cfg.regions_y},
        "limits": {"max_players": st.max_players, "name_max_length": schemas.NAME_MAX_LEN,
                   "command_queue_max": cfg.command_queue_max, "command_dedup_size": cfg.command_dedup_size,
                   "max_poll_wait_sec": st.max_poll_wait_sec, "event_log_size": cfg.event_log_size,
                   "player_idle_timeout_sec": st.player_idle_timeout_sec},
        "command_types": list(COMMAND_TYPES), "directions": sorted(DIRS),
        "cell_kinds": {LAND: "land", WATER: "water", ROCK: "rock"},
    }


@router.post("/players", response_model=schemas.JoinResponse, status_code=201)
async def join(body: schemas.JoinRequest, game: GameServer = Depends(get_game),
               x_join_key: Optional[str] = Header(None)):
    if game.settings.join_key and x_join_key != game.settings.join_key:
        raise HTTPException(403, "bad join key")
    if game.full:
        raise HTTPException(503, "server full")
    name = body.name.strip() or "player"
    try:
        pid, token = game.join(name)
    except ValueError as e:
        raise _name_error(str(e))
    return {"player_id": pid, "token": token, "name": name, "world_id": game.world_id,
            "tick": game.world.absolute_tick}


def _name_error(code: str) -> HTTPException:
    return HTTPException(409 if code == "name_taken" else 422, {"code": code})


def _own(pid: str, player_id: str) -> None:
    if pid != player_id:
        raise HTTPException(403, "not your player")


@router.get("/players/{player_id}", response_model=schemas.PlayerStatus)
async def player_status(player_id: str, pid: str = Depends(current_player),
                        game: GameServer = Depends(get_game)):
    _own(pid, player_id)
    w = game.world
    rec = w.players[pid]
    ent = w.player_entity(pid)
    return {"player_id": pid, "name": rec.name, "alive": ent is not None,
            "region": list(rec.region) if ent is not None else None,
            "entity": ent.to_dict() if ent is not None else None,
            "world_id": game.world_id, "tick": w.absolute_tick, "game_time": game.game_time_for(pid)}


@router.put("/players/{player_id}/name")
async def rename(player_id: str, body: schemas.RenameRequest, pid: str = Depends(current_player),
                 game: GameServer = Depends(get_game)):
    """名前を変更する。他のプレイヤーが使用中なら 409 {code: name_taken}。"""
    _own(pid, player_id)
    try:
        return {"name": game.world.rename_player(pid, body.name)}
    except ValueError as e:
        raise _name_error(str(e))


@router.delete("/players/{player_id}", status_code=204)
async def leave(player_id: str, pid: str = Depends(current_player),
                game: GameServer = Depends(get_game)):
    _own(pid, player_id)
    game.leave(pid)


@router.post("/players/{player_id}/commands", response_model=schemas.CommandResponse,
             status_code=202)
async def post_command(player_id: str, body: schemas.CommandRequest,
                       pid: str = Depends(current_player), game: GameServer = Depends(get_game)):
    _own(pid, player_id)
    if body.type != "respawn" and game.world.player_entity(pid) is None:
        raise HTTPException(409, "player is dead; send 'respawn'")
    ok, reason = game.submit(Command(body.command_id, pid, body.type, body.args))
    if reason == "duplicate":
        return {"status": "duplicate", "command_id": body.command_id}
    if reason == "queue_full":
        raise HTTPException(429, "command queue full", headers={"Retry-After": "1"})
    if not ok:
        raise HTTPException(422, reason)
    return {"status": "queued", "command_id": body.command_id}


@router.get("/world/regions", response_model=schemas.RegionsResponse)
async def regions(_: str = Depends(current_player), game: GameServer = Depends(get_game)):
    w = game.world
    return {"world_id": game.world_id, "region_size": w.cfg.region_size,
            "regions": [{"region": list(r), "planet": w.regions[r].planet.id, "url": None}
                        for r in sorted(w.regions)]}


@router.get("/world/snapshot")
async def snapshot(region: Optional[str] = Query(None, description="'x,y'。省略時は全 Region"),
                   _: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    """世界全体のスナップショット。**観戦・管理用**(X-Admin-Key が必要)。PC は `/players/{id}/view` を使う
    (知覚の範囲の内側だけが返る)。"""
    rid = _parse_region(game, region)
    return {"world_id": game.world_id, **game.world.snapshot(rid)}


@router.get("/world/events", response_model=schemas.EventsResponse)
async def events(since: int = Query(..., ge=0, description="前回の next_seq(初回は snapshot の event_seq)"),
                 region: Optional[str] = None,
                 wait: float = Query(0, ge=0, description="イベントが無い場合に待つ秒数(ロングポーリング)"),
                 _: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    """世界全体のイベント。**観戦・管理用**(X-Admin-Key が必要)。PC は `/players/{id}/events` を使う。"""
    rid = _parse_region(game, region)
    w = game.world
    deadline = asyncio.get_running_loop().time() + min(wait, game.settings.max_poll_wait_sec)
    while True:
        if since > w.last_seq:   # サーバ再起動で世界がリセットされた
            raise HTTPException(410, {"code": "world_reset", "snapshot": f"{PREFIX}/world/snapshot"})
        try:
            evs, last = w.events_since(since, rid)
        except EventsExpired:
            raise HTTPException(410, {"code": "events_expired", "snapshot": f"{PREFIX}/world/snapshot"})
        remaining = deadline - asyncio.get_running_loop().time()
        if evs or remaining <= 0:
            return {"world_id": game.world_id, "tick": w.absolute_tick, "game_time": game.game_time_for(),
                    "next_seq": last, "events": evs}
        await game.wait_next_tick(remaining)


# --- PC 向け: 知覚の範囲の内側だけを返す ---
@router.get("/players/{player_id}/view")
async def player_view(player_id: str, pid: str = Depends(current_player), game: GameServer = Depends(get_game)):
    """自分のセル + 距離 r(有効な知覚の範囲)までの、現在の地形・個体・イベントの通し番号。範囲の外は含めない。
    r = 0(眠っている間など)のときは、自分のセルだけ。"""
    _own(pid, player_id)
    v = game.world.player_view(pid)
    if v is None:
        raise HTTPException(409, "player is dead; send 'respawn'")
    return {"world_id": game.world_id, "game_time": game.game_time_for(pid), **v}


@router.get("/players/{player_id}/events", response_model=schemas.EventsResponse)
async def player_events(player_id: str,
                        since: int = Query(..., ge=0, description="前回の next_seq(初回は view の event_seq)"),
                        wait: float = Query(0, ge=0, description="イベントが無い場合に待つ秒数(ロングポーリング)"),
                        pid: str = Depends(current_player), game: GameServer = Depends(get_game)):
    """自分に関するイベントと、有効な知覚の範囲内のイベントだけ(他の個体の内部状態は含めない)。"""
    _own(pid, player_id)
    w = game.world
    deadline = asyncio.get_running_loop().time() + min(wait, game.settings.max_poll_wait_sec)
    while True:
        if since > w.last_seq:
            raise HTTPException(410, {"code": "world_reset", "snapshot": f"{PREFIX}/players/{pid}/view"})
        try:
            evs, last = w.events_since(since, None)
        except EventsExpired:
            raise HTTPException(410, {"code": "events_expired", "snapshot": f"{PREFIX}/players/{pid}/view"})
        vis = w.visible_events(pid, evs)
        remaining = deadline - asyncio.get_running_loop().time()
        if vis or remaining <= 0:
            return {"world_id": game.world_id, "tick": w.absolute_tick, "game_time": game.game_time_for(pid),
                    "next_seq": last, "events": vis}
        await game.wait_next_tick(remaining)


def _entity_of(game: GameServer, pid: str):
    ent = game.world.player_entity(pid)
    if ent is None:
        raise HTTPException(409, "player is dead; send 'respawn'")
    return ent


@router.get("/players/{player_id}/memory")
async def player_memory(player_id: str,
                        kind: str = Query("all", pattern="^(log|long|all)$", description="log = 短期記憶(経験ログ)/ long = 長期記憶"),
                        action: Optional[str] = None, outcome: Optional[str] = None, subject: Optional[str] = None,
                        near: Optional[str] = Query(None, description="'x,y'。この座標の近傍だけ"),
                        radius: int = Query(8, ge=0, le=200), since_tick: Optional[int] = Query(None, ge=0),
                        limit: int = Query(100, ge=1, le=memory_views.MAX_LIMIT), offset: int = Query(0, ge=0),
                        pid: str = Depends(current_player), game: GameServer = Depends(get_game)):
    """自分の記憶(本人のみ)。短期記憶(経験ログ)と長期記憶(強さの降順)。絞り込み・ページネーション付き。"""
    _own(pid, player_id)
    try:
        nr = memory_views.parse_near(near)
    except ValueError:
        raise HTTPException(422, "near must be 'x,y'")
    return memory_views.memory_payload(_entity_of(game, pid), game.world.tick_no, kind, action, outcome, subject,
                                       nr, radius, since_tick, limit, offset)


@router.get("/players/{player_id}/map")
async def player_map(player_id: str, x: Optional[int] = None, y: Optional[int] = None,
                     w: Optional[int] = Query(None, ge=1), h: Optional[int] = Query(None, ge=1),
                     pid: str = Depends(current_player), game: GameServer = Depends(get_game)):
    """自分が知覚した地図(本人のみ)。未知のセルは空白。範囲(x, y, w, h)を省略すると、既知のセルの外接の矩形。"""
    _own(pid, player_id)
    return memory_views.map_payload(_entity_of(game, pid), game.world.tick_no, x, y, w, h)


@router.get("/players/{player_id}/advice")
async def player_advice(player_id: str, pid: str = Depends(current_player), game: GameServer = Depends(get_game)):
    """NPC ならこう行動する、という助言(判断。実行しない)と、判断の材料(地図の要約・思い出した記憶・欲求)。"""
    _own(pid, player_id)
    out = memory_views.advice_payload(game.world, pid)
    if out is None:
        raise HTTPException(409, "player is dead; send 'respawn'")
    return out


# --- 管理: NPC を含む個体の記憶・地図 ---
@router.get("/admin/entities/{entity_id}/memory")
async def admin_entity_memory(entity_id: str, kind: str = Query("all", pattern="^(log|long|all)$"),
                              action: Optional[str] = None, outcome: Optional[str] = None,
                              subject: Optional[str] = None, near: Optional[str] = None,
                              radius: int = Query(8, ge=0, le=200), since_tick: Optional[int] = Query(None, ge=0),
                              limit: int = Query(100, ge=1, le=memory_views.MAX_LIMIT), offset: int = Query(0, ge=0),
                              _: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    ent = game.world.find_entity(entity_id)
    if ent is None or not hasattr(ent, "memory"):
        raise HTTPException(404, "no such individual")
    try:
        nr = memory_views.parse_near(near)
    except ValueError:
        raise HTTPException(422, "near must be 'x,y'")
    return memory_views.memory_payload(ent, game.world.tick_no, kind, action, outcome, subject, nr, radius,
                                       since_tick, limit, offset)


@router.get("/admin/entities/{entity_id}/map")
async def admin_entity_map(entity_id: str, x: Optional[int] = None, y: Optional[int] = None,
                           w: Optional[int] = Query(None, ge=1), h: Optional[int] = Query(None, ge=1),
                           _: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    ent = game.world.find_entity(entity_id)
    if ent is None or not hasattr(ent, "memory"):
        raise HTTPException(404, "no such individual")
    return memory_views.map_payload(ent, game.world.tick_no, x, y, w, h)


@router.get("/admin/memory-stats")
async def admin_memory_stats(_: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    """記憶・地図の件数と大きさ(全個体の合計。増え続けていないかの監視用)。"""
    return {"tick": game.world.tick_no, **game.world.memory_stats()}


@router.get("/admin/action-cache", response_model=schemas.ActionCacheStatus)
async def action_cache_status(_: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    """周期アクションの解決(アクション計画)のキャッシュの状態。管理者権限(X-Admin-Key)が必要。"""
    return game.world.action_cache_status()


@router.post("/admin/action-cache/clear", response_model=schemas.ActionCacheStatus)
async def clear_action_cache(body: Optional[schemas.ClearActionCacheRequest] = None,
                             _: None = Depends(require_admin), game: GameServer = Depends(get_game)):
    """周期アクションの解決(アクション計画)のキャッシュを、全 Region で捨てる。次の tick で作り直される。
    ゲームの状態(個体・タイマーの基準 tick)は変わらない。管理者権限(X-Admin-Key)が必要。"""
    rebuild = bool(body and body.rebuild_wheels)
    status = game.world.clear_action_cache(rebuild_wheels=rebuild)
    log.info("admin: action cache cleared (rebuild_wheels=%s)", rebuild)
    return status


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(app.state.game.run()) if settings.autotick else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Ecologrid", version="0.1.0", lifespan=lifespan)
    app.state.game = GameServer(settings)
    app.include_router(router)
    return app


app = create_app()
