"""PC の知覚の範囲だけを返す API・記憶/地図/助言の API・管理 API(docs/tasks/1_19)。"""
import pytest
from fastapi.testclient import TestClient

from ecologrid.api.main import create_app
from ecologrid.api.settings import Settings
from ecologrid.core.grid import Pos
from ecologrid.core.perception import perceive

P = "/api/v1"
ADM = {"X-Admin-Key": "adm"}


def make(**kw):
    s = Settings(**{"regions_x": 2, "regions_y": 2, "autotick": False, "admin_key": "adm", **kw})
    return TestClient(create_app(s))


def join(c, name="alice"):
    j = c.post(f"{P}/players", json={"name": name}).json()
    return j["player_id"], {"Authorization": f"Bearer {j['token']}"}


def _setup(c, ratio=None):
    pid, h = join(c)
    game = c.app.state.game
    ent = game.world.player_entity(pid)
    region = game.world.regions[game.world.players[pid].region]
    if ratio is not None:
        ent.wakefulness = ent.wakefulness_max * ratio
    return pid, h, game, ent, region


def _free_cell_near(region, ent, dx, dy):
    return Pos(ent.grid_position.x + dx, ent.grid_position.y + dy)


# --- view ---
def test_view_is_a_square_of_effective_range_around_the_player():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=0.5)            # Player 20 × 0.5 = 10
        v = c.get(f"{P}/players/{pid}/view", headers=h).json()
        assert v["r"] == 10 and v["perception_range"] == 20 and v["center"] == list(ent.grid_position)
        assert len(v["rows"]) == 21 and all(len(row) == 21 for row in v["rows"])
        assert v["origin"] == [ent.grid_position.x - 10, ent.grid_position.y - 10]
        assert v["rows"][10][10] == game.world.terrain.kind(ent.grid_position.x, ent.grid_position.y)
        assert v["self"]["id"] == ent.entity_id and "stamina" in v["self"] and v["tick"] == game.world.tick_no
        assert v["event_seq"] == game.world.last_seq and v["temperature_c"] == 20.0


def test_view_contains_only_entities_inside_range_with_public_fields():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=0.5)            # r = 10
        near = region.spawn("Rabbit", ent.grid_position)
        edge = Pos(ent.grid_position.x + 10, ent.grid_position.y)
        out = Pos(ent.grid_position.x + 11, ent.grid_position.y)
        game.world.regions[(out.x // 32, out.y // 32)] if out.x < 128 else None
        v = c.get(f"{P}/players/{pid}/view", headers=h).json()
        ids = {e["id"] for e in v["entities"]}
        assert near.entity_id in ids and ent.entity_id not in ids        # 自分は self に、他は entities に
        for e in v["entities"]:
            assert set(e) <= {"id", "name", "species", "pos", "state", "player_id"}      # 内部状態(水分・記憶など)は含めない
            assert max(abs(e["pos"][0] - v["center"][0]), abs(e["pos"][1] - v["center"][1])) <= 10
        assert [e for e in v["entities"] if e["id"] == near.entity_id][0]["state"] == ["awake"]


def test_range_zero_shows_only_own_cell():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=0.01)            # 20 × 0.01 → 0
        region.spawn("Grass", ent.grid_position) if True else None
        far = region.spawn("Rabbit", Pos(ent.grid_position.x, ent.grid_position.y))
        v = c.get(f"{P}/players/{pid}/view", headers=h).json()
        assert v["r"] == 0 and len(v["rows"]) == 1 and len(v["rows"][0]) == 1       # 自分のセルだけ
        assert all(e["pos"] == list(ent.grid_position) for e in v["entities"])
        ent.set_consciousness("asleep")
        v = c.get(f"{P}/players/{pid}/view", headers=h).json()
        assert v["r"] == 0 and len(v["rows"]) == 1                                     # 眠っている間も、自分のセルだけ


def test_view_requires_own_token_and_live_player():
    with make() as c:
        pid, h, game, ent, region = _setup(c)
        pid2, h2 = join(c, "bob")
        assert c.get(f"{P}/players/{pid}/view", headers=h2).status_code == 403
        assert c.get(f"{P}/players/{pid}/view").status_code == 401
        region.remove(ent, "death")
        game.world._update_players()
        assert c.get(f"{P}/players/{pid}/view", headers=h).status_code == 409


def test_perception_range_changes_with_wakefulness_through_the_view():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=1.0)
        assert c.get(f"{P}/players/{pid}/view", headers=h).json()["r"] == 20
        ent.wakefulness = ent.wakefulness_max * 0.25
        assert c.get(f"{P}/players/{pid}/view", headers=h).json()["r"] == 5


