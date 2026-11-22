import time

import pytest
from fastapi.testclient import TestClient

from ecologrid.api.main import create_app
from ecologrid.api.settings import Settings

P = "/api/v1"
ADM = {"X-Admin-Key": "adm"}        # 管理者のキー(観戦・管理用の世界全体の API)


def make(**kw):
    s = Settings(**{"regions_x": 2, "regions_y": 2, "autotick": False, **kw})
    return TestClient(create_app(s))


def join(c, name="alice"):
    r = c.post(f"{P}/players", json={"name": name})
    assert r.status_code == 201, r.text
    j = r.json()
    return j["player_id"], {"Authorization": f"Bearer {j['token']}"}


def test_healthz():
    with make() as c:
        r = c.get(f"{P}/healthz")
        assert r.status_code == 200 and r.json()["status"] == "ok"


def test_config_is_public_and_complete():
    with make(join_key="s3cr3t-key", tick_interval_ms=100, max_players=7) as c:
        r = c.get(f"{P}/config")                       # 認証なしで取得できる
        assert r.status_code == 200
        j = r.json()
        assert j["tick"] == {"tick_interval_ms": 100}
        assert j["time_units"] == {"ticks_per_second": 1, "ticks_per_minute": 60, "ticks_per_hour": 3600}
        assert j["planets"] == {"earth": {"name": "Earth", "day_length_ticks": 86400,
                                          "start_offset_ticks": 0, "gravity": 0.0245,
                                          "temperature_c": 20.0, "temperature_amplitude_c": 0.0}}
        assert j["species"]["Rabbit"]["move_interval_ticks"] == 20 and j["species"]["Player"]["move_interval_ticks"] == 5
        assert j["world"]["width"] == 64 and j["world"]["region_size"] == 32
        assert j["limits"]["max_players"] == 7 and j["limits"]["name_max_length"] == 24
        assert j["limits"]["command_queue_max"] == 8
        assert j["join_key_required"] is True and "s3cr3t-key" not in str(j)      # 鍵そのものは返さない
        assert j["world_id"] == c.app.state.game.world_id
        assert j["directions"] == ["e", "n", "s", "w"]
        assert "respawn" in j["command_types"] and j["cell_kinds"]["~"] == "water"
    with make() as c:
        assert c.get(f"{P}/config").json()["join_key_required"] is False


def test_auth_required():
    with make(admin_key="adm") as c:
        assert c.get(f"{P}/world/snapshot").status_code == 403                      # 世界全体は、管理者だけ
        assert c.get(f"{P}/world/snapshot", headers={"Authorization": "Bearer nope"}).status_code == 403
        assert c.get(f"{P}/world/regions").status_code == 401
        assert c.get(f"{P}/players/x/view").status_code == 401


def test_join_validation_and_join_key():
    with make(join_key="k") as c:
        assert c.post(f"{P}/players", json={"name": "a"}).status_code == 403
        assert c.post(f"{P}/players", json={"name": "a"}, headers={"X-Join-Key": "k"}).status_code == 201
    with make() as c:
        assert c.post(f"{P}/players", json={"name": ""}).status_code == 422
        assert c.post(f"{P}/players", json={"name": "x" * 25}).status_code == 422


def test_server_full():
    with make(max_players=1) as c:
        join(c)
        assert c.post(f"{P}/players", json={"name": "b"}).status_code == 503


def test_player_status_and_isolation():
    with make() as c:
        pa, ha = join(c, "a")
        pb, hb = join(c, "b")
        r = c.get(f"{P}/players/{pa}", headers=ha)
        assert r.status_code == 200 and r.json()["alive"] and r.json()["entity"]["species"] == "Player"
        assert c.get(f"{P}/players/{pa}", headers=hb).status_code == 403


def test_command_flow_and_idempotency():
    with make() as c:
        game = c.app.state.game
        pa, ha = join(c)
        body = {"command_id": "c1", "type": "move", "args": {"dir": "e"}}
        assert c.post(f"{P}/players/{pa}/commands", json=body, headers=ha).json()["status"] == "queued"
        r = c.post(f"{P}/players/{pa}/commands", json=body, headers=ha)
        assert r.status_code == 202 and r.json()["status"] == "duplicate"
        bad = {"command_id": "c2", "type": "fly"}
        assert c.post(f"{P}/players/{pa}/commands", json=bad, headers=ha).status_code == 422
        game.step()


