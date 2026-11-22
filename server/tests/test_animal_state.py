"""動物の状態(睡眠・覚醒度・食事可能・スタミナ・休憩・気温)。docs/spec/animal-states-spec.md。"""
from dataclasses import replace

import pytest

from ecologrid.core import factory
from ecologrid.core.grid import Pos
from ecologrid.core.messages import Command
from ecologrid.core.planets import Planet
from ecologrid.core.species import SPECIES
from helpers import empty_world, land_run, region_for


def _setup(species="Rabbit", **kw):
    w = empty_world(**kw)
    pos = land_run(w, 6)
    r = region_for(w, pos)
    a = r.spawn(species, pos, **({"player_id": "p"} if species == "Player" else {}))
    return w, r, a, pos


def _tick(r, a, n=1, **env):
    for _ in range(n):
        r.tick_no += 1
        a.tick(r)


# --- 能力値 ---
def test_abilities_from_species_table():
    for sp, (st, mind, atk, dfn, per, wmax) in {"Player": (50, 50, 50, 40, 2.0, 100), "Rabbit": (10, 20, 5, 3, 5.0, 100),
                                                "Fox": (35, 25, 42, 21, 4.0, 100)}.items():
        _, _, a, _ = _setup(sp)
        assert (a.strength, a.mind, a.max_health) == (st, mind, 100)
        assert a.attack_power == pytest.approx(atk) and a.defense_power == pytest.approx(dfn)
        assert a.wakefulness_max == pytest.approx(wmax) and SPECIES[sp].wakefulness_per_mind == per


def test_initial_wakefulness_random_30_to_100_percent_and_deterministic():
    w, r, a, pos = _setup()
    assert 0.3 * 100 <= a.wakefulness <= 100
    vals = {r.spawn("Rabbit", pos).wakefulness for _ in range(20)}
    assert len(vals) == 20 and all(30 <= v <= 100 for v in vals)          # 個体ごとに散る
    w2, r2, a2, _ = _setup()
    assert a2.wakefulness == a.wakefulness and a2.entity_id == a.entity_id  # 同じ seed なら同じ


# --- 覚醒度 ---
def test_wakefulness_decays_constantly_awake_regardless_of_action_and_health():
    w, r, a, pos = _setup("Rabbit")
    a.wakefulness = 100.0
    d = SPECIES["Rabbit"].wakefulness_decay_per_tick
    assert d == pytest.approx(100 / 91_800)
    a.health, a.max_health = 40.0, 40.0       # 体力が低くても、覚醒度の減りは同じ
    a.next_action_tick = 10**9                 # AI は動かさない
    _tick(r, a, 10)
    assert a.wakefulness == pytest.approx(100 - 10 * d)
    a.last_action_tick = r.tick_no             # 行動した直後でも、休憩中でも減り方は同じ
    before = a.wakefulness
    _tick(r, a, 5)
    assert a.wakefulness == pytest.approx(before - 5 * d)
    a.resting = True
    before = a.wakefulness
    _tick(r, a, 5)
    assert a.wakefulness == pytest.approx(before - 5 * d)                  # 休憩中も減る


def test_wakefulness_recovers_when_sleeping_and_caps():
    w, r, a, _ = _setup("Rabbit")
    a.set_consciousness("asleep")
    a.wakefulness = 50.0
    rec = SPECIES["Rabbit"].wakefulness_recovery_per_tick
    assert rec == pytest.approx(100 / 41_400)
    _tick(r, a, 100)
    assert a.wakefulness == pytest.approx(50 + 100 * rec)
    a.wakefulness = 99.999
    _tick(r, a, 5)
    assert a.wakefulness == pytest.approx(100.0)


def test_faint_at_zero_and_regain_to_sleeping():
    w, r, a, _ = _setup("Rabbit")
    a.next_action_tick = 10**9
    a.wakefulness = SPECIES["Rabbit"].wakefulness_decay_per_tick * 0.5
    r.tick_no = 777
    _tick(r, a)
    assert a.consciousness == "unconscious" and a.wakefulness == 0.0
    assert a.sleep_onset_history == [778]
    assert any(e["type"] == "fainted" for e in r.events)
    a.sleep_check(r, 1_200)                                                 # 意識消失中は起床の判定をしない
    assert a.consciousness == "unconscious"
    _tick(r, a, int(10 / SPECIES["Rabbit"].wakefulness_recovery_per_tick) + 2)   # 最大値の 10% に達する
    assert a.consciousness == "asleep"


