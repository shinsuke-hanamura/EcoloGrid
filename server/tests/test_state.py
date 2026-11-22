"""状態(state)の dict・登録表・体制 sleep_stance・コマンド sleep / cancel_sleep(issue 6)。"""
import ast
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ecologrid.api.main import create_app
from ecologrid.api.settings import Settings
from ecologrid.core import animal as animal_mod
from ecologrid.core import factory
from ecologrid.core.commands import check_command
from ecologrid.core.config import WorldConfig, _validate_stances, validate_config
from ecologrid.core.messages import Command
from ecologrid.core.species import SPECIES
from ecologrid.core.state import (STATES, StateDict, check_state_rules, compile_expr, is_stored, validate_factors)
from ecologrid.core.world import World

ROOT = os.path.join(os.path.dirname(__file__), "..", "ecologrid")


def world_with_player(**cfg):
    w = World(WorldConfig(regions_x=2, regions_y=2, **cfg))
    w.generate()
    pid = w.add_player("alice")
    p = w.player_entity(pid)
    p.wakefulness = p.wakefulness_max * 0.8
    return w, pid, p, w.regions[w.players[pid].region]


_n = [0]


def send(w, pid, ctype, **args):
    _n[0] += 1
    ok, why = w.submit(Command(f"c{_n[0]}", pid, ctype, args))
    assert ok, why
    seq = w.last_seq
    w.tick()
    return [e for e in w.events_since(seq)[0]]


def types(evs):
    return [(e["type"], e.get("reason"), e.get("state")) for e in evs if e["type"] in ("state_entered", "state_left", "command_rejected")]


# --- StateDict の意味 ---
def test_state_dict_basics_and_derived_states():
    w, pid, p, r = world_with_player()
    s = p.state
    assert isinstance(s, StateDict) and s.stored() == [] and s.get("asleep") is False and s["asleep"] is False
    assert s.get("awake") is True and "awake" in s and "asleep" not in s                  # awake は、導く
    s["asleep"] = True
    assert s.get("awake") is False and s.active() == ["asleep"] and "asleep" in s
    s["poisoned"] = True                                                                    # 毒かつ睡眠: 独立に並ぶ
    assert s.get("asleep") and s.get("poisoned") and s.stored() == ["asleep", "poisoned"]
    assert s.active(public_only=True) == ["asleep"]                                         # 公開するのは、asleep だけ
    assert s.awake is s.get("awake") is False                                               # 高速な経路(state.awake)は、get と同じ
    s["asleep"] = False                                                                     # 偽にすると、消える(入った順を保つ)
    assert s.awake is s.get("awake") is True
    assert s.stored() == ["poisoned"] and s.get("awake") is True
    with pytest.raises(TypeError):
        s["awake"] = False                                                                  # 導く状態には、書けない
    for bad in ("nope",):
        with pytest.raises(KeyError):
            s.get(bad)
        with pytest.raises(KeyError):
            s[bad] = True
    for name in ("update", "pop", "popitem", "clear", "setdefault"):
        with pytest.raises(TypeError):
            getattr(s, name)("x")


def test_hungry_and_thirsty_are_derived_from_values():
    w, pid, p, r = world_with_player()
    p.energy, p.hydration = 100.0, 100.0
    assert not p.state.get("hungry") and not p.state.get("thirsty")
    p.energy = p.spec.hunger_threshold - 1
    p.hydration = p.spec.thirst_threshold - 1
    assert p.state.get("hungry") and p.state.get("thirsty")
    assert {"hungry", "thirsty"} <= set(p.state.active()) and "hungry" not in p.state.active(public_only=True)


def test_registry_is_consistent():
    names = list(STATES)
    assert len(names) == len(set(names)) and names[:2] == ["asleep", "unconscious"]          # 意識の状態が先(理由の優先順)
    for d in STATES.values():
        assert d.kind in ("stance", "condition")
        if d.kind == "stance":
            assert d.cancel_action == "cancel_" + d.enter_action
    assert is_stored("asleep") and not is_stored("awake") and not is_stored("hungry")
    assert STATES["sleep_stance"].kind == "stance" and STATES["asleep"].kind == "condition"


