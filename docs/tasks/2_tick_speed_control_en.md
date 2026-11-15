# issue 2 Game speed (tick_interval_ms) control: players change it at runtime, and the fastest mode

*English version of `2_tick_speed_control.md` (Japanese). If the two differ, the Japanese version is the source.*

**Status: implemented (2026-11-14). See "Results" at the end.** Specification: `docs/spec/time-spec.md` §6 (Adjusting the speed per server [decided]). Issue #2. Includes TODO G-22 (change while running).

## Goal
Let **players change the speed of the game (the real-time length of one tick, `tick_interval_ms`) while the server is running, from the CLI config screen**. **0 means "no waiting, the fastest"** (simulation mode), and it can be used while clients are connected. The current value can be checked on the legend screen (`h`).

## Decisions (author's instructions, 2026-11-14)
1. **Who can change it**: all players. **Later, agreement among players and changes by an administrator will also be introduced** (for now, the permission check is kept in one place so that it can be replaced)
2. **It can be changed while running.** A player changes it from the CLI config (`c`). **The current `tick_interval_ms` can be checked on the legend screen (`h`)**
3. **The fastest mode** (`tick_interval_ms = 0`) can be used while clients are connected. A connected player can specify the fastest mode
4. The value is an **integer of 0 or more (ms), with an upper limit of 1000 ms (1 second)**

## Current state (from the code)
- The only wait is `asyncio.sleep(delay)` in `GameServer.run()`. The engine (`World.tick()`) does not depend on real time (random numbers also come from the tick, so changing the speed does not change the result)
- `core/config.py` treats `tick_interval_ms` as a positive integer and warns outside 20–200 ms. It is only the value at startup; it cannot be changed while running
- Every tick wakes up the waiting long-poll requests (`_tick_event`). The event ring buffer holds 20,000 events
- One tick takes about 0.9 ms (default world). The fastest mode is expected to give about 1,100 ticks per second (about 55 times the standard)

## Specification

### Value and validation
- `tick_interval_ms`: **an integer, 0–1000**. A `bool`, a decimal, a negative number, and a value above 1000 are rejected (422). The environment variable `ECOLOGRID_TICK_INTERVAL_MS` has the same range (the initial value at startup)
- `0` = fastest (no waiting). 1–19 gives a warning because the processing may not keep up (0 gives no warning). 20–200 is the recommended range (as before)
- While in the fastest mode, `behind_count` is not counted (it is not a delay)

### API for changing it
- `PUT /api/v1/world/tick-interval` (player authentication). Body `{"tick_interval_ms": integer}`. Response `{"tick_interval_ms": new value, "previous": previous value}`
- **The permission check is a single function** (`can_change_tick_interval(player_id)`). **For now, it allows all players.** Later it will be replaced by agreement (voting) or administrator changes (changes with `X-Admin-Key` by an administrator are also assumed)
- **Protection against abuse**: a change is allowed at most once per second of real time across all players (429 if exceeded). Every change is announced to everyone (see below)
- Getting the current value: `tick.tick_interval_ms` of `GET /config` becomes the **current value**. `tick_interval_ms_min` (0) and `tick_interval_ms_max` (1000) are added to `limits`. `/healthz` also gets the current value (for clients to cross-check)

### Event delivered to everyone
- `tick_interval_changed`: `{player_id, name, previous, tick_interval_ms}`. **It is delivered to all players regardless of the perception range** (the current `visible_events` only passes events inside the range, so a new "for everyone" kind is introduced)
- The CLI log shows, for example, "`<name>` changed the tick interval: 50 → 0 ms (max speed)"

### Server loop
- The current value is read again on every tick. When the value changes, the reference time (`nxt`) is re-based to now (also when going back from 0 to a positive value)
- **Behavior in the fastest mode** (to keep the CPU use and the API response compatible): ticks run continuously, and **control is returned to the event loop once about every 5 ms of real time** (`asyncio.sleep(0)`). An HTTP request waits about 5 ms at most
- **Coalescing of wake-ups**: long-poll requests are woken at most once per 20 ms of real time (when `tick_interval_ms` is below 20; with the standard 50 ms, every tick as before). The events up to the moment of waking are returned together
- The size of the event ring buffer is decided by measuring in the fastest mode (increase it from 20,000 if necessary)