def test_sleep_history_keeps_last_five():
    w, r, a, _ = _setup("Fox")
    for t in range(1, 9):
        r.tick_no = t * 1_000
        a.set_consciousness("awake")
        a.fall_asleep(r)
    assert a.sleep_onset_history == [4_000, 5_000, 6_000, 7_000, 8_000]


# --- 入眠・起床の判定 ---
def _frequency(a, r, n, state, **spec_kw):
    """sleep_check を n 回(tick を変えて)行い、状態が切り替わった割合。"""
    a.spec = replace(a.spec, **spec_kw)
    hits = 0
    for t in range(1, n + 1):
        a.set_consciousness(state)
        a.sleep_check(r, t * 600)
        hits += a.consciousness != state
    return hits / n


def test_sleep_check_matches_expected_probability():
    w, r, a, _ = _setup("Rabbit")
    a.wakefulness = 70.0                                # x = 0.7 → 入眠圧 0.3(周期要素なし、係数 1、n = 1)
    p = _frequency(a, r, 4000, "awake", sleep_cycle_amplitude=0.0, sleep_probability_gain=1.0,
                   sleep_pressure_exponent=1.0)
    assert p == pytest.approx(0.3, abs=0.03)


def test_wake_check_matches_wake_probability():
    w, r, a, _ = _setup("Rabbit")
    a.wakefulness = 90.0                                # x = 0.9 → 起床確率 14.8%
    p = _frequency(a, r, 4000, "asleep")
    assert p == pytest.approx(0.148, abs=0.03)
    a.wakefulness = 60.0
    assert _frequency(a, r, 500, "asleep") == 0.0     # 覚醒度が低いと起きない


def test_certain_sleep_when_probability_is_one():
    w, r, a, _ = _setup("Rabbit")
    a.wakefulness = 0.001                                # x ≈ 0 → 入眠圧 ≥ 1.0。周期の頂点(標準入眠時刻)なら 1.3 で必ず入眠
    a.set_consciousness("awake")
    r.tick_no = SPECIES["Rabbit"].sleep_standard_onset_tick
    a.sleep_check(r, r.tick_no)
    assert a.consciousness == "asleep" and a.sleep_onset_history == [SPECIES["Rabbit"].sleep_standard_onset_tick]
    assert any(e["type"] == "fell_asleep" for e in r.events)


def test_no_sleep_when_unsafe_or_starving():
    w, r, a, _ = _setup("Rabbit")
    a.wakefulness = 0.001
    tick = SPECIES["Rabbit"].sleep_standard_onset_tick
    a.state["in_combat"] = True
    a.sleep_check(r, tick)
    assert a.consciousness == "awake"                            # 戦闘状態では入眠しない
    a.state["in_combat"] = False
    a.state["falling"] = True
    a.sleep_check(r, tick)
    assert a.consciousness == "awake"                            # 落下中も
    a.state["falling"] = False
    a.energy = 0                                         # 飢えレベル 1 → 入眠圧 0
    a.sleep_check(r, tick)
    assert a.consciousness == "awake"
    a.energy = 100
    a.sleep_check(r, tick)
    assert a.consciousness == "asleep"


def test_sleep_peak_follows_recorded_onsets():
    """記録された入眠時刻(最頻値)が、睡眠リズムの頂点になる。"""
    w, r, a, _ = _setup("Fox")
    a.wakefulness = 10.0                                  # x = 0.1 → 入眠圧 0.9 + 周期要素(頂点で 1.2、反対側で 0.9)

    def hits(clock_hour, n=400):
        count = 0
        for k in range(1, n + 1):
            a.set_consciousness("awake")
            a.sleep_onset_history = [10 * 3_600] * 4     # 習慣の入眠時刻は 10 時
            r.tick_no = clock_hour * 3_600 + k * DAY
            a.sleep_check(r, r.tick_no)
            count += a.consciousness == "asleep"
        return count / n

    assert hits(10) == 1.0            # 頂点(10 時): 入眠圧 1.2 ≥ 1.122 → 必ず入眠
    assert hits(22) < 0.05            # 反対側(22 時): 入眠圧 0.9 → 約 1.2%
    assert hits(10) > hits(16) > hits(22) - 1e-9


DAY = 86_400


