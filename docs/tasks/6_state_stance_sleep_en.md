# issue 6 State (`state`) as a dict, and stance and sleep control

*English version of `6_state_stance_sleep.md` (Japanese). If the two differ, the Japanese version is the source.*

**Status: implemented (2026-11-18). See "Results" at the end.** Issue #6. It covers, from TODO G-27 (extension of sleep), only "falling asleep on one's own", and the foundation of G-46 (extension of stances). **The call-up screen (Actions page) is the next issue.** Specification: `docs/spec/animal-states-spec.md` §4 and §5.

## Goal
1. **Make `state` a dict from a name to a boolean** (`p.state["asleep"] = True`, `p.state.get("awake")`). Replace the branches such as `state != AWAKE` on an exclusive string, so that **several states can be true at the same time**, such as "poisoned and asleep"
2. Let a registry define, for each state, the distinction between a **stance** and a **condition** (`kind`), the **actions it allows or blocks (`actions`)**, and the **factors it applies to parameters (`factors`)**
3. Create the first stance, the **sleep stance (`sleep_stance`)**. It is entered with `sleep` and left with `cancel_sleep`. While it lasts, the wakefulness is treated as lower, which makes the **natural falling asleep** easier. Moving, eating, and drinking are not possible
4. Give **temporary hotkeys** to the actions that enter and leave the sleep stance (`sleep`, `cancel_sleep`). (The call-up screen (Actions page) will be made in the next issue; then the hotkeys are removed and the actions move to the Actions page)

## Decisions
**The state dict**
1. `state` is a `StateDict` (it inherits `dict`). The key is the name of a state and the value is a boolean (not written = false). `p.state["poisoned"] = True`, `p.state.get("asleep")`
2. **Compatibility is not kept.** `p.state` is no longer a string. **Every use of `state` in the code, the tests, the API, and the CLI is fixed**
3. **The numeric parameters (`hydration`, `energy`, `health`, `stamina`, `wakefulness`, `toxicity`) stay attributes.** They are not put into the dict
4. **Exclusion is given loosely by a rule, not by the implementation.** However, **(1) the places that write are limited**, and **(2) a state is derived from the states that exclude each other** (`awake` is computed by `get("awake")` as `not (asleep or unconscious)`)
5. **The distinction between a stance and a condition is not put into the stored form; it is an attribute of the registry (`kind`).** What is stored is one flat dict
6. **No check that the behavior is unchanged (no comparison with a golden result) is done.** If debugging is needed when the game balance is adjusted, it is done then

**Stance and sleep**
7. `sleep` enters the sleep stance, and the factors of the stance take effect. **The transition happens only once** (repeating has no meaning)
8. **The check of falling asleep is not changed** (it stays the natural falling asleep; there is no check at the moment of `sleep`). **The calculation of the perception range is not changed either** (if the wakefulness is treated as lower, the range narrows accordingly)
9. **Leaving is `cancel_<action name>`** (`cancel_sleep`). **Taking it when not in that stance does nothing**
10. **The lowering of the wakefulness by a stance lasts only while the stance lasts.** When the player falls asleep, the stance is left and the effect of the factors is removed. The sleep recovers from the real wakefulness
11. **Which actions are possible or impossible can be defined for each stance.** In the sleep stance, moving, eating, and drinking are impossible
12. **What is shown is the value after the factors** (in the sleep stance, the wakefulness bar goes down)
13. **The factors and the possible actions are held under the names `<state name>_factors` and `<state name>_actions`** (for example `sleep_stance_factors = {"wakefulness": "x * 0.2"}`)
14. **The call-up screen (Actions page) is separated into the next issue.** In this issue, temporary hotkeys (below) are given to `sleep` and `cancel_sleep`. **`state` in the API also includes the derived states (`awake`, `hungry`, `thirsty`)** (all of them for the player itself, only the `public` states for other players)
15. "An animal must rest, and the necessary stances can be defined for each species" is **not implemented now** (TODO G-46; `rest` stays the former `resting`)