### CLI
- **Config screen (`c`)**: choose an item (1: name, 2: tick interval). For the tick interval, enter `0`–`1000` (`max` is also accepted as 0). The current value and the range are shown, and errors (out of range, 429) are shown on the screen to enter again. An empty input cancels
- **Legend screen (`h`)**: shows the current value, for example "Tick: 50 ms". 0 is shown as "Tick: max speed". The ratio to the standard (50 ms) is also shown (for example ×1; "max" for the fastest)
- Updating the current value: the `Session` value is updated by the `tick_interval_changed` event and a periodic cross-check (`/healthz`, every 3 seconds). `step_seconds` is computed from the updated value with a lower limit (for example 1 ms). The bot's waiting uses it

## Order of implementation (stages)
### 21a Validation and the loop (small to medium)
The validation in `core/config.py` becomes 0–1000. `GameServer` holds the current (mutable) value. `run()` reads it on every tick and re-bases the reference time. Handling of `behind_count`.

### 21b API and event (medium)
`PUT /world/tick-interval`, `can_change_tick_interval`, the once-per-second limit, `tick_interval_changed` (the kind for everyone), the current value in `/config` and `/healthz`, `schemas.py`.

### 21c Behavior in the fastest mode and measurement (medium)
Returning control every 5 ms, coalescing of wake-ups, the size of the ring buffer. **Measurement**: ticks per second in the fastest mode, CPU use, API response time (1, 10, and 50 connections), the frequency of `410 events_expired`.

### 21d CLI (medium)
Choosing an item on the config screen, entering the tick interval, showing it on the legend screen, updating `Session`, the log message.

### 21e Documents and tests (small)
`time-spec.md` §6 (decide the change while running; the fastest mode, the upper limit, and the permission), `api-spec.md`, `cli-how-to-play.md`, `docker/README.md`, mark TODO G-22 as done, update `1_00_overview.md`.

## Files to change (estimate)
`server/ecologrid/core/config.py`, `api/game.py`, `api/main.py`, `api/schemas.py`, `api/settings.py`, `core/world.py` (event for everyone), `cli/ecologrid_cli/play.py`, `session.py`, `render.py`, `view.py`, `api.py`, tests, documents.

## Completion criteria (tests)
- 0 and 1000 are accepted; -1, 1001, a decimal, and `true` are rejected (422). The same for the environment variable
- A change while running takes effect. With 0, the game advances without waiting (more ticks in the same time than the standard), and `behind_count` does not increase. It can go back from 0 to a positive value
- A second change within 1 second gives 429
- `tick_interval_changed` also reaches players out of range. `/config` and `/healthz` return the current value
- Changing the speed does not change the state of the world at the same tick for the same seed (determinism)
- In the fastest mode, HTTP requests are handled within an upper bound of delay (measured)
- CLI: the config screen can choose the name and the tick interval. Errors (out of range, name in use, 429) are shown and the input can be repeated. The legend screen shows the current value. The display is updated by `tick_interval_changed`
- All tests pass

## Risks
- **Abuse**: anyone can change it, and the game of the other players becomes faster or slower at the same time. Mitigated by the once-per-second limit and the announcement to everyone. Agreement among players and administrator changes will be the fundamental measure
- **CPU use**: the fastest mode uses up one core. Control is returned every 5 ms, but the effect on other services on the same machine must be confirmed by measuring
- **Clients keeping up**: in the fastest mode a human cannot operate (one step of a Player = 5 ticks = about 5 ms). Coalescing wake-ups reduces the load on the server, but drawing has its limit on the client side
- **Missed events**: mitigated by coalescing; if some remain, enlarge the ring buffer or let the client take the view again
- **Idle detection**: `player_idle_timeout_sec` (300 seconds of real time) becomes longer in game time in the fastest mode (not a problem of operation)

## Out of scope (not now)
Changes by agreement (voting) among players, changes by an administrator only, pause and one-tick stepping, keeping the changed value (it returns to the initial value on restart), an option of the `simulate` tool (it is already the fastest).