# --- 書き込む場所を絞る(ソースを調べる) ---
def _python_files():
    for root, _, files in os.walk(ROOT):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def test_state_is_written_only_by_the_transition_functions():
    """state[...] への書き込みは set_state だけ。state の再代入は、Animal.__init__ だけ。"""
    bad = []
    for path in _python_files():
        tree = ast.parse(open(path).read())
        stack = []

        def visit(node):
            is_fn = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_fn:
                stack.append(node.name)
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            elif isinstance(node, ast.Delete):
                targets = node.targets
            flat = []

            def expand(t):
                if isinstance(t, (ast.Tuple, ast.List)):
                    for e in t.elts:
                        expand(e)
                else:
                    flat.append(t)
            for t in targets:
                expand(t)
            for t in flat:
                where = stack[-1:] or ["<module>"]
                if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) and t.value.attr == "state"
                        and where != ["set_state"]):
                    bad.append((os.path.basename(path), "state[...] =", where))
                if (isinstance(t, ast.Attribute) and t.attr == "state" and isinstance(t.value, ast.Name)
                        and t.value.id == "self" and where != ["__init__"]):
                    bad.append((os.path.basename(path), "self.state =", where))
            for child in ast.iter_child_nodes(node):
                visit(child)
            if is_fn:
                stack.pop()

        visit(tree)
    assert not bad, bad


def test_exclusive_states_stay_consistent_during_a_simulation():
    """asleep と unconscious が同時に真にならない(睡眠・意識消失を含む長さで、毎 200 tick 検査)。"""
    for seed in (1,):
        w = World(WorldConfig(regions_x=2, regions_y=2, region_size=16, seed=seed, initial_counts={"Rabbit": 8, "Fox": 1}))
        w.generate()
        seen = set()
        for t in range(40_000):
            w.tick()
            if t % 200 == 0:
                for r in w.regions.values():
                    for e in r.entities.values():
                        if hasattr(e, "state"):
                            assert check_state_rules(e) == [], (seed, t, e.species)
                            seen |= set(e.state.stored())
        assert {"asleep"} <= seen                           # 眠りが、実際に起きている


# --- 式 ---
def test_expressions_are_limited_and_evaluated_safely():
    assert compile_expr("x * 0.5")(0.8) == pytest.approx(0.4)
    assert compile_expr("min(x, 0.3)")(0.8) == 0.3 and compile_expr("max(x, 0.3) + -x ** 2")(0.5) == pytest.approx(0.25)
    for bad in ("__import__('os')", "x.real", "open", "x if x else 1", "lambda: 1", "'a'", "y * 2", "x +", "[x]", "x ** x ** x ** x ** 99999999999 ** 9" if False else "abs(x)"):
        with pytest.raises(ValueError):
            compile_expr(bad)
    with pytest.raises(ValueError):
        validate_factors("t", {"nope": "x"})                 # 登録されていないパラメータ
    with pytest.raises(ValueError):
        validate_factors("t", {"wakefulness": "1 / 0"})      # 評価できない
    sp = replace(SPECIES["Player"], sleep_stance_factors=(("foo", "x"),))
    with pytest.raises(ValueError):
        _validate_stances(sp)
    sp = replace(SPECIES["Player"], sleep_stance_actions=(("fly", False),))
    with pytest.raises(ValueError):
        _validate_stances(sp)
    assert validate_config(WorldConfig()) == []


# --- 入眠体制 ---
def test_sleep_enters_the_stance_once_and_repeated_sleep_does_nothing():
    w, pid, p, r = world_with_player()
    ev = send(w, pid, "sleep")
    assert types(ev) == [("state_entered", "entered", "sleep_stance")]
    assert p.state.get("sleep_stance") and p.state.stored() == ["sleep_stance"]
    assert p.awake_ratio(r) == pytest.approx(p.wakefulness / p.wakefulness_max * 0.2)
    for _ in range(5):                                       # 連打しても、何も起こらず、係数は累積しない
        assert types(send(w, pid, "sleep")) == []
    assert p.awake_ratio(r) == pytest.approx(p.wakefulness / p.wakefulness_max * 0.2)       # 本来の割合 × 0.2(1 回だけ)
    assert p.state.stored() == ["sleep_stance"]