# --- 食事が可能・自動捕食 ---
def _fox_with_rabbit():
    w = empty_world()
    pos = land_run(w, 6)
    r = region_for(w, pos)
    fox = r.spawn("Fox", pos)
    rabbit = r.spawn("Rabbit", pos)
    return w, r, fox, rabbit


def test_can_eat_conditions():
    w, r, a, _ = _setup("Fox")
    assert a.can_eat()
    a.set_consciousness("asleep")
    assert not a.can_eat()
    a.set_consciousness("unconscious")
    assert not a.can_eat()
    a.set_consciousness("awake")
    a.energy = 0
    assert not a.can_eat()                               # Energy 0 は体が動かない
    a.energy = 50
    a.stamina = SPECIES["Fox"].stamina_cost_eat - 1e-6
    assert not a.can_eat()                               # スタミナが捕食の消費に足りない
    a.stamina = 50
    a.state["in_combat"] = True
    assert not a.can_eat()
    a.state["in_combat"] = False
    a.state["falling"] = True
    assert not a.can_eat()
    a.state["falling"] = False
    assert a.can_eat() and a.is_safe()


def test_npc_auto_eat_only_when_hungry_and_can_eat():
    w, r, fox, rabbit = _fox_with_rabbit()
    fox.energy = 100                                      # 空腹でない → 食べない
    fox.after_enter(r)
    assert rabbit.entity_id in r.entities
    fox.energy = 20                                       # 空腹 → 食べる
    fox.after_enter(r)
    assert rabbit.entity_id not in r.entities and fox.energy > 20


@pytest.mark.parametrize("setup", ["asleep", "unconscious", "combat", "falling", "tired"])
def test_npc_does_not_eat_when_cannot(setup):
    w, r, fox, rabbit = _fox_with_rabbit()
    fox.energy = 20
    if setup in ("asleep", "unconscious"):
        fox.set_consciousness(setup)
    elif setup == "combat":
        fox.state["in_combat"] = True
    elif setup == "falling":
        fox.state["falling"] = True
    else:
        fox.stamina = 0.0
    fox.after_enter(r)
    assert rabbit.entity_id in r.entities


def test_eating_costs_stamina_and_marks_action():
    w, r, fox, rabbit = _fox_with_rabbit()
    fox.energy = 20
    r.tick_no = 500
    s0 = fox.stamina
    assert fox.eat_here(r)
    assert fox.stamina == pytest.approx(s0 - 0.025) and fox.last_action_tick == 500


def test_sleeping_npc_does_not_think_or_move():
    w, r, a, pos = _setup("Rabbit")
    a.set_consciousness("asleep")
    a.wakefulness = 30.0
    a.energy = 10                                         # 空腹でも動かない
    start = a.grid_position
    r.tick_no = 100_000
    a.next_action_tick = 0
    for _ in range(100):
        r.tick_no += 20
        a.tick(r)
    assert a.grid_position == start and a.energy < 10      # 代謝は続く


# --- スタミナ・体力の増減表 ---
def test_action_costs_and_move_needs_stamina():
    w, r, a, pos = _setup("Player")
    s = a.spec
    assert (s.stamina_cost_move, s.stamina_cost_eat, s.stamina_cost_drink) == (0.005, 0.025, 0.01)
    a.stamina = 50.0
    assert a.try_step("e", r) and a.stamina == pytest.approx(50 - 0.005)
    a.stamina = 0.004
    r.tick_no += 100
    assert not a.try_step("e", r)                          # 足りない(息切れ)
    assert a.stamina == 0.004


def test_state_table_health_and_stamina_rates():
    w, r, a, _ = _setup("Player")
    s = a.spec
    a.next_action_tick = 10**9
    base_regen = 100 / 129_600
    rest_regen = 100 / 28_800
    assert base_regen == pytest.approx(0.000772, abs=1e-6) and rest_regen == pytest.approx(0.003472, abs=1e-6)

    def step(mode):
        a.health, a.stamina = 50.0, 20.0
        a.set_consciousness("awake")
        a.resting = False
        a.last_action_tick = -10**9
        a.wakefulness = 60.0
        if mode == "acting":
            a.last_action_tick = r.tick_no + 1
        elif mode == "resting":
            a.resting = True
        elif mode in ("asleep", "unconscious"):
            a.set_consciousness(mode)
        a.energy = a.hydration = 100.0
        r.tick_no += 1
        a.tick(r)
        return a.health - 50.0, a.stamina - 20.0

    h, st = step("acting")
    assert h == 0 and st == 0                                           # 動作中: 回復しない
    h, st = step("idle")
    assert h == pytest.approx(base_regen) and st == pytest.approx(10 * base_regen)   # 静止: 体力 + 0.000772、スタミナ + 0.007716
    for mode in ("resting", "asleep", "unconscious"):
        h, st = step(mode)
        assert h == pytest.approx(rest_regen) and st == pytest.approx(10 * rest_regen), mode   # 0.003472 / 0.034722