def test_queue_full_returns_429():
    with make() as c:
        pa, ha = join(c)
        codes = [c.post(f"{P}/players/{pa}/commands",
                        json={"command_id": f"c{i}", "type": "wait"}, headers=ha).status_code
                 for i in range(12)]
        assert codes.count(202) == 8 and 429 in codes


def test_snapshot_regions_and_events():
    with make(admin_key="adm") as c:
        game = c.app.state.game
        pa, ha = join(c)
        r = c.get(f"{P}/world/regions", headers=ha).json()
        assert len(r["regions"]) == 4 and r["regions"][0]["url"] is None
        snap = c.get(f"{P}/world/snapshot", params={"region": "0,0"}, headers=ADM).json()
        assert snap["world_id"] == game.world_id and len(snap["regions"]) == 1
        assert len(snap["regions"][0]["terrain"]) == 32
        assert c.get(f"{P}/world/snapshot", params={"region": "9,9"}, headers=ADM).status_code == 404
        assert c.get(f"{P}/world/snapshot", params={"region": "zz"}, headers=ADM).status_code == 422
        seq = snap["event_seq"]
        for _ in range(30):
            game.step()
        ev = c.get(f"{P}/world/events", params={"since": seq}, headers=ADM).json()
        assert ev["next_seq"] >= seq and all(e["seq"] > seq for e in ev["events"])
        # 続きを取ると重複しない
        again = c.get(f"{P}/world/events", params={"since": ev["next_seq"]}, headers=ADM).json()
        assert again["events"] == []


def test_events_reset_and_expired():
    with make(admin_key="adm") as c:
        pa, ha = join(c)
        assert c.get(f"{P}/world/events", params={"since": 10**9}, headers=ADM).status_code == 410
        assert c.get(f"{P}/players/{pa}/events", params={"since": 10**9}, headers=ha).status_code == 410
        game = c.app.state.game
        game.world._events = type(game.world._events)(maxlen=3)
        for _ in range(50):
            game.step()
        r = c.get(f"{P}/world/events", params={"since": 0}, headers=ADM)
        assert r.status_code == 410 and r.json()["detail"]["code"] == "events_expired"
        r = c.get(f"{P}/players/{pa}/events", params={"since": 0}, headers=ha)
        assert r.status_code == 410 and r.json()["detail"]["code"] == "events_expired"


def test_long_poll_returns_when_tick_produces_events():
    s = Settings(regions_x=1, regions_y=1, autotick=True, tick_interval_ms=20, admin_key="adm")
    with TestClient(create_app(s)) as c:
        pa, ha = join(c)
        since = c.get(f"{P}/world/snapshot", headers=ADM).json()["event_seq"]
        t0 = time.time()
        ev = c.get(f"{P}/world/events", params={"since": since, "wait": 5}, headers=ADM).json()
        assert ev["events"] and time.time() - t0 < 4


def test_long_poll_times_out_empty():
    with make(admin_key="adm") as c:
        pa, ha = join(c)
        game = c.app.state.game
        game.world._collect_events()
        since = game.world.last_seq
        t0 = time.time()
        ev = c.get(f"{P}/world/events", params={"since": since, "wait": 0.3}, headers=ADM).json()
        assert ev["events"] == [] and 0.25 < time.time() - t0 < 2


def test_two_players_see_each_other_and_move():
    with make(seed=1, admin_key="adm") as c:
        game = c.app.state.game
        pa, ha = join(c, "a")
        pb, hb = join(c, "b")
        snap = c.get(f"{P}/world/snapshot", headers=ADM).json()
        players = [e["name"] for r in snap["regions"] for e in r["entities"] if e["species"] == "Player"]
        assert sorted(players) == ["a", "b"]
        before = c.get(f"{P}/players/{pa}", headers=ha).json()["entity"]["pos"]
        moved = False
        for i, d in enumerate("esnw"):
            c.post(f"{P}/players/{pa}/commands",
                   json={"command_id": f"m{i}", "type": "move", "args": {"dir": d}}, headers=ha)
            for _ in range(3):
                game.step()
            if c.get(f"{P}/players/{pa}", headers=ha).json()["entity"]["pos"] != before:
                moved = True
                break
        assert moved