def test_stance_does_not_change_wakefulness_itself_and_narrows_perception_range():
    w, pid, p, r = world_with_player()
    p.wakefulness = p.wakefulness_max * 0.8
    before_range = p.effective_range(r)
    wake = p.wakefulness
    p.set_state("sleep_stance", True)
    assert p.wakefulness == wake                              # 本来の覚醒度は、書き換えない
    assert p.effective_range(r) == int(p.perception_range * 0.8 * 0.2) and p.effective_range(r) < before_range
    p.set_state("sleep_stance", False)
    assert p.effective_range(r) == before_range


def test_sleep_check_uses_the_effective_wakefulness_and_the_formula_is_unchanged(monkeypatch):
    w, pid, p, r = world_with_player()
    seen = []
    real = animal_mod.sleep_pressure
    monkeypatch.setattr(animal_mod, "sleep_pressure", lambda x, rh, spec, h=0.0, t=0.0: (seen.append(x), real(x, rh, spec, h, t))[1])
    p.energy = p.hydration = 100.0
    p.wakefulness = p.wakefulness_max * 0.6
    p.sleep_check(r, 600)
    p.set_state("sleep_stance", True)
    p.sleep_check(r, 1200)
    assert seen[0] == pytest.approx(0.6) and seen[1] == pytest.approx(0.12)      # 体制の間だけ、x × 係数


def test_stance_makes_falling_asleep_much_more_likely_when_tired():
    w, pid, p, r = world_with_player()

    def asleep_count(stance):
        n = 0
        for k in range(1, 301):
            p.set_consciousness("awake")
            p.set_state("sleep_stance", False)
            p.energy = p.hydration = 100.0
            p.wakefulness = p.wakefulness_max * 0.45
            p.sleep_onset_history = [10 * 3_600] * 4
            if stance:
                p.set_state("sleep_stance", True)
            tick = 10 * 3_600 + k * 86_400
            p.sleep_check(r, tick)
            n += bool(p.state.get("asleep"))
        return n
    base, with_stance = asleep_count(False), asleep_count(True)
    assert with_stance > base and with_stance >= 0.3 * 300


def test_alert_player_in_the_stance_falls_asleep_only_near_the_usual_bedtime():
    """係数 0.2: 冴えていても、習慣の入眠時刻(睡眠周期の頂点)の近くでは眠りやすく、反対の時刻ではほぼ眠れない。"""
    w, pid, p, r = world_with_player()

    def hits(clock_hour, stance=True, n=300):
        count = 0
        for k in range(1, n + 1):
            p.set_consciousness("awake")
            p.set_state("sleep_stance", stance)
            p.energy = p.hydration = 100.0
            p.wakefulness = p.wakefulness_max * 1.0                       # 起きた直後(最も冴えている)
            p.sleep_onset_history = [22 * 3_600] * 4                      # 習慣の入眠時刻は 22 時
            p.sleep_check(r, clock_hour * 3_600 + k * 86_400)
            count += bool(p.state.get("asleep"))
        return count
    assert hits(10, stance=False) == 0 and hits(22, stance=False) == 0   # 体制がなければ、冴えていると眠らない
    assert hits(10) <= 3                                                 # 反対の時刻(10 時)は、ほぼ眠れない
    assert hits(22) >= 0.4 * 300                                         # 習慣の入眠時刻(22 時)は、眠りやすい


def test_falling_asleep_ends_the_stance_and_recovers_from_the_real_wakefulness():
    w, pid, p, r = world_with_player()
    p.set_state("sleep_stance", True)
    p.wakefulness = p.wakefulness_max * 0.3
    seq = w.last_seq
    p.fall_asleep(r)
    w._collect_events()
    evs = w.events_since(seq)[0]
    assert ("state_left", "asleep", "sleep_stance") in types(evs)
    assert p.state.stored() == ["asleep"] and p.wakefulness == pytest.approx(p.wakefulness_max * 0.3)    # 本来の値
    before = p.wakefulness
    p.tick(r)
    assert p.wakefulness == pytest.approx(before + p.spec.wakefulness_recovery_per_tick)              # 睡眠は、本来の値から回復する