def test_rabbit_rest_regen_is_faster():
    w, r, a, _ = _setup("Rabbit")
    a.set_consciousness("asleep")
    a.wakefulness = 30.0
    a.health = 50.0
    a.stamina = 20.0
    r.tick_no += 1
    a.tick(r)
    assert a.health - 50 == pytest.approx(100 / 25_200)


def test_idle_after_ticks_boundary():
    w, r, a, _ = _setup("Player")
    a.next_action_tick = 10**9
    r.tick_no = 1_000
    a.last_action_tick = 1_000
    a.health = 50.0
    for k in range(59):                                  # 行動から 60 tick 未満は動作中 → 回復なし
        r.tick_no += 1
        a.tick(r)
    assert a.health == 50.0
    r.tick_no += 1                                       # 60 tick 以上 → 静止
    a.tick(r)
    assert a.health > 50.0


def test_deficiency_damages_and_stops_recovery():
    w, r, a, _ = _setup("Player")
    a.next_action_tick = 10**9
    a.stamina, a.health = 20.0, 50.0
    a.energy = 0.0
    r.tick_no += 100
    a.tick(r)
    assert a.health == pytest.approx(50 - 100 / 32_400)    # 欠乏 1 つ: − 0.003086
    assert a.stamina <= a.health and a.stamina == pytest.approx(20.0)   # スタミナは回復しない
    a.hydration = 0.0
    h = a.health
    r.tick_no += 1
    a.tick(r)
    assert a.health == pytest.approx(h - 2 * 100 / 32_400)  # 両方なら 2 倍


def test_stamina_never_exceeds_health():
    w, r, a, _ = _setup("Player")
    a.next_action_tick = 10**9
    a.health = 30.0
    a.stamina = 100.0
    a.last_action_tick = -10**9
    r.tick_no += 1
    a.tick(r)
    assert a.stamina <= a.health
    for _ in range(2_000):                                  # 回復しても体力値で頭打ち
        r.tick_no += 1
        a.tick(r)
        assert a.stamina <= a.health + 1e-9
    assert a.stamina == pytest.approx(a.health)


# --- 気温 ---
def test_temperature_stamina_drain_and_comfort_zone():
    w, r, a, _ = _setup("Player")
    a.next_action_tick = 10**9
    a.last_action_tick = r.tick_no + 10**9                  # 動作中(回復なし)にして消費だけ見る
    for temp, drain in {10.0: 0, 20.0: 0, 30.0: 0, 40.0: 0.005, 0.0: 0.005, 50.0: 0.010, -10.0: 0.010}.items():
        r.temperature_c = temp
        a.stamina = 50.0
        r.tick_no += 1
        a.tick(r)
        assert 50.0 - a.stamina == pytest.approx(drain), temp


def test_temperature_net_stamina_is_recovery_minus_drain():
    w, r, a, _ = _setup("Player")
    a.next_action_tick = 10**9
    r.temperature_c = 50.0
    a.health = 100.0
    # 静止: 0.007716 − 0.010 = −0.002284(減る)。休憩: 0.034722 − 0.010 = + 0.024722
    a.stamina = 50.0
    a.set_consciousness("awake")
    a.resting = False
    a.last_action_tick = -10**9
    r.tick_no += 1
    a.tick(r)
    assert a.stamina - 50 == pytest.approx(100 / 129_600 * 10 - 0.010)
    a.stamina, a.resting = 50.0, True
    r.tick_no += 1
    a.tick(r)
    assert a.stamina - 50 == pytest.approx(100 / 28_800 * 10 - 0.010)


def test_comfort_ranges_by_species():
    S = SPECIES
    assert (S["Player"].comfort_low_c, S["Player"].comfort_high_c) == (10, 30)
    assert (S["Rabbit"].comfort_low_c, S["Rabbit"].comfort_high_c) == (5, 28)
    assert (S["Fox"].comfort_low_c, S["Fox"].comfort_high_c) == (0, 28)


