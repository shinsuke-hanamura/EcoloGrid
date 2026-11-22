"""キーの表(keymap.py)と、ホットキー・Help の「Keys」・最下行・サイドパネルの操作の一致(issue 4)。"""
import pytest

from ecologrid_cli import keymap, panel
from ecologrid_cli.pages import HelpPage
from ecologrid_cli.play import dispatch_key, key_to_command
from ecologrid_cli.render import FOOTERS, render
from ecologrid_cli.view import WorldView
from test_cli_view_render import ent, snap


def _view():
    rows = ["." * 32 for _ in range(32)]
    v = WorldView()
    v.player_id, v.me_id, v.my_name = "pid", "me", "Zed"
    v.load(snap([ent("me", "Player", 10, 10, hydration=50, energy=50, health=50, player_id="pid")], rows))
    v.tick_interval_ms = 50
    return v


def test_keymap_has_no_duplicate_keys_and_known_kinds():
    keys = [k.key for k in keymap.KEYMAP]
    assert len(keys) == len(set(keys))
    assert {k.kind for k in keymap.KEYMAP} <= {"move", "command", "panel", "toggle", "quit"}
    for k in keymap.KEYMAP:
        assert (k.kind in ("move", "command")) == (k.command is not None)
        assert (k.kind == "panel") == (k.page is not None)
        if k.page:
            assert k.page in panel.PAGES                       # 開くページは、登録されている


def test_hotkeys_come_from_the_table():
    for k in keymap.KEYMAP:
        if k.command:
            assert key_to_command(k.key) == k.command == keymap.HOTKEYS[k.key]
    assert key_to_command("w") == ("move", {"dir": "n"}) and key_to_command(" ") == ("wait", {})
    assert key_to_command("q") is None and key_to_command("h") is None


def test_every_shown_key_appears_in_help_keys_and_in_the_detailed_footer():
    help_text = "\n".join(HelpPage().body(_view(), 34, False))
    full = FOOTERS[0]
    for k in keymap.shown():
        assert f" {k.label}" in help_text and k.desc in help_text, k
        assert f"{k.label}:{k.desc}" in full, k
    # 隠す別名(スペースキー)は、表示しない。group は、1 行にまとまる
    assert [k.key for k in keymap.shown() if k.group == "wait"] == ["."]
    assert sum(1 for l in keymap.help_key_lines() if "wasd/arrows" in l) == 1


def test_footer_variants_are_ordered_from_detailed_to_short_and_fit_their_widths():
    widths = [len(f) for f in FOOTERS]
    assert widths == sorted(widths, reverse=True) and widths[0] == len(FOOTERS[0]) > 100
    for cols in (40, 50, 80, 100, 120):
        out = render(_view(), cols=cols, rows=30).split("\n")
        assert len(out[-1]) <= cols and "h:help" in out[-1] and "q:quit" in out[-1]      # どの幅でも、h と q は分かる
    assert "t:time/tick" in render(_view(), cols=130, rows=30)          # 詳しい候補は、幅 124 桁以上のとき


def test_dispatch_key_follows_the_table():
    class Sess:
        def __init__(self, v):
            self.view, self.sent = v, []

        def send(self, t, **a):
            self.sent.append((t, a))
            return "queued"

    s = Sess(_view())
    assert dispatch_key(s, "e") == "queued" and dispatch_key(s, "w") == "queued" and dispatch_key(s, " ") == "queued"
    assert s.sent == [("eat", {}), ("move", {"dir": "n"}), ("wait", {})]
    assert dispatch_key(s, "q") == "quit" and dispatch_key(s, "?") == ""
    assert dispatch_key(s, "t") == "handled" and s.view.time_format == "tick"            # 表示の切り替え
    assert dispatch_key(s, "h") == "handled" and s.view.panel.page == "help"            # h: Help を開く
    assert dispatch_key(s, "c") == "handled" and s.view.panel.page == "config"          # Help から c: Config へ切り替わる
    s.view.panel.sub = "menu"


def test_h_and_c_switching_and_closing_rules():
    class Sess:
        def __init__(self, v):
            self.view = v

        def send(self, *a, **k):
            return "queued"

    v = _view()
    s = Sess(v)
    dispatch_key(s, "h")                                   # 開く
    assert v.panel.page == "help"
    assert panel.handle_key(v, s, "ESC") is True and v.panel.page is None     # Esc で閉じる(Help)
    dispatch_key(s, "h")
    dispatch_key(s, "h")
    assert v.panel.page is None                            # h をもう一度で閉じる
    dispatch_key(s, "c")
    assert v.panel.page == "config" and v.panel.sub == "menu" and panel.captures_input(v)
    panel.handle_key(v, s, "h")                            # 項目の選択で h: Help へ
    assert v.panel.page == "help" and not panel.captures_input(v)
    dispatch_key(s, "c")
    panel.handle_key(v, s, "c")                            # 項目の選択で c: 閉じる
    assert v.panel.page is None
    dispatch_key(s, "c")
    panel.handle_key(v, s, "1")                            # 入力中は、h・c・q も文字(切り替わらない)
    for ch in "hcq":
        panel.handle_key(v, s, ch)
    assert v.panel.page == "config" and v.panel.sub == "name" and v.panel.buf == "hcq"
    panel.handle_key(v, s, "ESC")                          # Esc は 1 つ戻る(入力中 → 項目の選択 → 閉じる)
    assert v.panel.sub == "menu"
    panel.handle_key(v, s, "ESC")
    assert v.panel.page is None


def test_game_keys_still_work_while_help_is_open():
    v = _view()
    panel.toggle(v, "help")
    assert not panel.captures_input(v)
    assert panel.handle_key(v, None, "e") is False         # Help は、ゲームのキーを受け取らない(そのまま効く)


def test_temporary_sleep_hotkeys_come_from_the_table():
    assert key_to_command("l") == ("sleep", {}) and key_to_command("L") == ("cancel_sleep", {})
    labels = {k.label: k.desc for k in keymap.shown()}
    assert labels["l"] == "sleep" and labels["L"] == "cancel_sleep"
    help_text = "\n".join(HelpPage().body(_view(), 34, False))
    assert " l " in help_text and " L " in help_text


def test_dispatch_key_sends_sleep_and_cancel_sleep():
    class Sess:
        def __init__(self, v):
            self.view, self.sent = v, []

        def send(self, t, **a):
            self.sent.append(t)
            return "queued"

    s = Sess(_view())
    assert dispatch_key(s, "l") == "queued" and dispatch_key(s, "L") == "queued"
    assert s.sent == ["sleep", "cancel_sleep"]