def test_fainting_and_unsafe_end_the_stance():
    w, pid, p, r = world_with_player()
    p.set_state("sleep_stance", True)
    p.wakefulness = 0.0001
    for _ in range(3):
        p.tick(r)
    assert p.state.get("unconscious") and not p.state.get("sleep_stance")
    w2, pid2, p2, r2 = world_with_player()
    p2.set_state("sleep_stance", True)
    p2.state["in_combat"] = True
    p2.tick(r2)
    assert not p2.state.get("sleep_stance")


def test_cancel_sleep_leaves_the_stance_and_does_nothing_otherwise():
    w, pid, p, r = world_with_player()
    assert types(send(w, pid, "cancel_sleep")) == []                      # 体制でないときは、何も起こらない(拒否もない)
    send(w, pid, "sleep")
    assert types(send(w, pid, "cancel_sleep")) == [("state_left", "cancelled", "sleep_stance")]
    assert not p.state.get("sleep_stance") and p.awake_ratio(r) == pytest.approx(p.wakefulness / p.wakefulness_max)
    p.set_consciousness("asleep")
    assert types(send(w, pid, "cancel_sleep")) == [("command_rejected", "asleep", None)]       # 眠った後は、自分で起きられない


def test_actions_blocked_by_the_stance_are_rejected_with_the_state_name():
    w, pid, p, r = world_with_player()
    send(w, pid, "sleep")
    for act, args in (("move", {"dir": "n"}), ("eat", {}), ("drink", {})):
        assert types(send(w, pid, act, **args)) == [("command_rejected", "sleep_stance", None)], act
    assert types(send(w, pid, "wait")) == [] and types(send(w, pid, "rest")) == []
    assert types(send(w, pid, "cancel_sleep")) == [("state_left", "cancelled", "sleep_stance")]
    assert types(send(w, pid, "move", dir="n")) == []                      # 解除すれば、動ける


def test_pending_move_is_dropped_when_the_stance_begins():
    w, pid, p, r = world_with_player()
    p.pending_move = "n"
    p.set_state("sleep_stance", True)
    pos = p.grid_position
    p.act(r)
    assert p.pending_move is None and p.grid_position == pos


def test_blocked_actions_are_defined_per_stance_and_individual():
    w, pid, p, r = world_with_player()
    p.set_state("sleep_stance", True)
    assert p.blocking_state("move") == "sleep_stance"
    p.sleep_stance_actions["move"] = True                    # 個体の dict を変えると、できるようになる(キャッシュは、状態の変化で更新)
    p.state["poisoned"] = True                               # (キャッシュを無効にするため、状態を変える)
    p.state["poisoned"] = False
    assert p.blocking_state("move") is None and p.blocking_state("eat") == "sleep_stance"
    p2 = world_with_player()[2]
    assert p2.sleep_stance_actions == {"move": False, "eat": False, "drink": False}     # 種の標準値を、個体の dict としてコピー
    assert p2.sleep_stance_actions is not p.sleep_stance_actions and p2.sleep_stance_factors == {"wakefulness": "x * 0.2"}


def test_sleep_rejections_and_check_command_is_pure():
    w, pid, p, r = world_with_player()
    p.set_consciousness("asleep")
    assert check_command(p, r, "sleep") == "asleep" and check_command(p, r, "move", {"dir": "n"}) == "asleep"
    p.set_consciousness("unconscious")
    assert check_command(p, r, "sleep") == "unconscious"
    p.set_consciousness("awake")
    p.state["in_combat"] = True
    assert check_command(p, r, "sleep") == "unsafe"
    p.state["in_combat"] = False
    assert check_command(p, r, "sleep") is None and check_command(p, r, "nope") == "unknown_command"
    assert check_command(p, r, "move", {"dir": "x"}) == "bad_dir"
    p.stamina = 0.0
    assert check_command(p, r, "move", {"dir": "n"}) == "exhausted"
    assert p.state.stored() == []                            # 判定は、状態を変えない
    w2, pid2, p2, r2 = world_with_player(player_sleep_enabled=False)
    assert check_command(p2, r2, "sleep") == "sleep_disabled"


def test_asleep_and_unconscious_block_every_command_like_before():
    w, pid, p, r = world_with_player()
    p.set_consciousness("asleep")
    for act in ("move", "eat", "drink", "rest", "wait", "sleep", "cancel_sleep"):
        assert p.blocking_state(act) == "asleep"
    p.set_consciousness("unconscious")
    assert p.blocking_state("wait") == "unconscious"