def test_planet_temperature_constant_and_daily_cycle():
    from ecologrid.core.planets import EARTH
    assert all(EARTH.temperature_at(t) == 20.0 for t in (0, 12_345, 99_999))
    p = Planet("hot", "Hot", day_length_ticks=86_400, start_offset_ticks=0, gravity=0.01,
               temperature_c=20.0, temperature_amplitude_c=10.0)
    assert p.temperature_at(50_400) == pytest.approx(30.0)               # 14:00 が最高
    assert p.temperature_at(50_400 + 43_200) == pytest.approx(10.0)      # 02:00 が最低
    assert p.temperature_at(50_400 + 86_400) == pytest.approx(30.0)


def test_region_updates_temperature_each_tick():
    w = empty_world()
    r = w.regions[(0, 0)]
    r.planet = Planet("hot", "Hot", 86_400, 0, 0.01, temperature_c=20.0, temperature_amplitude_c=10.0)
    r.tick([], 50_400)
    assert r.temperature_c == pytest.approx(30.0)
    assert r.snapshot()["temperature_c"] == pytest.approx(30.0)


# --- 休憩 ---
def test_rest_command_and_end_by_other_commands():
    w = empty_world()
    pid = w.add_player("p")
    p = w.player_entity(pid)
    r = w.regions[w.players[pid].region]

    def run(cmd, **args):
        w.submit(Command(f"c{len(w._seen)}", pid, cmd, args))
        w.tick()

    run("rest")
    assert p.resting and any(e["type"] == "rest_started" for e in w._events)
    run("move", dir="e")
    assert not p.resting and any(e["type"] == "rest_ended" for e in w._events)


def test_rest_rejected_when_unsafe():
    w = empty_world()
    pid = w.add_player("p")
    p = w.player_entity(pid)
    p.state["in_combat"] = True
    w.submit(Command("r1", pid, "rest", {}))
    w.tick()
    assert not p.resting
    assert any(e["type"] == "command_rejected" and e["reason"] == "unsafe" for e in w._events)


def test_player_commands_rejected_when_asleep_or_unconscious_or_exhausted():
    w = empty_world()
    pid = w.add_player("p")
    p = w.player_entity(pid)

    def reasons():
        return [e["reason"] for e in w._events if e["type"] == "command_rejected"]

    p.set_consciousness("asleep")
    for i, t in enumerate(("move", "eat", "drink", "rest")):
        w.submit(Command(f"s{i}", pid, t, {"dir": "e"}))
        w.tick()
    assert reasons() == ["asleep"] * 4
    p.set_consciousness("unconscious")
    w.submit(Command("u", pid, "drink", {}))
    w.tick()
    assert reasons()[-1] == "unconscious"
    p.set_consciousness("awake")
    p.wakefulness = 100.0
    for i, t in enumerate(("move", "eat", "drink")):
        p.stamina = 0.0                                    # 静止の回復があるので、毎回 0 にする
        w.submit(Command(f"x{i}", pid, t, {"dir": "e"}))
        w.tick()
    assert reasons()[-3:] == ["exhausted"] * 3


# --- NPC の休憩 ---
def test_npc_rests_when_low_stamina_and_safe_then_stops():
    w, r, a, _ = _setup("Rabbit")
    a.wakefulness = 80.0
    a.stamina = 10.0                                       # 30% 未満
    a.energy = a.hydration = 100.0
    a.next_action_tick = 0
    r.tick_no = 1_000
    a.tick(r)
    assert a.resting
    a.stamina = a.health                                   # 満タン
    a.next_action_tick = 0
    r.tick_no += 1
    a.tick(r)
    assert not a.resting
    # 空腹・渇きでは、疲れていても休憩を始めず(動けないほどでなければ)、休憩中なら終える
    a.stamina, a.energy = 10.0, 10.0
    a.next_action_tick = 0
    r.tick_no += 1
    a.tick(r)
    assert not a.resting


def test_npc_rest_not_started_when_unsafe_and_ends_when_unsafe():
    w, r, a, _ = _setup("Rabbit")
    a.wakefulness = 80.0
    a.state["in_combat"] = True
    a.stamina = 10.0
    a.next_action_tick = 0
    r.tick_no = 1_000
    a.tick(r)
    assert not a.resting
    a.state["in_combat"] = False
    a.resting = True
    a.state["in_combat"] = True
    r.tick_no += 1
    a.tick(r)
    assert not a.resting                                    # 安全でなくなると休憩を終える


