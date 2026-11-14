import time

from ecologrid_cli.bot import Bot
from ecologrid_cli.play import run_script
from ecologrid_cli.view import WorldView


def server_state(game, s):
    """サーバの全 Entity のうち、PC の有効な知覚の範囲(自分のセル + 距離 r。チェビシェフ距離)の中のもの。"""
    ent = game.world.player_entity(s.api.player_id)
    r = ent.effective_range(game.world.regions[game.world.players[s.api.player_id].region])
    c = ent.grid_position
    return {e_id: tuple(e.grid_position) for reg in game.world.regions.values() for e_id, e in reg.entities.items()
            if max(abs(e.grid_position.x - c.x), abs(e.grid_position.y - c.y)) <= r}


def view_state(view):
    """クライアントが持つ個体のうち、知覚の範囲の中のもの。"""
    return {i: tuple(e["pos"]) for i, e in view.entities.items() if view.in_view(*e["pos"])}


def test_start_loads_config_and_world(env):
    s, game, _ = env
    assert s.tick_interval_ms == 50 and s.config["world"]["width"] == 64
    assert s.view.me_id and s.view.alive
    assert view_state(s.view) == server_state(game, s)


def test_view_mirrors_server_over_many_ticks(env):
    """イベント適用だけで、サーバの全 Entity の位置・増減に追従できる(捕食・出生・越境を含む)。"""
    s, game, _ = env
    for i in range(1, 301):
        game.step()
        if i % 7 == 0:
            s.poll(0)
            assert view_state(s.view) == server_state(game, s), f"diverged at tick {i}"
    s.poll(0)
    visible = server_state(game, s)
    assert sum(s.view.census().values()) == len([i for i in visible])


def test_move_command_updates_view(env):
    s, game, _ = env
    start = s.view.me_pos
    moved = False
    for d in "esnw":
        assert s.send("move", dir=d) == "queued"
        for _ in range(3):
            game.step()
        s.poll(0)
        if s.view.me_pos != start:
            moved = True
            break
    assert moved
    assert s.view.me_pos == tuple(game.world.player_entity(s.api.player_id).grid_position)


def test_resync_on_410(env):
    s, game, _ = env
    game.world._events = type(game.world._events)(maxlen=3)
    for _ in range(40):
        game.step()
    s.poll(0)                                  # events_expired → view を取り直す
    assert view_state(s.view) == server_state(game, s)
    assert s.view.me_id is not None


def test_rejoin_after_server_forgets_player(env):
    s, game, _ = env
    old = s.api.player_id
    game.leave(old)                            # タイムアウト退出を模擬
    game.step()
    s.poll(0)
    assert s.reconnects == 1 and s.api.player_id != old and s.view.alive and s.view.me_id


def test_death_and_respawn(env):
    s, game, _ = env
    ent = game.world.player_entity(s.api.player_id)
    ent.hydration, ent.health = 0, 0.001
    game.step()
    s.poll(0)
    assert not s.view.alive
    assert s.send("move", dir="e") == "dead"
    assert s.send("respawn") == "queued"
    game.step()
    s.poll(0)
    assert s.view.alive and s.view.me_id == game.world.player_entity(s.api.player_id).entity_id


def test_busy_when_queue_full(env):
    s, game, _ = env
    results = [s.send("wait") for _ in range(12)]
    assert results.count("queued") == 8 and "busy" in results


def test_run_script_sends_keys(env):
    s, game, _ = env
    sent = []
    orig = s.send
    s.send = lambda t, **a: (sent.append((t, a)), orig(t, **a))[1]
    run_script(s, "d.e?r", step_wait=0, sleep=lambda _: [game.step() for _ in range(2)])
    assert [t for t, _ in sent] == ["move", "wait", "eat", "drink"]       # 未知のキー '?' は無視


def _bot_view(rows_edit=None, me=(5, 5), **stats):
    rows = ["." * 32 for _ in range(32)]
    for (x, y), ch in (rows_edit or {}).items():
        rows[y] = rows[y][:x] + ch + rows[y][x + 1:]
    v = WorldView()
    v.player_id, v.me_id = "pid", "me"
    st = {"hydration": 100, "energy": 100, "health": 100, **stats}
    v.load({"tick": 0, "region_size": 32, "event_seq": 0, "world_id": "w",
            "regions": [{"region": [0, 0], "origin": [0, 0], "size": 32, "terrain": rows,
                         "entities": [{"id": "me", "species": "Player", "name": "me", "pos": list(me),
                                       "player_id": "pid", **st}]}]})
    v.stats = {k: int(st[k]) for k in ("hydration", "energy", "health")}
    return v


