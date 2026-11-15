"""進行速度(tick_interval_ms)の変更: 値の検査・稼働中の変更 API・全員向けのイベント・最速モード(plan 21)。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from ecologrid.api.game import GameServer, NOTIFY_MIN_SEC
from ecologrid.api.main import create_app
from ecologrid.api.settings import Settings
from ecologrid.core.config import WorldConfig, check_tick_interval, validate_config

P = "/api/v1"


def make(**kw):
    return TestClient(create_app(Settings(**{"regions_x": 2, "regions_y": 2, "autotick": False, **kw})))


def join(c, name="alice"):
    j = c.post(f"{P}/players", json={"name": name}).json()
    return j["player_id"], {"Authorization": f"Bearer {j['token']}"}


def put(c, h, ms):
    return c.put(f"{P}/world/tick-interval", json={"tick_interval_ms": ms}, headers=h)


def unlock(c):
    c.app.state.game._tick_change_at = -1e9          # 1 秒の制限を解く(テストを速くするため)


# --- 値の検査 ---
@pytest.mark.parametrize("v", [0, 1, 50, 1000])
def test_valid_tick_intervals(v):
    assert check_tick_interval(v) == ""


@pytest.mark.parametrize("v", [-1, 1001, 5.5, True, None, "50"])
def test_invalid_tick_intervals(v):
    assert check_tick_interval(v)
    with pytest.raises(ValueError):
        validate_config(WorldConfig(tick_interval_ms=v))


def test_max_speed_has_no_warning_but_odd_values_do():
    assert not [w for w in validate_config(WorldConfig(tick_interval_ms=0)) if "tick_interval_ms" in w]
    assert [w for w in validate_config(WorldConfig(tick_interval_ms=10)) if "tick_interval_ms" in w]
    assert not [w for w in validate_config(WorldConfig(tick_interval_ms=50)) if "tick_interval_ms" in w]


def test_env_initial_value_accepts_zero(monkeypatch):
    monkeypatch.setenv("ECOLOGRID_TICK_INTERVAL_MS", "0")
    assert Settings.from_env().tick_interval_ms == 0
    assert GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=0)).tick_interval_ms == 0


# --- API ---
def test_any_player_can_change_the_interval_and_everyone_can_read_it():
    with make() as c:
        pid, h = join(c)
        assert c.get(f"{P}/healthz").json()["tick_interval_ms"] == 50
        assert c.get(f"{P}/config").json()["tick"]["tick_interval_ms"] == 50
        lim = c.get(f"{P}/config").json()["limits"]
        assert (lim["tick_interval_ms_min"], lim["tick_interval_ms_max"]) == (0, 1000)
        r = put(c, h, 0)
        assert r.status_code == 200 and r.json() == {"tick_interval_ms": 0, "previous": 50}
        assert c.get(f"{P}/healthz").json()["tick_interval_ms"] == 0
        assert c.get(f"{P}/config").json()["tick"]["tick_interval_ms"] == 0       # 現在値
        unlock(c)
        assert put(c, h, 1000).json() == {"tick_interval_ms": 1000, "previous": 0}


def test_invalid_values_and_missing_token():
    with make() as c:
        pid, h = join(c)
        for bad in (-1, 1001, 5.5, True, "50", None):
            r = put(c, h, bad)
            assert r.status_code == 422, bad
            unlock(c)
        assert c.get(f"{P}/healthz").json()["tick_interval_ms"] == 50               # 失敗しても変わらない
        assert c.put(f"{P}/world/tick-interval", json={"tick_interval_ms": 10}).status_code == 401


def test_changes_are_rate_limited_to_once_per_second_across_players():
    with make() as c:
        a, ha = join(c, "alice")
        b, hb = join(c, "bob")
        assert put(c, ha, 20).status_code == 200
        r = put(c, hb, 30)                                   # 別のプレイヤーでも、全体で 1 秒に 1 回
        assert r.status_code == 429 and r.json()["detail"]["code"] == "tick_interval_rate_limited"
        assert int(r.headers["Retry-After"]) >= 1
        assert c.get(f"{P}/healthz").json()["tick_interval_ms"] == 20
        unlock(c)
        assert put(c, hb, 30).status_code == 200


def test_permission_check_is_a_single_replaceable_hook(monkeypatch):
    with make() as c:
        pid, h = join(c)
        monkeypatch.setattr(GameServer, "can_change_tick_interval", lambda self, p: False)
        r = put(c, h, 10)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "tick_interval_forbidden"


def test_same_value_is_accepted_without_event():
    with make() as c:
        pid, h = join(c)
        seq = c.app.state.game.world.last_seq
        assert put(c, h, 50).json() == {"tick_interval_ms": 50, "previous": 50}
        assert c.app.state.game.world.last_seq == seq


# --- 全員に届くイベント ---
def test_change_event_reaches_every_player_regardless_of_range():
    with make() as c:
        a, ha = join(c, "alice")
        b, hb = join(c, "bob")
        game = c.app.state.game
        # bob を、alice から遠い Region へ(知覚の範囲の外)
        ea, eb = game.world.player_entity(a), game.world.player_entity(b)
        seq = game.world.last_seq
        assert put(c, ha, 0).status_code == 200
        for pid, h in ((a, ha), (b, hb)):
            evs = c.get(f"{P}/players/{pid}/events", params={"since": seq}, headers=h).json()["events"]
            ch = [e for e in evs if e["type"] == "tick_interval_changed"]
            assert len(ch) == 1 and ch[0]["name"] == "alice" and ch[0]["previous"] == 50 and ch[0]["tick_interval_ms"] == 0
        # 範囲外に置いても届く(死亡中のプレイヤーにも)
        ev = [e for e in game.world.events_since(seq)[0] if e["type"] == "tick_interval_changed"][0]
        assert game.world.visible_events("nobody", [ev]) == [ev]


# --- 最速モードのループ ---
def _run_loop(game, seconds, then=None):
    async def go():
        t = asyncio.ensure_future(game.run())
        await asyncio.sleep(seconds)
        if then:
            then()
            await asyncio.sleep(seconds)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    asyncio.run(go())


def test_max_speed_runs_far_more_ticks_without_counting_as_behind():
    slow = GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=50))
    fast = GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=0))
    _run_loop(slow, 0.4)
    _run_loop(fast, 0.4)
    assert slow.world.tick_no <= 12
    assert fast.world.tick_no >= 3 * max(slow.world.tick_no, 1) and fast.world.tick_no >= 30
    assert fast.behind_count == 0


def test_changing_the_interval_while_running_takes_effect_and_can_go_back():
    g = GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=50))
    pid, _ = g.join("alice")
    marks = {}

    def to_fast():
        marks["t0"] = g.world.tick_no
        g.set_tick_interval(pid, 0)

    _run_loop(g, 0.3, then=to_fast)
    gained_fast = g.world.tick_no - marks["t0"]
    assert marks["t0"] <= 8 and gained_fast >= 30                       # 0.3 秒で、50 ms 刻みより、ずっと多い
    g._tick_change_at = -1e9
    g.set_tick_interval(pid, 50)
    assert g._rebase is True
    before = g.world.tick_no
    _run_loop(g, 0.3)
    assert g.world.tick_no - before <= 8                                # 50 ms に戻った


def test_long_poll_wakeups_are_coalesced_when_interval_is_short(monkeypatch):
    g = GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=0))
    n = {"notify": 0}
    orig = g._notify

    def counting():
        n["notify"] += 1
        orig()

    monkeypatch.setattr(g, "_notify", counting)
    _run_loop(g, 0.4)
    assert g.world.tick_no >= 30
    assert n["notify"] <= 0.4 / NOTIFY_MIN_SEC + 4                      # 実時間で 20 ms に 1 回まで(+ 余裕)
    assert n["notify"] < g.world.tick_no / 2


def test_speed_does_not_change_the_simulation_result():
    """速さ(実時間の待ち)は、世界の状態に影響しない: 同じ seed・同じ tick 数なら、同じ状態。"""
    a = GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=50))
    b = GameServer(Settings(regions_x=2, regions_y=2, autotick=False, tick_interval_ms=0))
    pa, _ = a.join("alice")
    pb, _ = b.join("alice")
    for i in range(300):
        if i == 100:
            b.set_tick_interval(pb, 1000)
        a.step()
        b.step()
    sa, sb = a.world.snapshot(), b.world.snapshot()
    assert sa["tick"] == sb["tick"] and a.world.census() == b.world.census()
    assert [r["entities"] for r in sa["regions"]] == [r["entities"] for r in sb["regions"]]
