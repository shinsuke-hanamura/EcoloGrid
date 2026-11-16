# issue 4 Tidying up the Side Panel

*English version of `4_side_panel.md` (Japanese). If the two differ, the Japanese version is the source.*

**Status: implemented (2026-11-15). See "Results" at the end.** Issue #4. Tidying the structure, the key definitions, and the display rules of the Side Panel introduced in issue 2 (change of the game speed). It is the foundation for the following "player-controlled sleep" and the "call-up screen (Actions; actions without a hotkey)" (separate issues).

## Goal
Call the area beside the map the **Side Panel**, and treat its contents as **pages** (Help and Config; Actions in the future) handled by **one mechanism**.

1. Adding a key no longer requires changing several places (the key definitions live in one place)
2. The same **operation and rules** work for any terminal size (height and width). Before, only Config used a different method on a narrow terminal, and the key list of Help was not visible on a short terminal
3. A following issue (Actions) can add a new page or key with one line of a table

## Current state (from the code, after issue 2 was merged)
| Item | State |
|---|---|
| State | Two systems: `view.show_help` (Help), and `view.panel_mode`, `panel_buf`, `panel_msg` (Config) |
| Where it is implemented | The contents of Help are in `render.py` (`LEGEND`, `LEGEND_KEYS`, `legend_lines`), Config is in `panel.py`. The drawing code (`render.py`) knows both branches |
| Key definitions | The hotkeys are in `KEYS` of `play.py`; `h`, `c`, `t`, and `q` are `if` statements in the loop of `play.py`; the "Keys" of Help is `LEGEND_KEYS`; the bottom line is `FOOTERS` (4 kinds). **The same key is written separately in 4 places** |
| Height | The map height is `rows - 11`. Help is about 25 lines, so on a 24-line terminal only 13 lines are visible (**the "Keys" section is not visible**). There is neither a display of the overflow nor scrolling |
| Width | The panel is shown at a width of 76 columns (`40 + PANEL_W`) or more. Below that, Help is not shown, and `c` switches to a full-screen input (`config_screen`, `tick_screen`, `rename_screen`) (**two kinds of operation**) |
| Heading | The panel has no line that shows the current page and the keys to switch |

## Design

### The page mechanism (4a)
- `panel.py`: put the state of the panel, `PanelState` (`page`, the state inside the page `sub`, the string being typed `buf`, the message `msg`), into one `view.panel`. `view.show_help`, `panel_mode`, `panel_buf`, `panel_msg`, and `toggle_help()` are removed
- **A registry of pages**: `PAGES = {"help": …, "config": …}` (`"actions"` in the future). Each page has `title`, `body(view, width, color)` (the lines of the body), `hint(view)` (the hint on the bottom line), `handle_key(view, session, key)`, and `captures_input(view)`. The contents of Help and Config move to `pages.py` (`panel.py` holds only the mechanism)
- The drawing code (`render.py`) does not know the contents of the panel; it only calls `panel.compose` (which builds the heading, the body, and the bottom line)

### The key table `KEYMAP` (4b)
- New `keymap.py`: `KeyDef(key, label, description, kind, command or page)`. Kinds: `move` (wasd, arrows) / `command` (run by one key: e, r, z, ., x) / `panel` (open a page: h, c) / `toggle` (t) / `quit` (q) / `action` (no hotkey; appears only in the list of the Actions page; **no rows for now**)
- Everything below is **generated** from this table: the hotkeys of `play.py` (`KEYS`) and the dispatch of keys, the "Keys" of Help, and the bottom line (each kind of `FOOTERS`; the most detailed one that fits the width is chosen)
- Adding a key takes only one line of the table

### Heading and rules of operation (4c)
- **Heading line** (the first line of the panel): like `[Help]  Config`, the current page is emphasized and the names of the other pages (the keys to switch, `h` and `c`) are shown. The keys inside a page (Enter, Esc, etc.) are shown as a hint on the bottom line of the panel
- **Keys**: `h` = open/close Help. `c` = open/close Config. Esc = go back one step (typing → choosing an item → close). Pressing `c` while Help is open switches to Config; pressing `h` at the item choice of Config switches to Help (`h`, `c`, and `q` while typing are characters). While Config is open, the panel receives the keys (as before)
- **When the height is not enough** (decided): when the body does not fit, **scroll with `↑` and `↓`**. The bottom line shows `Up/Down:scroll` and the directions where more content exists (`^`, `v`). **When the body fits, `↑` and `↓` are used for movement as before** (the panel uses them for scrolling only while the body overflows)
- **When the width is not enough** (decided): show the panel **over the map, on the whole screen** (Esc returns; while it is open the map is hidden, but the game goes on). **The separate method of Config (full-screen input) is abolished** so that there is one method of operation. Only the duplicate name at startup keeps the former input (`rename_screen`), because the screen is not drawn yet

### Documents and tests (4d)
- `cli-how-to-play.md`: put the Side Panel (Help, Config, heading, keys, narrow terminals) into one section. `spec.md` §17. Add C-7 to the TODO and mark it done
- Tests (see the completion criteria)

## Order of implementation (stages)
4a the page mechanism → 4b the key table → 4c heading, height, and width → 4d documents and tests. At the end of each stage, run the existing tests and the pseudo-terminal check (below).

## Files to change (estimate)
`cli/ecologrid_cli/panel.py` (reduced to the mechanism), new `pages.py` and `keymap.py`, `render.py`, `play.py`, `view.py`, `session.py` (as far as needed), the CLI tests, documents. **No change on the server.**

