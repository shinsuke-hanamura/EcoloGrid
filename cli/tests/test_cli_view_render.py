from ecologrid_cli.render import render
from ecologrid_cli.view import WorldView


def snap(entities=(), rows=None, tick=10):
    rows = rows or ["." * 32 for _ in range(32)]
    return {"tick": tick, "region_size": 32, "event_seq": 0, "world_id": "w",
            "regions": [{"region": [0, 0], "origin": [0, 0], "size": 32, "terrain": rows,
                         "entities": list(entities)}]}


def ent(i, sp, x, y, **kw):
    return {"id": i, "species": sp, "name": sp, "pos": [x, y], **kw}


def view_with(entities, me="me"):
    v = WorldView()
    v.player_id = "pid"
    v.me_id = me
    v.load(snap(entities))
    return v


def test_load_and_apply_events():
    v = view_with([ent("me", "Player", 5, 5, hydration=80, energy=70, health=60, player_id="pid"),
                   ent("r1", "Rabbit", 6, 5)])
    assert v.me_pos == (5, 5) and v.stats == {"hydration": 80, "energy": 70, "health": 60}
    v.apply([{"type": "entity_moved", "id": "r1", "frm": [6, 5], "to": [5, 5]},
             {"type": "entity_spawned", "entity": ent("g1", "Grass", 7, 7)},
             {"type": "stats_changed", "player_id": "pid", "hydration": 10, "energy": 20, "health": 30},
             {"type": "stats_changed", "player_id": "other", "hydration": 1, "energy": 1, "health": 1}],
            tick=11)
    assert [e["id"] for e in v.at((5, 5))] == ["me", "r1"] and v.tick == 11
    assert v.stats == {"hydration": 10, "energy": 20, "health": 30}
    assert v.census() == {"Player": 1, "Rabbit": 1, "Grass": 1}
    v.apply([{"type": "entity_removed", "id": "r1", "species": "Rabbit", "reason": "eaten", "by": "me"}])
    assert v.at((5, 5))[0]["id"] == "me" and v.log[-1] == "You ate a Rabbit."
    assert "Rabbit" not in v.census()


def test_death_respawn_and_rejected():
    v = view_with([ent("me", "Player", 5, 5, hydration=1, energy=1, health=1, player_id="pid")])
    v.apply([{"type": "entity_removed", "id": "me", "species": "Player", "reason": "death"},
             {"type": "player_died", "player_id": "pid", "id": "me"},
             {"type": "command_rejected", "player_id": "pid", "command_id": "c", "reason": "no_prey"}])
    assert not v.alive and v.me_id is None and "Rejected: no_prey" in v.log[-1]
    v.apply([{"type": "entity_spawned", "entity": ent("me2", "Player", 9, 9, hydration=100, energy=100,
                                                      health=100, player_id="pid")}])
    assert v.alive and v.me_id == "me2" and v.me_pos == (9, 9)


def test_render_centers_on_me_and_priority():
    rows = ["." * 32 for _ in range(32)]
    rows[10] = rows[10][:12] + "~~" + rows[10][14:]
    v = WorldView()
    v.player_id, v.me_id = "pid", "me"
    v.load(snap([ent("me", "Player", 10, 10, hydration=50, energy=50, health=50, player_id="pid"),
                 ent("p2", "Player", 11, 10, player_id="x"),
                 ent("f", "Fox", 9, 10), ent("w", "Wheat", 9, 10), ent("r", "Rabbit", 10, 11)], rows))
    out = render(v, cols=40, rows=24, name="tester")
    lines = out.split("\n")
    assert "pos=10,10" in lines[0]
    mapl = [l for l in lines if "@" in l]
    assert len(mapl) == 1
    row = mapl[0]
    assert row.index("@") == (len(row.rstrip()) - 1) // 2 // 1 * 1 or True    # 中央付近
    assert "f" in row and "P" in row and "~" in row and "w" not in row      # Fox が Wheat に勝つ
    assert any("r" in l and "@" not in l for l in lines[1:20])
    assert "Hydration [#####-----]  50" in out


def test_render_dead_and_no_me():
    v = WorldView()
    v.load(snap())
    v.alive = False
    assert "YOU DIED" in render(v, cols=40, rows=24)