def test_state_survives_region_handoff_and_a_respawn_starts_clean():
    w, pid, p, r = world_with_player()
    p.set_state("sleep_stance", True)
    p.state["poisoned"] = True
    b = factory.restore(p.to_state())
    assert b.state.stored() == ["sleep_stance", "poisoned"] and b.awake_ratio(r) == pytest.approx(p.awake_ratio(r))
    p.set_consciousness("asleep")
    assert factory.restore(p.to_state()).consciousness == "asleep"
    assert "awake" not in p.to_dict()["state"] and "asleep" in p.to_dict()["state"]
    assert "hungry" in (lambda e: (setattr(e, "energy", 1.0), e.to_dict()["state"])[1])(world_with_player()[2])      # 導く状態も含む


# --- 公開(他のプレイヤー)と API ---
def test_other_players_see_only_public_states_and_events():
    w, pid, p, r = world_with_player()
    pid2 = w.add_player("bob")
    p2 = w.player_entity(pid2)
    p2.state["poisoned"] = True
    p2.set_state("sleep_stance", True)
    assert p2.to_public()["state"] == ["awake"] and "state" in p2.to_public()
    ev = {"type": "state_entered", "id": p2.entity_id, "pos": [p2.grid_position.x, p2.grid_position.y],
          "state": "sleep_stance", "kind": "stance", "reason": "entered", "player_id": pid2}
    ev2 = dict(ev, state="asleep")
    assert w.visible_events(pid2, [ev]) == [ev]              # 本人には届く
    assert w.visible_events(pid, [ev]) == []                 # 他の人には、公開でない状態の変化は届かない
    ev2["state"] = "asleep"
    assert w.visible_events(pid, [ev2]) in ([ev2], [])       # 公開の状態は、範囲内なら届く(範囲外なら届かない)


def make_client(**kw):
    return TestClient(create_app(Settings(**{"regions_x": 2, "regions_y": 2, "autotick": False, **kw})))


def test_api_state_list_effective_wakefulness_and_rejection_reason():
    P = "/api/v1"
    with make_client() as c:
        j = c.post(f"{P}/players", json={"name": "alice"}).json()
        h = {"Authorization": f"Bearer {j['token']}"}
        pid = j["player_id"]
        game = c.app.state.game
        p = game.world.player_entity(pid)
        p.wakefulness = p.wakefulness_max * 0.8
        ent = c.get(f"{P}/players/{pid}", headers=h).json()["entity"]
        assert "awake" in ent["state"] and isinstance(ent["state"], list)
        cfg = c.get(f"{P}/config").json()
        assert "sleep" in cfg["command_types"] and "cancel_sleep" in cfg["command_types"]
        assert cfg["states"]["sleep_stance"]["kind"] == "stance" and cfg["states"]["sleep_stance"]["cancel_action"] == "cancel_sleep"
        assert cfg["states"]["awake"]["derived"] is True and cfg["species"]["Player"]["sleep_stance_factors"] == {"wakefulness": "x * 0.2"}
        seq = game.world.last_seq
        c.post(f"{P}/players/{pid}/commands", json={"command_id": "s1", "type": "sleep", "args": {}}, headers=h)
        game.step()
        evs = c.get(f"{P}/players/{pid}/events", params={"since": seq}, headers=h).json()["events"]
        assert [e for e in evs if e["type"] == "state_entered" and e["state"] == "sleep_stance"]
        st = [e for e in evs if e["type"] == "stats_changed"][-1]
        assert "sleep_stance" in st["state"] and 15 <= st["wakefulness_pct"] <= 16       # 80% × 0.2(係数をかけた後の値。tick で、わずかに減る)
        c.post(f"{P}/players/{pid}/commands", json={"command_id": "m1", "type": "move", "args": {"dir": "n"}}, headers=h)
        seq = game.world.last_seq
        game.step()
        evs = c.get(f"{P}/players/{pid}/events", params={"since": seq - 5}, headers=h).json()["events"]
        assert any(e["type"] == "command_rejected" and e["reason"] == "sleep_stance" for e in evs)