class _S:
    def __init__(self, view):
        self.view, self.dt = view, 0.2


def test_bot_decisions():
    # 喉が渇いていて水が隣 → drink
    v = _bot_view({(6, 5): "~"}, hydration=10)
    assert Bot(_S(v), 1).decide() == ("drink", {})
    # 喉が渇いていて水が遠い → 水の方へ移動
    v = _bot_view({(9, 5): "~"}, hydration=10)
    assert Bot(_S(v), 1).decide() == ("move", {"dir": "e"})
    # 空腹で獲物が同じセル → eat
    v = _bot_view(energy=30)
    v._add({"id": "r", "species": "Rabbit", "name": "r", "pos": [5, 5]})
    assert Bot(_S(v), 1).decide() == ("eat", {})
    # 空腹で獲物が離れている → 近づく
    v = _bot_view(energy=30)
    v._add({"id": "r", "species": "Rabbit", "name": "r", "pos": [5, 9]})
    assert Bot(_S(v), 1).decide() == ("move", {"dir": "s"})
    # 死亡 → respawn
    v = _bot_view()
    v.alive = False
    assert Bot(_S(v), 1).decide() == ("respawn", {})
    # 壁(岩)には進まない
    v = _bot_view({(6, 5): "#", (4, 5): "#", (5, 4): "#"})
    assert Bot(_S(v), 1).decide() == ("move", {"dir": "s"})


def test_bot_plays_against_live_loop():
    from fastapi.testclient import TestClient
    from ecologrid.api.main import create_app
    from ecologrid.api.settings import Settings
    from ecologrid_cli.api import ApiClient
    from ecologrid_cli.session import Session

    app = create_app(Settings(regions_x=2, regions_y=2, autotick=True, tick_interval_ms=20))
    with TestClient(app) as c:
        s = Session(ApiClient(http=c), "bot")
        s.start()
        counts = Bot(s, 3).run(1.5, move_interval=0.05)
        assert sum(counts.values()) > 5 and counts["move"] > 0
        assert view_state(s.view) is not None
        s.close()


def test_game_time_follows_server(env):
    s, game, _ = env
    assert s.view.game_time["text"] == "Day 1 00:00:00" and s.view.game_time["planet"] == "earth"
    for _ in range(61):
        game.step()
    s.poll(0)
    assert s.view.tick == 61 and s.view.game_time["text"] == "Day 1 00:01:01"      # 1 tick = 1 秒
    s.view.time_format = "tick"
    assert s.view.time_label() == "Tick 61"


def test_run_script_t_key_toggles_time_format(env):
    s, game, _ = env
    sent = []
    s.send = lambda t, **a: sent.append(t)
    assert s.view.time_format == "clock"
    run_script(s, "t.t", step_wait=0, sleep=lambda _: None)
    assert s.view.time_format == "clock" and sent == ["wait"]          # t は 2 回(切り替えて戻る)。コマンドは送らない
    run_script(s, "t", step_wait=0, sleep=lambda _: None)
    assert s.view.time_format == "tick"


def test_step_seconds_uses_move_interval_and_tick_length(env):
    s, game, _ = env
    assert abs(s.step_seconds("Player") - 5 * 0.05) < 1e-9             # 5 tick × 50 ms
    assert abs(s.step_seconds("Rabbit") - 20 * 0.05) < 1e-9


def test_bot_sends_commands_at_move_interval_not_every_poll():
    """コマンドは 1 歩にかかる時間(Player は 5 tick × 1 tick の長さ)ごとに 1 つ。イベントが来るたびには送らない。"""
    from fastapi.testclient import TestClient
    from ecologrid.api.main import create_app
    from ecologrid.api.settings import Settings
    from ecologrid_cli.api import ApiClient
    from ecologrid_cli.session import Session

    app = create_app(Settings(regions_x=2, regions_y=2, autotick=True, tick_interval_ms=20))
    with TestClient(app) as c:
        s = Session(ApiClient(http=c), "bot")
        s.start()
        interval = s.step_seconds("Player")                  # 5 tick × 20 ms = 0.1 秒
        assert abs(interval - 0.1) < 1e-9
        counts = Bot(s, 3).run(1.5)
        total = sum(counts.values())
        assert 6 <= total <= 1.5 / interval + 3, total        # 約 15 回(毎 poll に送ると数百回になる)
        s.close()