## Current state (from the code)
- `Animal.state` is a string that holds only one of `awake`, `sleeping`, and `unconscious`. It is used in 34 places in the code (23 in `animal.py`, 3 in `memory.py`, 3 in `api/main.py`, 2 in `region.py`, 2 in `player.py`, 1 in `simulate.py`) and in 67 places in the tests. It also appears in the API `state`, in the public `state` (used to draw other players; `World.PUBLIC_KEYS`), and in the CLI (`STATE_EVENTS` in `view.py`, and others)
- `in_combat` and `falling` are flags (attributes) of `Animal`, used by `is_safe`. They appear in 14 places in the tests. `resting` is also a flag (not touched now)
- The branches are of two kinds: **permission** (moving, eating, drinking, `can_eat`, starting to rest, the decision of an NPC, perception) and **dynamics** (decrease and recovery of the wakefulness, the checks of falling asleep and waking up, recovery from unconsciousness)
- Falling asleep is only the probabilistic check of `Animal.sleep_check` (every 600 ticks). When awake and safe, `p = min(1, gain × pressure^n)`. The pressure rises as the wakefulness ratio `x` becomes low and with the sleep cycle, and falls as hunger and thirst become deep
- The wakefulness ratio is used in **`x` of the check of falling asleep** and in the **effective perception range** (`awake_ratio` → `effective_range`)
- While asleep, every command except `respawn` is rejected. The decision to accept a command is written directly in `Region`
- `toxicity` is only a numeric attribute, and its effect is not implemented
- The Side Panel has two pages, Help and Config (issue 4). The key table (`keymap.py`) does not yet use the kind `action` (no hotkey)

## Design

### The state dict and the registry (6a)
- **Stored** (per individual): `Animal.state` (`StateDict`) holds only the **stored states**. For now: `asleep`, `unconscious`, `sleep_stance`, `in_combat`, `falling` (the flags `in_combat` and `falling` move into the dict). Only the true keys are saved (`to_state` / `load_state`)
- **Derived states**: `get(name)` computes them with the derivation function of the registry: `awake` = `not (asleep or unconscious)`, `hungry` (Energy < `hunger_threshold`), `thirsty` (hydration < `thirst_threshold`). **A derived state cannot be written with `state[...] = ...`** (an error)
- **`get(name, default=False)`**: the stored value; if none, the derived value; if neither, `default`. Calling `get` with a name that is not registered is an **error** (to prevent spelling mistakes)
- **`StateDict.__setitem__`**: accepts only registered, stored names. **Only when the value changes**, it (a) emits a state event (`state_entered`, `state_left`; items `state`, `kind`, `reason`) and (b) invalidates the cache of the possible actions. If the value is the same, it does nothing
- **Registry `STATES`** (`core/state.py`): name → `StateDef` (`kind` = `stance` / `condition`, `label`, `public` (whether to show it to other players), `actions` (possible or impossible), `factors`, a derivation function (derived states only), and, for a stance, `enter_action`, `cancel_action` (= `cancel_` + `enter_action`), and `ends_on`). **A name is unique regardless of `kind`** (checked at registration)
- **The exclusion rule** (written as a table in the specification): `asleep` and `unconscious` are never true at the same time (`awake` is derived). The others are independent
- **Limit the places that write**: `state[...] = ...` may be written only in **the transition functions** (`fall_asleep`, `wake_up`, `_faint`, the move from unconsciousness to sleep in `_update_wakefulness`, `enter_state` / `leave_state`, `load_state`, and the initialization in `bind` / at creation). **A test checks that this is kept** (it parses the source under `ecologrid/` and fails if `state` is written outside the permitted functions)
- **Checking function** `check_state_rules(individual)`: returns the violations of the rule (such as `asleep` and `unconscious` both true). A test that runs the simulation calls it on every tick (it is not enforced at runtime)
- **Names**: the former `sleeping` is `asleep` (the name of a state is an adjective or a past participle). `awake` and `unconscious` stay

### What is rewritten (6a, 6b)
- **Permission branches** (those that stop moving, eating, drinking, resting, the decision of an NPC, and perception with `state != AWAKE`) → `self.state.get("awake")`. Later (6c) they are unified into `action_allowed`
- **Dynamics branches** (decrease and recovery of the wakefulness, the checks of falling asleep and waking up, recovery from unconsciousness) → replaced with `get` of `asleep`, `unconscious`, and `awake` (the processing itself is not changed)
- `is_safe` → `not (state.get("in_combat") or state.get("falling"))` (replacing the flags)
- **API and saving**: `state` in `to_dict` becomes a **list of state names** (★1). `to_state` is a dict of the stored states. The public output (`to_public`, `PUBLIC_KEYS`) has only the `public` states
- **Tests**: `p.state == "sleeping"` → `p.state.get("asleep")` and so on (67 places), `p.in_combat = True` → `p.state["in_combat"] = True` and so on (14 places)
- "The behavior is unchanged" is confirmed by **all the rewritten existing tests passing** (no comparison with a golden result)