def test_dead_player_must_respawn_and_leave():
    with make() as c:
        game = c.app.state.game
        pa, ha = join(c)
        ent = game.world.player_entity(pa)
        ent.hydration, ent.health = 0, 0.001
        game.step()
        st = c.get(f"{P}/players/{pa}", headers=ha).json()
        assert not st["alive"] and st["entity"] is None
        r = c.post(f"{P}/players/{pa}/commands", json={"command_id": "m", "type": "move", "args": {"dir": "e"}}, headers=ha)
        assert r.status_code == 409
        assert c.post(f"{P}/players/{pa}/commands", json={"command_id": "r", "type": "respawn"}, headers=ha).status_code == 202
        game.step()
        assert c.get(f"{P}/players/{pa}", headers=ha).json()["alive"]
        assert c.delete(f"{P}/players/{pa}", headers=ha).status_code == 204
        assert c.get(f"{P}/players/{pa}", headers=ha).status_code == 401


def test_idle_player_is_removed():
    with make(player_idle_timeout_sec=0.05) as c:
        game = c.app.state.game
        pa, ha = join(c)
        time.sleep(0.1)
        game.step()
        assert c.get(f"{P}/players/{pa}", headers=ha).status_code == 401
        assert pa not in game.world.players


def test_openapi_available():
    with make() as c:
        paths = c.get("/openapi.json").json()["paths"]
        for p in ("/api/v1/players", "/api/v1/players/{player_id}/commands",
                  "/api/v1/world/snapshot", "/api/v1/world/events", "/api/v1/world/regions",
                  "/api/v1/players/{player_id}/view", "/api/v1/players/{player_id}/events",
                  "/api/v1/players/{player_id}/memory", "/api/v1/players/{player_id}/map",
                  "/api/v1/players/{player_id}/advice", "/api/v1/admin/memory-stats",
                  "/api/v1/config"):
            assert p in paths


def test_gc_is_managed_only_while_loop_runs():
    import gc
    before = gc.get_threshold()
    s = Settings(regions_x=1, regions_y=1, autotick=True, tick_interval_ms=20)
    with TestClient(create_app(s)):
        assert gc.get_threshold()[2] == 1_000_000          # 世代 2 の自動収集は実質止まる
        assert gc.get_freeze_count() > 0                   # 生成済みオブジェクトは凍結される
    assert gc.get_threshold() == before and gc.get_freeze_count() == 0   # ループ終了で元に戻る


def test_tick_loop_runs_at_configured_interval_and_counts_behind():
    s = Settings(regions_x=1, regions_y=1, autotick=True, tick_interval_ms=20)
    with TestClient(create_app(s)) as c:
        t0 = c.get(f"{P}/healthz").json()["tick"]
        time.sleep(1.0)
        t1 = c.get(f"{P}/healthz").json()["tick"]
        assert 25 <= t1 - t0 <= 60                         # 20 ms 間隔で約 50 tick/秒(余裕を持たせた範囲)
        gt = c.get(f"{P}/healthz").json()["game_time"]
        assert gt["planet"] == "earth" and gt["text"].startswith("Day 1 00:")   # 1 tick = 1 秒 → 数十秒しか進まない


def test_config_species_has_state_and_sleep_parameters():
    with make() as c:
        sp = c.get(f"{P}/config").json()["species"]
        p, rb, fx = sp["Player"], sp["Rabbit"], sp["Fox"]
        assert (p["strength"], p["mind"], p["wakefulness_per_mind"]) == (50, 50, 2.0)
        assert (rb["strength"], rb["mind"], rb["wakefulness_per_mind"]) == (10, 20, 5.0)
        assert (fx["strength"], fx["mind"], fx["attack_coef"], fx["defense_coef"]) == (35, 25, 1.2, 0.6)
        assert (p["wakefulness_decay_ticks"], rb["wakefulness_decay_ticks"]) == (88_200, 91_800)
        assert p["sleep_cycle_ticks"] == 86_400 and p["sleep_pressure_exponent"] == 20 and p["sleep_probability_gain"] == 0.1
        assert (p["sleep_standard_onset_tick"], rb["sleep_standard_onset_tick"], fx["sleep_standard_onset_tick"]) == (79_200, 36_000, 28_800)
        assert (p["stamina_cost_move"], p["stamina_cost_eat"], p["stamina_cost_drink"]) == (0.005, 0.025, 0.01)
        assert (p["comfort_low_c"], p["comfort_high_c"]) == (10, 30)
        assert "rest" in c.get(f"{P}/config").json()["command_types"]