## Completion criteria
- `show_help`, `panel_mode`, `panel_buf`, and `panel_msg` no longer remain. Help and Config work with the same mechanism (the registry of pages)
- **The key table and every place agree** (a test that fails when they drift): the keys of kinds `command`, `panel`, `toggle`, and `quit` in the table appear in all of the hotkey dispatch, the "Keys" of Help, and the bottom line (the detailed kind)
- **Combinations of terminal sizes** (width 40–240 × height 16–41 × pages (closed; Help; the 3 states of Config; side-by-side on a wide terminal and the full-screen display on a narrow one; the top and the end of the scrolling)): the number of lines and the width do not exceed the terminal (extending the test of issue 2). Even on a short terminal (24 lines), scrolling with `↑` and `↓` shows every item of the Legend and the Keys. When the height fits, `↑` and `↓` are not used by the panel (they are used for movement)
- On a narrow terminal (for example 60 columns), Help and Config work in the full-screen display, and Esc returns. Typing in Config, the duplicate name, and the change of the tick work with the same operation as on a wide terminal
- Tests of operation: switching `h` (open → close), scrolling (it stops at the ends), switching between `c` and `h`, `h`, `c`, and `q` while typing become characters, the way Esc goes back, and the game keys work while Help is open
- The sequence of operations with a pseudo terminal (wide and narrow terminals) passes (the procedure used in issue 2 is also done on a narrow terminal)
- The existing tests (config screen, legend, terminal sizes) are fixed to the new mechanism and all tests pass

## Risks
- **Changing the key input affects a wide area** (the loop of `play.py` and `panel.py`). The pseudo-terminal check is done at each stage
- **There are many combinations of terminal sizes**: a broken display is hard to catch with tests. It is supplemented by the matrix test over sizes and a check on a real terminal
- **A change of behavior on narrow terminals**: since the separate method of Config is abolished, the operation on a narrow terminal changes (it becomes the full-screen display). It is written in the documents
- **Consistency with the layout rules of issue 2** (`odd_at_most`, `clip`, `RESERVED_ROWS`) and the full-screen display: the full-screen display also keeps the same rules (the lines and the width do not exceed)

## Out of scope
Player-controlled sleep, the Actions page, `GET /players/{id}/actions` (separate issues, after this one), tidying colors, mouse operation, switching the position of the panel (right or bottom).

## Decisions
1. **Height**: **scroll with ↑↓** (the plan to split Help into two sections is not adopted)
2. **Width**: show it over the map on the whole screen (as recommended; the separate method of Config is abolished)
3. **Switching between `h` and `c`**: it can be switched (as recommended)

## Results (2026-11-15)
- **4a The page mechanism**: separated into `panel.py` (the mechanism: the registry `PAGES`, `open_page`, `close`, `toggle`, `scroll`, `handle_key`, `compose`) and the new `pages.py` (the contents of Help and Config). The state is put into one `view.panel` (`PanelState`: `page`, `sub`, `buf`, `msg`, `scroll`, `max_scroll`), and `view.show_help`, `panel_mode`, `panel_buf`, `panel_msg`, and `toggle_help()` are removed. The drawing code (`render.py`) does not know the contents of the panel and only calls `panel.compose`. Common parts were split out into `textutil.py` (`char_width`, `fit`, `tail`, `clip`, `odd_at_most`) and `style.py` (symbols, colors, `my_glyph`)
- **4b The key table**: new `keymap.py` (`KEYMAP`, `KeyDef`, `BY_KEY`, `HOTKEYS`, `help_key_lines()`, `footer_variants()`). The hotkeys and the dispatch of `play.py` (`dispatch_key`; `run_script` shares it), the "Keys" of Help, and the bottom line (4 kinds) are all made from this table. Adding a key takes one line of the table. The kind for actions without a hotkey has no rows for now (a following issue)
- **4c Heading, height, and width**: the heading line `[Help]  Config` (the current page is shown with `[ ]`) and the hint on the bottom line (`Up/Down:scroll`, `^`, `v`). Only when the body overflows, `↑` and `↓` scroll (when it fits, they are used for movement). Switching with `h` and `c` (`c` on Help → Config; `h` at the item choice of Config → Help); Esc goes back one step. **On a narrow terminal (under 76 columns) the panel is shown over the map on the whole screen.** The full-screen input of Config (`config_screen`, `tick_screen`, and `rename_screen` during play) is abolished so that there is one method of operation (the duplicate name at startup remains in `rename_screen`, because the screen is not drawn yet)
- **4d Documents and tests**: `cli-how-to-play.md` (§3.1 Side Panel), `spec.md` §17, and TODO C-7 marked done. Tests: `cli/tests/test_cli_keymap.py` (new: the consistency of the table, the agreement of the hotkeys, Help, and the bottom line, `dispatch_key`, the rules of `h`/`c`/Esc, the game keys while Help is open), `test_cli_view_render.py` (Help and Config on wide and narrow terminals, scrolling, and the matrix of terminal sizes (width 40–240 × height 16–41 × each page, at the top and the end of scrolling)), `test_cli_session.py` (key operation of the panel)
- **Pseudo-terminal check**: wide terminal (110×24): `h` → ↓ to scroll to the Keys → `c` → `2` → `max` → Enter → Esc → Esc → `q`. Narrow terminal (60×24, full-screen display): `c` → `1` → a name → Enter → Esc → `h` → ↓ → Esc → `q`. Both as expected
- **Remaining issues**: the Actions page and actions without a hotkey (player-controlled sleep) are for a following issue