def test_bot_sleep_and_rest_decisions():
    # 眠っている間はコマンドを送らない
    v = _bot_view()
    v.vitals["state"] = "sleeping"
    assert Bot(_S(v), 1).decide() is None
    # スタミナが低ければ休憩する。休憩中で満タンでなければ続ける。満タンなら通常の行動(移動など)で休憩を終える
    v = _bot_view()
    v.vitals["stamina"] = 10
    assert Bot(_S(v), 1).decide() == ("rest", {})
    v.vitals["resting"] = True
    assert Bot(_S(v), 1).decide() is None
    v.vitals["stamina"] = 100
    assert Bot(_S(v), 1).decide()[0] in ("move", "wait")
    # 喉が渇いていれば、疲れていても水を優先する
    v = _bot_view({(6, 5): "~"}, hydration=10)
    v.vitals["stamina"] = 10
    assert Bot(_S(v), 1).decide() == ("drink", {})


def test_rest_key_sends_rest_command(env):
    s, game, _ = env
    from ecologrid_cli.play import key_to_command
    assert key_to_command("z") == ("rest", {})
    s.send("rest")
    game.step()
    game.step()
    s.poll(0)
    assert s.view.vitals["resting"] is True


def test_session_uses_only_the_perception_view_and_player_apis(env):
    import pytest
    from ecologrid_cli.api import ApiError
    s, game, _ = env
    ent = game.world.player_entity(s.api.player_id)
    r = ent.effective_range(game.world.regions[game.world.players[s.api.player_id].region])
    assert s.view.view_r == r and len(s.view.win) == (2 * r + 1) ** 2         # 窓の大きさ = (2r+1)²(範囲の外は持たない)
    with pytest.raises(ApiError) as e:
        s.api.snapshot("no-key")                                              # 世界全体は、PC には返らない
    assert e.value.status == 403
    assert "long" in s.api.memory(kind="all") and s.api.map()["known_cells"] >= 0
    a = s.api.advice()
    assert a["kind"] in ("drink", "eat", "move", "wander", "rest_start", "rest_continue") and "materials" in a


def test_window_follows_the_player_when_moving(env):
    s, game, _ = env
    ent = game.world.player_entity(s.api.player_id)
    ent.wakefulness = ent.wakefulness_max * 0.3                                # r = 6
    s.refresh_status()
    before = set(s.view.win)
    for d in "esnw":
        s.send("move", dir=d)
        for _ in range(6):
            game.step()
        s.poll(0)
        if set(s.view.win) != before:
            break
    c = game.world.player_entity(s.api.player_id).grid_position
    assert s.view.me_pos == (c.x, c.y) and (c.x, c.y) in s.view.win            # 窓が、新しい位置に追従した


def test_config_screen_asks_again_when_name_is_taken(env):
    from ecologrid_cli.api import ApiClient
    from ecologrid_cli.play import config_screen, start_interactive
    from ecologrid_cli.session import Session
    s, game, c = env
    other = Session(ApiClient(http=c), "bob")
    other.start()
    answers = iter(["bob", "carol"])                      # 使用中 → 再入力
    out = []
    assert config_screen(s, input_fn=lambda _: next(answers), out=out.append) is True
    assert any("already used" in str(l) for l in out)
    assert s.name == "carol" and game.world.players[s.api.player_id].name == "carol"
    # 起動時に使用中なら、名前の変更画面で再設定させる(再設定でも重複チェック)
    dup = Session(ApiClient(http=c), "bob")
    answers = iter(["bob", "carol", "dave"])
    out = []
    assert start_interactive(dup, input_fn=lambda _: next(answers), out=out.append) is True
    assert dup.name == "dave" and sum("already used" in str(l) for l in out) == 3
    # 空入力で中止
    dup2 = Session(ApiClient(http=c), "dave")
    assert start_interactive(dup2, input_fn=lambda _: "", out=lambda *_: None) is False