# --- シリアライズ(Region 越境) ---
def test_state_roundtrips_through_to_dict_and_restore():
    w, r, a, _ = _setup("Fox")
    a.set_consciousness("asleep")
    a.resting = False
    a.wakefulness = 42.5
    a.stamina = 33.3
    a.sleep_onset_history = [100, 200, 300]
    a.last_action_tick = 777
    a.state["in_combat"] = True
    d = a.to_state()
    for k in ("state", "wakefulness", "wakefulness_max", "stamina", "stamina_max", "mind", "strength", "max_health", "resting"):
        assert k in d
    b = factory.restore(d)
    assert (b.consciousness, b.wakefulness, b.stamina) == ("asleep", 42.5, 33.3)
    assert b.sleep_onset_history == [100, 200, 300] and b.last_action_tick == 777 and b.state.get("in_combat")
    r.add(b, announce=False)                                # bind が覚醒度を上書きしない
    assert b.wakefulness == 42.5


# --- 設定 ---
def test_player_sleep_can_be_disabled():
    w = empty_world(player_sleep_enabled=False)
    pid = w.add_player("p")
    p = w.player_entity(pid)
    for _ in range(3_000):
        w.tick()
    assert p.consciousness == "awake" and p.wakefulness == pytest.approx(p.wakefulness_max * 1.0, rel=1) and not p.sleep_onset_history
    w2 = empty_world()
    pid2 = w2.add_player("p")
    p2 = w2.player_entity(pid2)
    w2_start = p2.wakefulness
    for _ in range(3_000):
        w2.tick()
    assert p2.wakefulness != w2_start                       # 既定では覚醒度が動く


def test_sleep_actions_registered_and_sleep_check_runs_periodically():
    w = empty_world()
    ids = {a.action_id for a in w.actions.entity_actions("global")}
    assert {"sleep_check:Rabbit", "sleep_check:Fox", "sleep_check:Player"} <= ids
    pos = land_run(w, 4)
    r = region_for(w, pos)
    a = r.spawn("Rabbit", pos, init={"wakefulness": 0.001, "birth_tick": 0})
    a.next_action_tick = 10**9
    seen = []
    orig = type(a).sleep_check
    type(a).sleep_check = lambda self, region, t: (seen.append(t), orig(self, region, t))[1]
    try:
        for _ in range(1_300):
            w.tick()
    finally:
        type(a).sleep_check = orig
    assert seen[:2] == [600, 1_200]                         # 600 tick ごと


def test_config_validation_of_sleep_params():
    from ecologrid.core import species as sp_mod
    from ecologrid.core.config import WorldConfig, validate_config
    assert validate_config(WorldConfig()) == []
    good = sp_mod.SPECIES["Rabbit"]
    for bad in (dict(sleep_probability_gain=0), dict(sleep_pressure_exponent=0.5), dict(sleep_cycle_amplitude=1.5),
                dict(sleep_standard_onset_tick=86_400), dict(comfort_low_c=40.0), dict(wakefulness_decay_ticks=0)):
        sp_mod.SPECIES["Rabbit"] = replace(good, **bad)
        try:
            with pytest.raises(ValueError):
                validate_config(WorldConfig())
        finally:
            sp_mod.SPECIES["Rabbit"] = good


# --- 睡眠の周期(数日間) ---
def test_rabbit_sleep_cycle_over_four_days():
    """4 日分: 睡眠と覚醒が約 1 日周期で繰り返し、眠っている割合は 2〜5 割、意識消失はほぼない。"""
    w = empty_world(seed=3, regions_x=1, regions_y=1)      # 1 Region だけ(4 日分の tick を速く回す)
    pos = land_run(w, 4)
    r = region_for(w, pos)
    a = r.spawn("Rabbit", pos, init={"wakefulness": 80.0, "birth_tick": 0})
    a.next_action_tick = 10**9
    asleep = faints = 0
    total = 4 * 86_400
    for t in range(total):
        w.tick()
        a.energy = a.hydration = 100.0                       # 食べ物・水は十分にある
        asleep += a.consciousness != "awake"
    mine = [e for e in w._events if e.get("id") == a.entity_id]       # 子ウサギの睡眠は数えない
    faints = sum(1 for e in mine if e["type"] == "fainted")
    onsets = sum(1 for e in mine if e["type"] == "fell_asleep")
    assert 0.20 <= asleep / total <= 0.50, asleep / total
    assert 3 <= onsets + faints <= 6, (onsets, faints)
    assert faints <= 1