# --- イベント(知覚の範囲内だけ) ---
def test_events_are_limited_to_own_and_in_range_and_sanitized():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=0.5)            # r = 10
        since = game.world.last_seq
        near = region.spawn("Rabbit", ent.grid_position)
        far_pos = Pos(ent.grid_position.x + 15, ent.grid_position.y) if ent.grid_position.x + 15 < 128 else \
            Pos(ent.grid_position.x - 15, ent.grid_position.y)
        far_region = game.world.regions[(far_pos.x // 32, far_pos.y // 32)]
        far = far_region.spawn("Rabbit", far_pos) if far_region.walkable(far_pos) else None
        region.remove(near, "death")
        if far is not None:
            far_region.remove(far, "death")
        game.world._collect_events()
        evs = c.get(f"{P}/players/{pid}/events", headers=h, params={"since": since}).json()["events"]
        removed = {e["id"] for e in evs if e["type"] == "entity_removed"}
        assert near.entity_id in removed
        if far is not None:
            assert far.entity_id not in removed                         # 範囲外のイベントは届かない
        spawned = [e for e in evs if e["type"] == "entity_spawned" and e["entity"]["id"] == near.entity_id]
        assert spawned and set(spawned[0]["entity"]) <= {"id", "name", "species", "pos", "state", "player_id"}
        admin = c.get(f"{P}/world/events", headers=ADM, params={"since": since}).json()["events"]
        assert len(admin) > len(evs) or far is None                    # 全体のイベントは管理者だけ


def test_own_events_always_arrive_even_when_range_is_zero():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=0.01)
        since = game.world.last_seq
        c.post(f"{P}/players/{pid}/commands", headers=h, json={"command_id": "r1", "type": "rest"})
        game.step()
        evs = c.get(f"{P}/players/{pid}/events", headers=h, params={"since": since}).json()["events"]
        assert any(e["type"] == "rest_started" for e in evs) and any(e["type"] == "stats_changed" for e in evs)


# --- 記憶 ---
def test_memory_api_log_long_filters_pagination_and_auth():
    with make() as c:
        pid, h, game, ent, region = _setup(c)
        for i in range(5):
            region.tick_no += 1
            ent.remember(region, "drink", "drank")
        ent.remember(region, "eat", "no_prey")
        j = c.get(f"{P}/players/{pid}/memory", headers=h, params={"kind": "log"}).json()
        n = j["log_total"]
        assert n >= 6 and "long" not in j and j["individual"] == ent.entity_id and j["params"]["forgetting_factor"] == 0.9
        assert all(set(e) >= {"tick", "action", "outcome", "x", "y", "d", "reward", "strength"} for e in j["log"])
        f = c.get(f"{P}/players/{pid}/memory", headers=h, params={"kind": "log", "action": "drink", "outcome": "drank"}).json()
        assert f["log_total"] == 5 and all(e["outcome"] == "drank" for e in f["log"])
        p = c.get(f"{P}/players/{pid}/memory", headers=h, params={"kind": "log", "limit": 2, "offset": 1}).json()
        assert len(p["log"]) == 2 and p["log_total"] == n
        x, y = ent.grid_position
        near = c.get(f"{P}/players/{pid}/memory", headers=h, params={"kind": "log", "near": f"{x + 100},{y}", "radius": 5}).json()
        assert near["log_total"] == 0
        assert c.get(f"{P}/players/{pid}/memory", headers=h, params={"near": "bad"}).status_code == 422
        since = c.get(f"{P}/players/{pid}/memory", headers=h, params={"kind": "log", "since_tick": 10 ** 9}).json()
        assert since["log_total"] == 0
        ent.fall_asleep(region)                                                  # 入眠 → 長期記憶に整理される
        j2 = c.get(f"{P}/players/{pid}/memory", headers=h).json()
        assert j2["log_total"] == 0 and j2["long_total"] >= 3 and j2["organized"] == 1
        strengths = [e["strength"] for e in j2["long"]]
        assert strengths == sorted(strengths, reverse=True)                      # 強さの降順
        drank = [e for e in j2["long"] if e["outcome"] == "drank"][0]
        assert drank["count"] == 5 and drank["mean_reward"] == pytest.approx(10.0)
        pid2, h2 = join(c, "bob")
        assert c.get(f"{P}/players/{pid}/memory", headers=h2).status_code == 403
        assert c.get(f"{P}/players/{pid}/memory?limit=100000", headers=h).status_code == 422


def test_map_api_returns_known_terrain_and_resources_only():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=0.3)             # r = 6
        pos = ent.grid_position
        region.spawn("Grass", pos)
        perceive(ent, region)
        j = c.get(f"{P}/players/{pid}/map", headers=h).json()
        assert j["known_cells"] == ent.map.known and j["version"] == ent.map.version
        assert all(len(row) == len(j["rows"][0]) for row in j["rows"])
        assert any(r["species"] == "Grass" for r in j["resources"])
        sub = c.get(f"{P}/players/{pid}/map", headers=h, params={"x": pos.x, "y": pos.y, "w": 1, "h": 1}).json()
        assert sub["rows"] == [game.world.terrain.kind(pos.x, pos.y)]
        unknown = c.get(f"{P}/players/{pid}/map", headers=h, params={"x": 0, "y": 0, "w": 3, "h": 2}).json()
        assert all(set(row) <= {" ", ".", "~", "#"} for row in unknown["rows"])