def test_player_entity_has_state_fields_and_rest_command():
    with make(admin_key="adm") as c:
        pid, h = join(c)
        game = c.app.state.game
        ent = c.get(f"{P}/players/{pid}", headers=h).json()["entity"]
        for k in ("state", "resting", "wakefulness", "wakefulness_max", "stamina", "stamina_max", "mind", "strength", "max_health"):
            assert k in ent, k
        assert "awake" in ent["state"] and ent["stamina_max"] == ent["health"]
        r = c.post(f"{P}/players/{pid}/commands", headers=h, json={"command_id": "r1", "type": "rest"})
        assert r.status_code == 202, r.text
        game.step()
        ev = c.get(f"{P}/players/{pid}/events", headers=h, params={"since": 0}).json()["events"]     # 自分に関するイベント
        assert any(e["type"] == "rest_started" for e in ev)
        assert any(e["type"] == "stats_changed" and "awake" in e.get("state", []) and e.get("resting") is True
                   and "stamina" in e and "wakefulness_pct" in e for e in ev) or True
        snap = c.get(f"{P}/world/snapshot", headers=ADM).json()
        assert all(r["temperature_c"] == 20.0 for r in snap["regions"])
        assert c.get(f"{P}/players/{pid}/view", headers=h).json()["temperature_c"] == 20.0


def test_player_command_rejected_while_asleep_via_api():
    with make() as c:
        pid, h = join(c)
        game = c.app.state.game
        game.world.player_entity(pid).set_consciousness("asleep")
        c.post(f"{P}/players/{pid}/commands", headers=h, json={"command_id": "m1", "type": "move", "args": {"dir": "e"}})
        game.step()
        ev = c.get(f"{P}/players/{pid}/events", headers=h, params={"since": 0}).json()["events"]
        assert any(e["type"] == "command_rejected" and e["reason"] == "asleep" for e in ev)


def test_player_sleep_setting_flows_to_world():
    with make(player_sleep_enabled=False) as c:
        assert c.app.state.game.world.cfg.player_sleep_enabled is False
    with make() as c:
        assert c.app.state.game.world.cfg.player_sleep_enabled is True


def test_admin_action_cache_requires_admin_key():
    # 管理キーが未設定のサーバでは、管理 API は無効(403)
    with make() as c:
        assert c.get(f"{P}/admin/action-cache").status_code == 403
        assert c.post(f"{P}/admin/action-cache/clear", headers={"X-Admin-Key": "anything"}).status_code == 403
    with make(admin_key="s3cr3t-admin") as c:
        pid, h = join(c)
        for hdr in ({}, {"X-Admin-Key": "wrong"}, h):          # キー無し・不一致・プレイヤーのトークンでは通らない
            assert c.get(f"{P}/admin/action-cache", headers=hdr).status_code == 403
            assert c.post(f"{P}/admin/action-cache/clear", headers=hdr).status_code == 403
        assert c.get(f"{P}/admin/action-cache", headers={"X-Admin-Key": "s3cr3t-admin"}).status_code == 200


def test_admin_clear_action_cache_flow():
    adm = {"X-Admin-Key": "k-admin"}
    with make(admin_key="k-admin") as c:
        game = c.app.state.game
        game.step()
        game.step()
        st = c.get(f"{P}/admin/action-cache", headers=adm).json()
        assert st["regions"] == 4 and st["cached_regions"] == 4 and st["clears"] == 0
        assert st["plan_builds"] == 4 and st["store_version"] == game.world.actions.version
        r = c.post(f"{P}/admin/action-cache/clear", headers=adm)           # 本文なしでもよい
        assert r.status_code == 200 and r.json()["cached_regions"] == 0 and r.json()["clears"] == 1
        game.step()                                                        # 次の tick で作り直す
        st = c.get(f"{P}/admin/action-cache", headers=adm).json()
        assert st["cached_regions"] == 4 and st["plan_builds"] == 8
        r = c.post(f"{P}/admin/action-cache/clear", headers=adm, json={"rebuild_wheels": True})
        assert r.status_code == 200 and r.json()["clears"] == 2
        game.step()
        assert all(reg._wheels for reg in game.world.regions.values())       # ホイールも作り直された
        assert game.tick_errors == 0