## Results (2026-11-14)
- **21a**: `check_tick_interval` (an integer 0–1000; a bool, a decimal, and an out-of-range value are rejected) and constants in `core/config.py`. 0 gives no warning; 1–19 and above 200 give a warning. `GameServer` holds the current value (updates `world.cfg.tick_interval_ms`), and `run()` reads it on every tick. When changed, the reference time is re-based (`_rebase`). In the fastest mode (0) it does not wait and does not count `behind_count`. The environment variable `ECOLOGRID_TICK_INTERVAL_MS` also allows 0
- **21b**: `PUT /api/v1/world/tick-interval` (`{tick_interval_ms}`; 422, 429 (`Retry-After`), 403). The permission is `GameServer.can_change_tick_interval` (all players for now). At most once per second across all players. **Event for everyone** `tick_interval_changed` (`World.emit_global`, `World.GLOBAL_EVENTS`; `visible_events` also passes it to players out of range and to dead players). `/healthz` has the current value, `tick.tick_interval_ms` of `/config` is the current value, and `limits` has `tick_interval_ms_min/max`
- **21c**: In the fastest mode, control is returned to the event loop about every 5 ms of real time (`FAST_YIELD_SEC`). With an interval below 20 ms, long-poll requests are woken once per 20 ms of real time (`NOTIFY_MIN_SEC`). **`World.events_since` now reads from the newest event back to `since` only** (it no longer scans the whole log; this also helps at the standard speed)
- **21d**: The CLI config screen (`c`) becomes a choice of items (1: name, 2: tick interval) (`rename_screen`, `tick_screen`, `config_screen`). 0–1000 or `max`. Messages for out of range, 429, and 403, then enter again. The legend screen (`h`) shows `Tick: 50 ms (x1)` (`Tick: 0 ms (max speed)` for the fastest). Updated by `tick_interval_changed` and a cross-check of `/healthz` every 3 seconds. `step_seconds` has a lower limit (1 ms)
- **Side Panel** (added 2026-11-14): the area beside the map (where the Help legend was) is named the "Side Panel"; `h` = Help, and `c` = **Config** (name and tick interval), which is entered inside the panel (`cli/ecologrid_cli/panel.py`; keys are received one by one; `q` and the movement keys can also be typed as characters; multibyte characters are possible). On a narrow terminal (under 76 columns) the full-screen input of before is used. A pseudo terminal run of `c` → `2` → `max` → Enter → `1` → a name → Enter → Esc → `h` → `q` was confirmed
- **Fix of a display problem** (2026-11-15; it looked as if one blank line was inserted when the Side Panel was hidden): the cause was that the processing that makes the width and height of the map odd (`| 1`) exceeded the size of the terminal (one more line than the terminal, so it scrolled; when half of the terminal width is even, every row overflowed by 2 columns and wrapped). The wrap of the key list on the bottom line (about 99 columns) was also a cause. **How it was fixed**: the map is the largest odd size that fits the terminal (`odd_at_most`), 11 lines are reserved for the non-map lines, every line is fit to the terminal width (`clip`; color codes are not counted as columns), and the width of the status bars and the key list on the bottom line shrink to the terminal width. A test was added that checks that the width and the number of lines do not exceed the terminal for terminal widths 40–240 × heights 16–41 × each page (none, Help, Config) (TODO C-1 resolved)
- **21e**: Updated `time-spec.md` §6, §8, §10, `api-spec.md`, `cli-how-to-play.md`, `docker/README.md`, `spec.md`, and TODO G-22
- **Tests**: `server/tests/test_tick_speed.py` (22 tests: validation, API, 429, the permission hook, the event for everyone, the loop in the fastest mode, coalescing of wake-ups, determinism), `cli/tests` (config screen, legend, events)
- **Measurement** (default world 4×4×32, in-process ASGI; the client processing is in the same process, so the API response looks worse than on a real server; `tools/measure_tick_speed.py`, not committed):

| Interval | Long-polling players | ticks/s | CPU | Response time (`/healthz`) p50 / p95 | 410 | behind |
|---|---|---|---|---|---|---|
| 50 ms | 1 / 10 / 50 | 20 | 19 / 26 / 51% | 2–3 / 5–8 ms | 0 | 0 (3 with 50 players) |
| **0 (fastest)** | 1 | **about 1,100** | 100% | 25 / 31 ms | 0 | 0 |
| 0 | 10 | about 600 | 100% | 34 / 43 ms | 0 | 0 |
| 0 | 50 | about 150 | 100% | 72 ms / - | 0 | 0 |

  - The fastest mode is **about 55 times** the standard (20 ticks/s) with one player. With more players the API work increases and ticks/s goes down (still about 7 times the standard with 50 players). It uses up one core (CPU 100%)
  - `410 events_expired` is 0 even in the fastest mode (the ring buffer of 20,000 was enough). The effect of coalescing and the improvement of `events_since`
  - The interval of returning control was compared at 0, 1, 2, and 5 ms; there was no large difference in the response time (one tick and the API work dominate), and 5 ms was adopted
- **Remaining issues**: in the fastest mode with many players the API work dominates (room for optimization, such as returning the waiting long-poll requests together and caching `visible_events`). Agreement among players, changes by an administrator, pause and one-tick stepping are out of scope (future)