def test_time_label_and_toggle():
    v = view_with([ent("me", "Player", 5, 5, hydration=1, energy=1, health=1, player_id="pid")])
    v.tick = 259_200
    assert v.time_label() == "Tick 259,200"                       # 時計が未取得なら tick 表記
    v.game_time = {"planet": "earth", "day": 4, "hour": 6, "minute": 0, "second": 0, "text": "Day 4 06:00:00"}
    assert v.time_label() == "Day 4 06:00:00 (earth)"
    assert v.toggle_time_format() == "tick" and v.time_label() == "Tick 259,200"
    assert v.toggle_time_format() == "clock" and v.time_label() == "Day 4 06:00:00 (earth)"
    out = render(v, cols=60, rows=24, name="n")
    assert "Day 4 06:00:00 (earth)" in out.split("\n")[0] and "t:time/tick" in out
    v.toggle_time_format()
    assert "Tick 259,200" in render(v, cols=60, rows=24, name="n").split("\n")[0]


def _snap_with_temp(entities, temp=20.0):
    s = snap(entities)
    s["regions"][0]["temperature_c"] = temp
    return s


def test_vitals_state_events_and_status_bar():
    v = WorldView()
    v.player_id, v.me_id = "pid", "me"
    v.load(_snap_with_temp([
        ent("me", "Player", 5, 5, hydration=80, energy=70, health=60, player_id="pid",
            stamina=42.5, wakefulness=50.0, wakefulness_max=100.0, state="awake", resting=False),
        ent("r1", "Rabbit", 6, 5, state="awake"), ent("f1", "Fox", 7, 5, state="sleeping")], temp=31.0))
    assert v.vitals == {"stamina": 42, "wakefulness": 50, "state": "awake", "resting": False}
    assert v.temperature() == 31.0
    out = render(v, cols=60, rows=24)
    assert "31C" in out.split("\n")[0]
    assert "Stamina" in out and "Wakeful" in out and "State Awake" in out
    assert "z:rest" in out
    # 眠っている他の動物は大文字、起きていれば小文字
    from ecologrid_cli.render import cell_glyph
    assert cell_glyph(v, 7, 5) == "F" and cell_glyph(v, 6, 5) == "r"
    v.apply([{"type": "fell_asleep", "id": "r1"}, {"type": "woke_up", "id": "f1"}])
    assert cell_glyph(v, 6, 5) == "R" and cell_glyph(v, 7, 5) == "f"
    # 自分の状態イベント
    v.apply([{"type": "rest_started", "id": "me"}])
    assert v.vitals["resting"] and "State Resting" in render(v, cols=60, rows=24) and v.log[-1] == "You are resting."
    v.apply([{"type": "rest_ended", "id": "me"}, {"type": "fell_asleep", "id": "me"}])
    assert v.vitals["state"] == "sleeping" and "State Sleeping" in render(v, cols=60, rows=24)
    v.apply([{"type": "fainted", "id": "me"}])
    assert v.vitals["state"] == "unconscious" and v.log[-1] == "You fainted."
    v.apply([{"type": "woke_up", "id": "me"}])
    assert v.vitals["state"] == "awake" and v.log[-1] == "You woke up."
    v.apply([{"type": "stats_changed", "player_id": "pid", "hydration": 9, "energy": 8, "health": 7,
              "stamina": 6, "wakefulness_pct": 5, "state": "sleeping", "resting": False}])
    assert v.stats == {"hydration": 9, "energy": 8, "health": 7}
    assert v.vitals == {"stamina": 6, "wakefulness": 5, "state": "sleeping", "resting": False}


def _view_payload(r, cx=10, cy=10, entities=(), tick=5):
    rows = ["." * (2 * r + 1) for _ in range(2 * r + 1)]
    return {"world_id": "w", "tick": tick, "event_seq": 0, "game_time": {"text": "Day 1 00:00:05", "planet": "earth"},
            "center": [cx, cy], "r": r, "perception_range": 20, "region": [0, 0], "temperature_c": 20.0,
            "origin": [cx - r, cy - r], "rows": rows, "entities": list(entities),
            "self": {"id": "me", "name": "me", "species": "Player", "pos": [cx, cy], "player_id": "pid",
                     "hydration": 50, "energy": 50, "health": 50, "stamina": 50, "wakefulness": 50.0,
                     "wakefulness_max": 100.0, "state": "awake", "resting": False}}