def test_map_api_empty_before_any_perception():
    with make() as c:
        pid, h, game, ent, region = _setup(c)
        ent.map = None
        j = c.get(f"{P}/players/{pid}/map", headers=h).json()
        assert j["known_cells"] == 0 and j["rows"] == [] and j["resources"] == []


# --- 助言 ---
def test_advice_returns_a_decision_without_acting():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=1.0)
        ent.hydration = 20.0
        perceive(ent, region)
        before = (tuple(ent.grid_position), list(ent.path), ent.resting, ent.next_move_tick, ent.stamina, ent.hydration)
        a = c.get(f"{P}/players/{pid}/advice", headers=h).json()
        assert a["kind"] in ("drink", "move", "wander") and "reason" in a and a["r"] == 20
        assert {"hydration", "energy", "stamina", "health"} <= set(a["materials"]) and a["needs"]["thirsty"] is True
        assert (tuple(ent.grid_position), list(ent.path), ent.resting, ent.next_move_tick, ent.stamina, ent.hydration) == before
        assert c.get(f"{P}/players/{pid}/advice", headers=h).json()["kind"] == a["kind"]       # 要求のたびに同じ(副作用なし)


def test_advice_for_thirsty_player_at_shore_is_drink_and_recalled_places_are_listed():
    with make() as c:
        pid, h, game, ent, region = _setup(c, ratio=1.0)
        water = sorted(region.water_cells)[0]
        shore = next(Pos(water.x + dx, water.y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                     if region.walkable(Pos(water.x + dx, water.y + dy)))
        region.move(ent, shore)
        ent.hydration = 20.0
        perceive(ent, region)
        a = c.get(f"{P}/players/{pid}/advice", headers=h).json()
        assert a["kind"] == "drink" and a["goal"] == "water"
        ent.remember(region, "rest", "rested")
        for _ in range(3):
            ent.memory.organize(game.world.tick_no)
        for e in list(ent.memory.long.values()):
            e.strength = 500.0                                  # 強い記憶は、場所に来ると、ほぼ確実に思い出される
        ent.memory._last_place_keys = None
        a2 = c.get(f"{P}/players/{pid}/advice", headers=h).json()
        assert isinstance(a2["recalled"], list)


def test_advice_dead_player_is_409():
    with make() as c:
        pid, h, game, ent, region = _setup(c)
        region.remove(ent, "death")
        game.world._update_players()
        assert c.get(f"{P}/players/{pid}/advice", headers=h).status_code == 409
        assert c.get(f"{P}/players/{pid}/memory", headers=h).status_code == 409
        assert c.get(f"{P}/players/{pid}/map", headers=h).status_code == 409


# --- 管理 ---
def test_admin_can_read_npc_memory_map_and_stats_but_players_cannot():
    with make() as c:
        pid, h, game, ent, region = _setup(c)
        pos = ent.grid_position
        rb = region.spawn("Rabbit", pos)
        rb.remember(region, "drink", "drank")
        perceive(rb, region)
        m = c.get(f"{P}/admin/entities/{rb.entity_id}/memory", headers=ADM).json()
        assert m["individual"] == rb.entity_id and any(e["outcome"] == "drank" for e in m["log"])
        mp = c.get(f"{P}/admin/entities/{rb.entity_id}/map", headers=ADM).json()
        assert mp["known_cells"] == rb.map.known
        st = c.get(f"{P}/admin/memory-stats", headers=ADM).json()
        assert st["individuals"] >= 2 and st["log_entries"] >= 1 and st["tick"] == game.world.tick_no
        assert c.get(f"{P}/admin/entities/nope/memory", headers=ADM).status_code == 404
        for path in (f"/admin/entities/{rb.entity_id}/memory", f"/admin/entities/{rb.entity_id}/map", "/admin/memory-stats"):
            assert c.get(f"{P}{path}", headers=h).status_code == 403            # プレイヤーのトークンでは読めない
            assert c.get(f"{P}{path}").status_code == 403
    with make(admin_key=None) as c:
        assert c.get(f"{P}/admin/memory-stats", headers=ADM).status_code == 403   # 管理キー未設定なら無効


def test_public_snapshot_and_entity_dicts_never_include_memory_or_map():
    with make() as c:
        pid, h, game, ent, region = _setup(c)
        perceive(ent, region)
        ent.remember(region, "drink", "drank")
        snap = c.get(f"{P}/world/snapshot", headers=ADM).json()
        for r in snap["regions"]:
            for e in r["entities"]:
                assert "memory" not in e and "map" not in e and "preference" not in e and "absorption" not in e
        st = c.get(f"{P}/players/{pid}", headers=h).json()["entity"]
        assert "memory" not in st and "map" not in st


# --- 名前の重複チェック・名前変更(マップ上の表示は名前の 1 文字目) ---
def test_join_rejects_duplicate_name_and_rename_checks_too():
    with make() as c:
        pid, h = join(c, "alice")
        r = c.post(f"{P}/players", json={"name": "alice"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"
        pid2, h2 = join(c, "bob")
        r = c.put(f"{P}/players/{pid2}/name", json={"name": "alice"}, headers=h2)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "name_taken"
        assert c.get(f"{P}/players/{pid2}", headers=h2).json()["name"] == "bob"      # 失敗しても変わらない
        assert c.put(f"{P}/players/{pid2}/name", json={"name": "bob"}, headers=h2).status_code == 200   # 自分の名前は OK
        assert c.put(f"{P}/players/{pid}/name", json={"name": "Zed"}, headers=h).json() == {"name": "Zed"}
        assert c.post(f"{P}/players", json={"name": "alice"}).status_code == 201     # 空いた名前は使える
        assert c.put(f"{P}/players/{pid}/name", json={"name": "x"}, headers=h2).status_code == 403


def test_rename_updates_entity_and_nearby_players_see_event():
    with make() as c:
        pid, h = join(c, "alice")
        pid2, h2 = join(c, "bob")
        game = c.app.state.game
        a, b = game.world.player_entity(pid), game.world.player_entity(pid2)
        seq = game.world.last_seq
        assert c.put(f"{P}/players/{pid}/name", json={"name": "Ωmega"}, headers=h).status_code == 200
        assert a.entity_name == "Ωmega" and game.world.players[pid].name == "Ωmega"
        evs = c.get(f"{P}/players/{pid}/events", params={"since": seq}, headers=h).json()["events"]
        assert [e for e in evs if e["type"] == "player_renamed" and e["name"] == "Ωmega"]