### Unifying permission (6c)
- `Animal.action_allowed(action name)`: combines the `actions` of the true states (**the stored true states**, not the derived ones) (if any is False, it is impossible; if not written, it is possible). The result is cached until the state changes
- **Put the decision to accept a command into one function**: `check_command(individual, type, arguments)` → a reason for rejection or None. The command processing (`Region`) uses it. The API for the Actions page of the next issue is expected to use the same decision
- The `actions` of `asleep` and `unconscious` are the same as the former rejection (every command except `respawn` is rejected; the reasons are the former `asleep` and `unconscious`)

### The stance `sleep_stance`, its factors and possible actions (6d)
- **Species parameters** (`SpeciesDef`): `sleep_stance_factors` (`dict[parameter name, expression]`; default `{"wakefulness": "x * 0.2"}`) and `sleep_stance_actions` (`dict[action name, bool]`; default `{"move": False, "eat": False, "drink": False}`). The standard value of the species is **copied as the value of the individual** when the individual is created (in the future, a gene). The `factors` and `actions` of the other states are the defaults of the registry (to override them for a species, add an item with the same name)
- **Language of the expression**: only numbers, the variable `x` (the value before applying), `+ - * / **`, parentheses, `min`, and `max` (a safe, limited syntax; `eval` is not used; checked and compiled at startup). The result is kept within the range of the parameter. The parameters that the factors of a stance affect are **only the registered ones** (for now `wakefulness`). An unregistered name is an error at startup
- **Effective wakefulness ratio** = `effective("wakefulness", wakefulness ratio)` (apply the `factors` of the true states in the order they were entered). **`wakefulness` itself is not rewritten.** It is used in **`x` of the check of falling asleep** (`sleep_check`; the formula is not changed) and in the **effective perception range** (`awake_ratio` → `effective_range`; the formula is not changed)
- **End of a stance**: it is left when an event of `ends_on` happens (sleep stance: falling asleep, unconsciousness, becoming unsafe) (`state_left`, with `reason`). When the player falls asleep (`fall_asleep`), the effect of the stance is removed and the sleep recovers from the real wakefulness. Death and respawn empty the stances. The stances are kept across a Region boundary (`to_state`)
- **Commands**: `sleep` = accepted when the player is awake, safe, and sleep is enabled for the Player; it enters the sleep stance (`state_entered`). **If already in the stance, nothing happens.** Rejections: `asleep`, `unconscious`, `unsafe`, `sleep_disabled`. `cancel_sleep` = leaves the stance (`reason` = `cancelled`). **If not in the stance, nothing happens.** While asleep, it is rejected with `asleep` (one cannot wake up on one's own). **Impossible actions** (`actions` of the stance is False): rejected **with the name of the state that blocks them as the reason** (for example `move` in the sleep stance has the reason `sleep_stance`; the same rule as `asleep` and `unconscious` while asleep. ★3). `cancel_<action of the stance>` is always accepted while in that stance

### API (6e)
- `state` in `entity` and `stats_changed`: a list of state names (★1). **`wakefulness_pct` is the effective wakefulness ratio** (after the factors). The real value stays in `wakefulness` (`to_dict`)
- `/config`: `sleep` and `cancel_sleep` in `command_types`, and `sleep_stance_factors` and `sleep_stance_actions` in `species`
- Events: `state_entered` and `state_left` (delivered to the player itself and to players in range; the only item published is the name of a `public` state). The former `fell_asleep`, `woke_up`, and `fainted` are still emitted (the log and the drawing use them)

### CLI (6e)
- **Temporary hotkeys**: add to the key table (`keymap.py`), as rows of the kind `command`, **`l` (the l of sleep) = `sleep`** and **`L` = `cancel_sleep`**. In the next issue they move to the Actions page and the hotkeys are removed. The "Keys" of Help and the bottom line are generated from the key table, so they appear automatically (the bottom line picks the candidate that fits the width)
- **State line**: shows the `label` of the stance entered (`Ready to sleep`) and being asleep or unconscious. **The wakefulness bar is the effective value** (it goes down in the stance). The start and the end of a stance (falling asleep, cancelling, unconsciousness, and so on) are written in the log (`You get ready to sleep.` and so on)
- The use of `state` (a string) in the drawing (`render.py`), `view.py`, and `play.py` is rewritten to the list of `state` (such as `"asleep" in state`)

## Order of implementation (stages)
- **6a `StateDict`, the registry, and rewriting the code**: `core/state.py` (`StateDef`, `STATES`, `StateDict`, the derivation functions, `check_state_rules`), rewriting the uses of `state` in `Animal` and elsewhere (including `in_combat` and `falling`), `to_state` / `load_state`
- **6b Rewriting the tests, the API, and the CLI**: the 67 + 14 places in the tests, `state` in `to_dict`, the public output, and the CLI, the test of the places that write, and the test of `check_state_rules`. **Here, confirm that the existing tests (after rewriting) all pass**
- **6c Unifying permission**: `action_allowed` and `check_command`, rewriting the command processing of `Region`
- **6d The stance `sleep_stance`**: registration, `sleep_stance_factors` and `sleep_stance_actions`, evaluating the expression, the effective wakefulness, `sleep` and `cancel_sleep`, the end of the stance, the events
- **6e API and CLI**: the list of `state` (derived states included) and the effective `wakefulness_pct`, `/config`, the temporary hotkeys, the state line, the bar, the log
- **6f Documents and tests**: `animal-states-spec.md` (the state dict, the exclusion rule, stances), `api-spec.md`, `cli-how-to-play.md`, TODO (G-27, G-46)

## Files to change (estimate)
New `server/ecologrid/core/state.py`; `server/ecologrid/core/animal.py`, `player.py`, `region.py`, `memory.py`, `species.py`, `messages.py`, `config.py`, `world.py` (public items); `api/main.py`, `schemas.py`, `memory_views.py`; `tools/simulate.py`; `cli/ecologrid_cli/keymap.py`, `render.py`, `view.py`, `session.py`, `api.py`, `play.py`; a wide range of tests of the server and the CLI; documents.

## Completion criteria (tests)
**The state dict**
- `p.state["asleep"] = True`, `p.state.get("asleep")`, and `p.state.get("awake")` (derived) work. `awake` is false if either `asleep` or `unconscious` is true. Writing a derived state (`awake`, `hungry`, `thirsty`) is an error. A name that is not registered is an error for both `get` and set
- **"Poisoned and asleep" can be expressed at the same time** (`poisoned` and `asleep` are both true, without affecting each other)
- **Test of the places that write**: no code writes `state` outside the permitted functions
- **Check of the rule**: run the simulation (several seeds; long enough to include sleep and unconsciousness), and `check_state_rules` returns no violation on any tick
- The stored states are kept by `to_state` / `load_state` (including across a Region boundary). Only the `public` states appear in the public output (to other players)
- **All the existing tests pass (after rewriting the uses of `state`)**

**Stance and sleep**
- **Evaluating the expression**: `x * 0.5`, `min(x, 0.3)` and so on are evaluated correctly and kept within the range. Dangerous expressions are rejected at startup. An unregistered parameter name is an error
- **Repeating has no meaning**: however many times `sleep` is taken, `state_entered` is emitted once and the effective wakefulness ratio stays the value with the expression applied once
- **The formula of the check of falling asleep is unchanged**: outside the stance the probability is the same as before (regression). In the stance it is the value of the same formula evaluated with the effective wakefulness ratio
- **The wakefulness itself is not rewritten.** **When the player falls asleep, the stance is left and the sleep recovers from the real wakefulness** (`state_left`, `reason` = `asleep`). The stance is also left on unconsciousness, becoming unsafe, death, and respawn. It is kept across a Region boundary
- **Perception range**: in the stance, the effective range is decided by the effective wakefulness ratio (with the expression `x * 0.2`, one fifth of the range without the stance). The formula is unchanged
- **The stance makes falling asleep easier according to the time (sleep cycle)** (factor 0.2): an alert Player (wakefulness 1.0) hardly falls asleep at any time without the stance; with the stance, it falls asleep easily near the usual bedtime (40% or more in 300 checks) and hardly falls asleep at the opposite time (3 or fewer). The checks can be reproduced with deterministic random numbers. Deep hunger and thirst make falling asleep harder
- **Impossible actions**: in the stance, moving, eating, and drinking are rejected, and `wait`, `rest`, and `cancel_sleep` are accepted. Changing `sleep_stance_actions` changes what is possible and impossible
- **`cancel_sleep`**: leaves the stance (`reason` = `cancelled`). When not in the stance, nothing happens (neither a rejection nor an event). While asleep it is rejected with `asleep`

**API and CLI**
- The list of `state` (**derived states included**; all for the player itself, only the `public` states for other players) and the effective `wakefulness_pct` are output. `/config` shows `sleep` and `cancel_sleep`, and `sleep_stance_factors` and `sleep_stance_actions`
- **Temporary hotkeys**: `l` sends `sleep` and `L` sends `cancel_sleep` (`dispatch_key` made from the key table). They appear in the "Keys" of Help and in the detailed candidate of the bottom line (a test of agreement with the key table)
- `Ready to sleep` appears in the state line, and the bar goes down with the effective value when the stance is entered. The start and the end of the stance appear in the log
- The tests of the terminal size (lines and width) pass (after changing the state line and the bottom line)
- A sequence of operations on a pseudo terminal (`l` → check the state → `L`) passes
- All the tests pass

## Risks
- **Rewriting every use of `state` (34 places in the code, 81 in the tests)**: the range is wide, and it is easy to miss something or change the behavior. **Measure**: finish 6a and 6b before adding the stance, and confirm that all the existing tests pass before going on to 6c and later. Since no comparison with a golden result is done, rewrite one place at a time **without changing the meaning** (mechanically, such as `state != AWAKE` → `not state.get("awake")`)
- **Extracting the decision of commands** (6c): putting the decision written directly in `Region` into `check_command` easily changes the existing behavior. Confirm the regression with the existing tests of the commands
- **Holding an expression as a string**: it needs checking. Use only a limited syntax, and check and compile it at startup
- **The effective value affects several places**: it is used in the check of falling asleep and in the perception range. When the stance is entered, the perception range changes at once (the intended specification)
- **The size of the issue**: it includes the state dict, the stance, sleep, and the API, and is large (the Actions page was separated into the next issue). Do the tests and the pseudo-terminal check at each stage (6a to 6f). **At the time of 6b, run everything once and confirm**
- **Temporary hotkeys**: they are removed in the next issue. Make it possible to move them only by changing a row of the key table from `command` to `action` (no hotkey)
- **Effect on the game balance**: since no comparison with a golden result is done, a change of behavior caused by the rewriting is found, if debugging is needed, when the balance is adjusted

## Out of scope
- **The call-up screen (Actions page) and `GET /players/{id}/actions`** (the next issue)
- An animal must rest, and defining the necessary stances for each species, and making `rest` a stance (G-46)
- The effect of poison (`toxicity` and `poisoned`), the effects of `in_combat` and `falling` (the implementation of combat and falling; G-26, G-41), and showing warnings for `hungry` and `thirsty` (G-19)
- Waking up on one's own, naps and specifying the length of sleep, beds, voluntary sleep of NPCs, waking up by an outside stimulus (the rest of G-27), and the simplified calculation mode (`lite`)
- Changing the check of falling asleep, and individual differences of the factors of a stance by genes (up to the mechanism of copying them as the value of the individual)
- The comparison with a golden result (do it if necessary when the balance is adjusted)

## Settled points (the result of the points to confirm)
- The name of a stance is **`<action name>_stance`** (the sleep stance is `sleep_stance`; the factors `sleep_stance_factors` and the possible actions `sleep_stance_actions`)
- The temporary hotkeys are **`l` = `sleep` and `L` = `cancel_sleep`** (in the next issue they move to the Actions page and are removed)
- The reason for rejecting an impossible action is **the name of the state that blocks it** (`move` in the sleep stance gives `sleep_stance`; the same rule as the existing `asleep` and `unconscious`; if there are several, the first in the order of the registry)
- `state` in the API is a list that includes the derived states (all for the player itself, only the public states for other players)

## Results (2026-11-18)
- **6a `StateDict`, the registry, and rewriting the code**: new `core/state.py` (`StateDef`, `STATES`, `StateDict`, the derivation functions, the evaluation of expressions `compile_expr` / `apply_factor`, the checks `validate_factors` / `validate_registry`, `check_state_rules`). `Animal.state` is a `StateDict`. The stored states are `asleep`, `unconscious`, `sleep_stance`, `in_combat`, `falling`, and `poisoned` (the flags `in_combat` and `falling` moved into the dict; `poisoned` is only registered and its effect is not implemented). The derived states are `awake`, `hungry`, and `thirsty`. Writing is gathered in `Animal.set_state`, and the transition of consciousness is `Animal._become` (the only place that keeps the exclusion rule; when the player falls asleep or loses consciousness, it ends the stances of `ends_on`). `state.awake` is a fast path that holds the same value as `get("awake")` and is recomputed every time the state changes (for the judgments of every tick). `consciousness` and `set_consciousness` are for display, tests, and debugging
- **6b Rewriting the tests, the API, and the CLI**: the tests of the server (67 uses of `state`, 14 flags `in_combat` and `falling`) were rewritten mechanically. `state` in `to_dict` is a list of the names of the true states (derived states included), and `to_public` has only the public states. `stats_changed`, `state_entered`, `state_left`, and the CLI (`view.py`, `render.py`, `bot.py`) support the list. **The existing tests (after rewriting) all passed**
- **6c Unifying permission**: `check_command` in the new `core/commands.py` (blocking state, `exhausted`, `bad_dir`, `unsafe`, `sleep_disabled`, `unknown_command`; no side effects). `Animal.blocking_state` and `action_allowed` (the result is cached until the state changes). The command processing of `Region` uses `check_command`. `try_step`, `eat_here`, `drink`, and `Player.act` also use `action_allowed` (a move queued before entering the sleep stance is not executed during the stance). The reasons found only by executing (`no_prey`, `no_water`, `unsafe` (rest)) stay on the executing side
- **6d The sleep stance**: `sleep_stance` in the registry (`kind` = `stance`, `enter_action` = `sleep`, `cancel_action` = `cancel_sleep`, `ends_on` = falling asleep, unconsciousness, becoming unsafe). The species parameters `sleep_stance_factors` (default `{"wakefulness": "x * 0.2"}`) and `sleep_stance_actions` (default `{"move": False, "eat": False, "drink": False}`) are held in `SpeciesDef` as **immutable tuples** and **copied as dicts of the individual** when the individual is created. The effective wakefulness (`Animal.effective`, `awake_ratio`) is used in `x` of the check of falling asleep and in the effective perception range (the formulas are not changed). `sleep` and `cancel_sleep` (`COMMAND_TYPES`). The startup check (`config._validate_stances`)
- **6e API and CLI**: `states` (the registry) and the `sleep_stance_*` of `species` in `/config`. `wakefulness_pct` in `stats_changed` is the value after the factors. The visible events (`World.visible_events`) do not deliver the changes of non-public states to other players. CLI: the temporary hotkeys `l` and `L` (key table), the state line (`Ready to sleep`, `Resting, Ready to sleep`, `Sleeping`, `Unconscious`), the log, and the display of a rejection (`Rejected: blocked by Ready to sleep (press L to cancel)`)
- **6f Documents and tests**: `animal-states-spec.md` §4 and §5.5, `api-spec.md`, `cli-how-to-play.md`, TODO (G-27 marked as implemented, G-47 (the effect of poison) added). Tests: `server/tests/test_state.py` (22 new tests: the meaning of `StateDict`, derived states, the registry, the **test of the places that write (parsing the source)**, the check of the exclusion rule (simulation), the safety of expressions, repeating the stance, the factor, the perception range, the application to the check of falling asleep (`x` passed to `sleep_pressure`), leaving the stance on falling asleep and recovering from the real wakefulness, leaving on unconsciousness and when unsafe, `cancel_sleep`, impossible actions and their reasons, the dict of the individual, `check_command` without side effects, crossing a Region boundary, the public output, and the API), and the tests of the CLI (the list of states, the events of states, the display of a rejection, the temporary hotkeys, and the whole flow between the server and the CLI). **All 363 tests pass**
- **Pseudo-terminal check**: `l` → the state line `Ready to sleep` → `w` (display of the rejection) → `L` (cancel) → `l` → `h` (`l` and `L` in the Keys of Help) → `q`. As expected
- **Performance**: in the default world, one tick takes about 0.88 ms → **about 0.94 ms (+7%)** (reduced from +15% at first with the fast path `state.awake` and the path for stored names in `StateDict.get`)
- **Change of the factor** (2026-11-18): the default factor of the sleep stance was changed from `x * 0.5` to **`x * 0.2`** (the wakefulness ratio becomes one fifth). The probability of one natural check of falling asleep (Player; wakefulness 1.0 → 0.2 and so on) is 0.67 even at wakefulness 1.0 near the usual bedtime (rhythm 1), and almost 1 at 0.8 or lower. At the opposite time (rhythm 0) it is 1.2e-3 at 1.0, 1.2e-2 at 0.5, and 2.9e-2 at 0.3. So whether a player "is alert and cannot fall asleep" is now decided by the time (sleep cycle) rather than by the wakefulness
- **Remaining issues**: the call-up screen (Actions page) (the next issue), the effect of poison (G-47), making `rest` a stance and defining the necessary stances (G-46), the warning display for `hungry` and `thirsty` (G-19), and further speeding up the judgments of `state` (if necessary)