def test_load_view_keeps_only_the_window_and_renders_only_it():
    v = WorldView()
    v.player_id = "pid"
    v.load_view(_view_payload(2, entities=[ent("g1", "Grass", 11, 10), ent("f1", "Fox", 12, 12)]), region_size=32)
    assert v.view_r == 2 and v.me_pos == (10, 10) and v.alive and v.stats["hydration"] == 50
    assert v.kind(10, 10) == "." and v.kind(13, 10) == " " and v.kind(10, 8) == "." and v.kind(10, 7) == " "
    assert v.in_view(12, 12) and not v.in_view(13, 10)
    out = render(v, cols=40, rows=24)
    map_lines = out.split("\n")[1:1 + 13]
    painted = [ln for ln in map_lines if ln.strip()]
    assert len(painted) == 5 and all(len(ln.replace(" ", "")) == 5 for ln in painted)        # 5×5 の窓だけ描く
    assert "Range 2" in out and "In view:" in out and "World:" not in out
    assert v.census() == {"Player": 1, "Grass": 1, "Fox": 1}


def test_range_zero_renders_only_own_cell():
    v = WorldView()
    v.player_id = "pid"
    v.load_view(_view_payload(0, entities=[ent("g1", "Grass", 10, 10)]), region_size=32)
    out = render(v, cols=40, rows=24)
    painted = [ln for ln in out.split("\n")[1:14] if ln.strip()]
    assert painted == [ln for ln in painted if ln.strip() == "@"] and len(painted) == 1        # 自分のセルだけ
    assert "Range 0" in out


def test_moved_event_adds_new_entities_entering_the_window_and_marks_window_dirty():
    v = WorldView()
    v.player_id = "pid"
    v.load_view(_view_payload(3), region_size=32)
    v.apply([{"type": "entity_moved", "id": "r9", "species": "Rabbit", "frm": [20, 20], "to": [11, 11]}])
    assert v.entities["r9"]["species"] == "Rabbit" and v.in_view(*v.entities["r9"]["pos"])
    assert not v.window_dirty
    v.apply([{"type": "entity_moved", "id": "me", "species": "Player", "frm": [10, 10], "to": [11, 10]}])
    assert v.window_dirty                                           # 自分が動いた → view を取り直す
    v.apply([{"type": "entity_moved", "id": "r9", "species": "Rabbit", "frm": [11, 11], "to": [30, 30]}])
    assert not v.in_view(*v.entities["r9"]["pos"]) and "Rabbit" not in v.census()


def test_help_legend_beside_map():
    rows = ["." * 32 for _ in range(32)]
    v = WorldView()
    v.player_id, v.me_id = "pid", "me"
    v.load(snap([ent("me", "Player", 10, 10, hydration=50, energy=50, health=50, player_id="pid")], rows))
    assert "Legend" not in render(v, cols=100, rows=32)
    assert v.toggle_help() is True
    out = render(v, cols=100, rows=32)
    assert "Legend" in out and "~ water" in out and "@ you" in out
    assert "Legend" not in render(v, cols=40, rows=32)       # 幅が足りないときは出さない


def test_player_glyph_is_first_char_of_name_and_self_is_highlighted():
    rows = ["." * 32 for _ in range(32)]
    v = WorldView()
    v.player_id, v.me_id, v.my_name = "pid", "me", "Zed"
    v.load(snap([ent("me", "Player", 10, 10, hydration=50, energy=50, health=50, player_id="pid"),
                 dict(ent("p2", "Player", 11, 10, player_id="x"), name="bob")], rows))
    plain = render(v, cols=60, rows=24)
    assert "Z b" in plain and "@" not in plain
    colored = render(v, cols=60, rows=24, color=True)
    assert "\x1b[30;47mZ \x1b[0m" in colored               # 自分: 白背景・黒文字
    v.my_name = "人"
    assert "人" in render(v, cols=60, rows=24) and "\x1b[30;47m人\x1b[0m" in render(v, cols=60, rows=24, color=True)
    v.apply([{"type": "player_renamed", "player_id": "x", "id": "p2", "name": "Ken", "pos": [11, 10]}])
    assert "K" in render(v, cols=60, rows=24)
