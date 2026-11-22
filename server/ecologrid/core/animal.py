"""Animal: 移動・捕食・飲水。Player / Fox / Rabbit の共通基底(種はデータで区別)。"""
from typing import Optional

from .grid import DIRS, Pos, step
from .living import LivingBeing
from .memory import Memory, Params as MemoryParams, REPLAY_HOOKS
from .rng import make_rng
from .sleep import HISTORY_SIZE, level, peak_tick, sleep_pressure, sleep_probability, sleep_rhythm, wake_probability
from .state import CONSCIOUSNESS, STATES, StateDict, apply_factor, is_stored


class Animal(LivingBeing):
    ticks = True       # 代謝・覚醒度・スタミナ・AI が毎 tick 動く

    def __init__(self, spec, entity_id, grid_position, entity_name=""):
        super().__init__(spec, entity_id, grid_position, entity_name)
        self.next_move_tick = 0    # この絶対 tick 以降に次の 1 歩を進められる(move_interval_ticks ごと)
        self.next_action_tick = -1  # AI が次に行動する絶対 tick(-1 = 未設定。Region が位相を散らして設定)
        # 繁殖状態(tick の絶対値で保持。年齢 = 現在 tick - birth_tick)
        self.birth_tick = 0
        self.last_birth_tick = None
        self.gestation_started_tick = None   # 妊娠(抱卵)中ならその開始 tick
        # 能力値(個体の値。現状は種の値で一定。将来は遺伝子: TODO G-28)
        self.strength = spec.strength
        self.mind = spec.mind
        # 知覚(個体の値。種の標準値を発生時にコピー。将来は遺伝子)と、知覚した地図(状態)
        self.perception_range = spec.perception_range
        self.perception_interval_ticks = spec.perception_interval_ticks
        self.next_perception_tick = -1       # 次に知覚して地図を更新する tick(-1 = 未設定。bind で位相を散らす)
        self.map = None                      # PerceptMap(最初の知覚で作る)
        self.path = []                       # 経路探索の結果(方向のリスト)。目標が同じ間、使い回す
        self.path_goal = None
        self.memory = Memory(MemoryParams.from_spec(spec))   # 短期記憶(経験ログ)と長期記憶
        self.recalled = []                   # 場所による想起で、直近に思い出した記憶
        self._perceive_key = None            # 前回の知覚の (x, y, r, 植物の版番号)。同じなら走査を省く
        self.route_fail = {}                 # 経路が見つからなかった目標 -> 再探索してよい tick
        # 食べ物ごとの嗜好と吸収性(種の標準値を、発生時にコピー。当面は個体差なし。将来は遺伝子)。内部状態(公開しない)
        self.preference = {f: spec.preference_of(f) for f in spec.prey}
        self.absorption = {f: spec.absorption_of(f) for f in spec.prey}
        # 状態(名前 -> 真偽の dict。core/state.py)・覚醒度・スタミナ(docs/spec/animal-states-spec.md)
        self._blocked_cache = {}             # action 名 -> 止めている状態の名前(状態が変わると空にする)
        self.state = StateDict(self)
        # 体制ごとの係数と、できる action(種の標準値を、発生時にコピー。個体の dict。将来は遺伝子)
        for name, d in STATES.items():
            if d.kind == "stance":
                f, a = getattr(spec, f"{name}_factors", None), getattr(spec, f"{name}_actions", None)
                setattr(self, f"{name}_factors", dict(f) if f is not None else dict(d.factors))
                setattr(self, f"{name}_actions", dict(a) if a is not None else dict(d.actions))
        self.resting = False
        self.wakefulness = None              # None = 未初期化。bind() で最大値の 30〜100% の乱数にする
        self.stamina = self.max_health       # 最大値は現在の Health
        self.last_action_tick = -10**9       # 最後に行動(移動・捕食・飲水)した tick。動作中/静止の判定に使う
        self.sleep_onset_history = []        # 直近 5 回の入眠 tick

    # --- 能力値 ---
    @property
    def wakefulness_max(self) -> float:
        return self.mind * self.spec.wakefulness_per_mind

    @property
    def attack_power(self) -> float:
        return self.strength * self.spec.attack_coef

    @property
    def defense_power(self) -> float:
        return self.strength * self.spec.defense_coef

    @property
    def stamina_max(self) -> float:
        return self.health

    def raw_awake_ratio(self, region) -> float:
        """本来の覚醒度の割合(0〜1)。睡眠を無効にした個体(Player の設定)は 1。"""
        if not self.sleep_enabled(region):
            return 1.0
        wmax = self.wakefulness_max
        return min(1.0, max(0.0, self.wakefulness / wmax)) if wmax > 0 else 1.0

    def awake_ratio(self, region) -> float:
        """有効な覚醒度の割合(0〜1)= 本来の割合に、体制の係数をかけた後の値。入眠体制の間は、低く扱われる。"""
        return self.effective("wakefulness", self.raw_awake_ratio(region))

    def effective_range(self, region) -> int:
        """有効な知覚の範囲 r = floor(perception_range × 覚醒度の割合)。眠っている間は 0(自分のセルだけ)。
        知覚するのは、自分のセル + 距離 r(チェビシェフ距離)まで。"""
        if not self.state.get("awake"):
            return 0
        return int(self.perception_range * self.awake_ratio(region))

    def perceive_if_due(self, region) -> None:
        if self.state.awake and region.tick_no >= self.next_perception_tick:
            from .perception import perceive
            perceive(self, region)
            self.next_perception_tick = region.tick_no + max(1, self.perception_interval_ticks)

    def bind(self, region) -> None:
        """Region への登録時: 覚醒度が未設定なら、最大値の 30〜100% の決定論的な乱数にする(周期を散らす)。"""
        if self.next_perception_tick < 0:     # 知覚の周期の位相を散らす(全個体が同じ tick に走査する負荷の集中を避ける)
            self.next_perception_tick = region.tick_no + make_rng(
                region.cfg.seed, "perc0", self.entity_id).randrange(max(1, self.perception_interval_ticks))
        if self.wakefulness is None:
            self.wakefulness = self.wakefulness_max * (0.3 + 0.7 * make_rng(region.cfg.seed, "wake0", self.entity_id).random())

    def sleep_enabled(self, region) -> bool:
        s = self.spec
        return s.sleeps and (s.cls != "Player" or region.cfg.player_sleep_enabled)

    # --- 状態(core/state.py)。state[...] に書いてよいのは、set_state と load_state(reset)だけ ---
    def set_state(self, name: str, value: bool, region=None, reason: str = "") -> bool:
        """保存する状態を書く(唯一の書き込み場所)。値が変わったら True。region があり、登録表で emit_event のときは、
        state_entered / state_left を出す(asleep・unconscious は、fell_asleep・woke_up・fainted があるので出さない)。"""
        before = dict.get(self.state, name, False)
        self.state[name] = value
        changed = before != bool(value)
        d = STATES[name]
        if changed and region is not None and d.emit_event:
            extra = {"player_id": self.player_id} if getattr(self, "player_id", "") else {}
            region.emit("state_entered" if value else "state_left", id=self.entity_id,
                        pos=[self.grid_position.x, self.grid_position.y], species=self.species,
                        state=name, kind=d.kind, reason=reason, **extra)
        return changed

    def _become(self, region, which: str, reason: str = "") -> None:
        """意識の状態の遷移(awake / asleep / unconscious)。排他のルールを守る、唯一の場所。眠った・意識を失ったとき、
        ends_on に含まれる体制を終える。region が None なら、イベントは出さない。"""
        for name in ("asleep", "unconscious"):
            self.set_state(name, name == which, region)
        if which != "awake":
            self._end_stances(region, which, reason)

    def _end_stances(self, region, event: str, reason: str = "") -> None:
        for name in self.state.stored():
            d = STATES[name]
            if d.kind == "stance" and event in d.ends_on:
                self.set_state(name, False, region, reason=event)

    def set_consciousness(self, which: str) -> None:
        """意識の状態を直接設定する(テスト・デバッグ用。イベントは出さない)。which = awake / asleep / unconscious。"""
        assert which in CONSCIOUSNESS, which
        self._become(None, which)

    @property
    def consciousness(self) -> str:
        """意識の状態の要約(awake / asleep / unconscious)。表示・ログ・テスト用。判定には state.get を使う。"""
        return "unconscious" if dict.get(self.state, "unconscious") else "asleep" if dict.get(self.state, "asleep") else "awake"

    def _state_actions(self, name: str) -> dict:
        a = getattr(self, f"{name}_actions", None)
        return a if a is not None else STATES[name].actions

    def _state_factors(self, name: str) -> dict:
        f = getattr(self, f"{name}_factors", None)
        return f if f is not None else STATES[name].factors

    def blocking_state(self, action: str):
        """その action を止めている状態の名前(登録表の順で最初のもの)。止めていなければ None。
        体制は、自分の解除の action(cancel_<action>)を、止めない。結果は、状態が変わるまで、キャッシュする。"""
        cache = self._blocked_cache
        if action in cache:
            return cache[action]
        res = None
        for name, d in STATES.items():
            if d.derive is not None or not dict.get(self.state, name, False):
                continue
            if d.cancel_action and action == d.cancel_action:
                continue
            acts = self._state_actions(name)
            ok = acts[action] if action in acts else acts.get("*", True)
            if ok is False:
                res = name
                break
        cache[action] = res
        return res

    def action_allowed(self, action: str) -> bool:
        return self.blocking_state(action) is None

    def effective(self, param: str, value: float) -> float:
        """パラメータの値に、入っている状態(入った順)の係数を適用した後の値(登録された param だけ)。"""
        st = self.state
        if not dict.__len__(st):
            return value
        for name in dict.keys(st):
            src = self._state_factors(name).get(param)
            if src:
                value = apply_factor(src, value, param)
        return value

    # --- 状態の判定 ---
    def is_safe(self) -> bool:
        """安全な状態: 戦闘状態でない かつ 落下中でない。"""
        st = self.state
        return not (dict.get(st, "in_combat", False) or dict.get(st, "falling", False))

    def is_hungry(self) -> bool:
        return self.energy < self.spec.hunger_threshold

    def is_thirsty(self) -> bool:
        return self.hydration < self.spec.thirst_threshold

    def can_eat(self) -> bool:
        """食事が可能: 意識がある ∧ 体が動く(Energy > 0、スタミナが捕食の消費以上) ∧ 安全。"""
        return (self.state.get("awake") and self.action_allowed("eat") and self.energy > 0
                and self.stamina >= self.spec.stamina_cost_eat and self.is_safe())

    def _acting(self, region) -> bool:
        """動作中: 覚醒していて休憩中でなく、直近 idle_after_ticks 以内に行動した。"""
        return (self.state.awake and not self.resting
                and region.tick_no - self.last_action_tick < self.spec.idle_after_ticks)

    def _deep_rest(self) -> bool:
        return self.resting or not self.state.awake

    def mark_action(self, region) -> None:
        self.last_action_tick = region.tick_no
        self.stop_rest(region)

    # --- 記憶(docs/spec/animal-memory-spec.md) ---
    def _memory_state(self, region) -> tuple:
        """経験の state: 内部状態(渇き・空腹・体力・スタミナの深さ。0〜255)+ 知覚の範囲の地図への参照(中心・r・版番号)。"""
        s = self.spec
        return (int(255 * level(self.hydration, s.thirst_threshold)), int(255 * level(self.energy, s.hunger_threshold)),
                int(255 * self.health / self.max_health), int(255 * self.stamina / max(self.max_health, 1e-9)),
                self.grid_position.x, self.grid_position.y, self.effective_range(region),
                0 if self.map is None else self.map.version)

    def remember(self, region, action: str, outcome: str, subject: str = "", reward=None) -> None:
        pos = self.grid_position
        self.memory.record(region.cfg.seed, self.entity_id, region.tick_no, action, outcome, subject,
                           pos.x, pos.y, reward, self._memory_state(region))

    def on_map_update(self, region, version: int, cells, resources) -> None:
        self.memory.record_map_update(region.tick_no, version, cells, resources)

    def organize_memory(self, region) -> None:
        """入眠・意識消失のとき、記憶を整理する(忘却 → 統合 → 短期記憶を空に)。地図の資源の層も忘却する。"""
        p = self.memory.p
        if self.map is not None:
            self.map.decay(p.forgetting_factor, p.forgetting_threshold)
        self.memory.organize(region.tick_no, replay=(lambda log: [h(self, log) for h in REPLAY_HOOKS]) if REPLAY_HOOKS else None)

    def recall_goal(self, region, action: str, subject=None):
        """目標指向の想起: 記憶から、action の目標の場所(x, y)を 1 つ選ぶ。なければ None。"""
        if not region.cfg.recall_enabled:
            return None
        pos = self.grid_position
        r = self.memory.recall_goal(region.cfg.seed, self.entity_id, region.tick_no, action, subject, pos.x, pos.y)
        return None if r is None else (r[0], r[1])

    # --- 休憩 ---
    def start_rest(self, region) -> bool:
        if not self.state.get("awake") or self.resting or not self.is_safe():
            return False
        self.resting = True
        region.emit("rest_started", id=self.entity_id, pos=[self.grid_position.x, self.grid_position.y])
        self.remember(region, "rest", "rested")
        return True

    def stop_rest(self, region) -> None:
        if self.resting:
            self.resting = False
            region.emit("rest_ended", id=self.entity_id, pos=[self.grid_position.x, self.grid_position.y])

    # --- tick ---
    def _hydration_decay_now(self, region) -> float:
        s = self.spec
        if s.hydration_rate_factor > 0:      # 動物の水分は気温だけで減る(§6.9)
            from .species import hydration_rate_fraction
            return s.max_hydration * s.hydration_rate_factor * hydration_rate_fraction(region.temperature_c)
        return self._hydration_decay

    def _regen_now(self, region) -> float:
        if self._deep_rest():
            return self.spec.rest_regen_per_tick
        if self._acting(region):
            return 0.0
        return self._regen

    def tick(self, region) -> None:
        super().tick(region)                 # 代謝(水分・Energy・体力)
        if self.dead:
            return
        self._update_wakefulness(region)
        self._update_stamina(region)
        if self.resting and not self.is_safe():
            self.stop_rest(region)
        if dict.__len__(self.state) and not self.is_safe():      # 安全でなくなると、終わる体制がある(ends_on)
            self._end_stances(region, "unsafe")
        self.perceive_if_due(region)             # 知覚の周期ごとに、地図を更新する
        self.act(region)

    def _update_wakefulness(self, region) -> None:
        if not self.sleep_enabled(region):
            return
        s, wmax = self.spec, self.wakefulness_max
        if self.state.awake:           # 覚醒中は一定量ずつ減る(行動によらない。休憩中も減る)
            self.wakefulness -= s.wakefulness_decay_per_tick
            if self.wakefulness <= 0:
                self.wakefulness = 0.0
                self._faint(region)
        else:                                 # 睡眠・意識消失の間は回復する
            self.wakefulness = min(wmax, self.wakefulness + s.wakefulness_recovery_per_tick)
            if self.state.get("unconscious") and self.wakefulness >= s.consciousness_regain_level * wmax:
                self._become(region, "asleep")

    def _update_stamina(self, region) -> None:
        """スタミナの純増減 = 回復 − 気温の消費。回復は体力と同じ状態のとき、体力の 10 倍の速さ(欠乏のときは止まる)。"""
        s = self.spec
        rec = 0.0
        if self.hydration > 0 and self.energy > 0:
            if self._deep_rest():
                rec = s.rest_regen_per_tick * s.stamina_recovery_multiplier
            elif not self._acting(region):
                rec = self._regen * s.stamina_recovery_multiplier
        t = region.temperature_c
        drain = s.stamina_temp_drain_per_degree * max(0.0, t - s.comfort_high_c, s.comfort_low_c - t)
        self.stamina = min(self.health, max(0.0, self.stamina + rec - drain))   # 常に stamina ≤ health

    def _record_onset(self, region) -> None:
        self.sleep_onset_history.append(region.tick_no)
        del self.sleep_onset_history[:-HISTORY_SIZE]

    def _faint(self, region) -> None:
        self.stop_rest(region)
        self._become(region, "unconscious")
        self._record_onset(region)
        region.emit("fainted", id=self.entity_id, pos=[self.grid_position.x, self.grid_position.y])
        self.remember(region, "sleep", "slept")
        self.organize_memory(region)             # 意識消失(強制睡眠)でも整理する

    def fall_asleep(self, region) -> None:
        self.stop_rest(region)
        self._become(region, "asleep")
        self._record_onset(region)
        region.emit("fell_asleep", id=self.entity_id, pos=[self.grid_position.x, self.grid_position.y])
        self.remember(region, "sleep", "slept")
        self.organize_memory(region)             # 入眠のとき、記憶を整理する(忘却 → 統合 → 短期記憶を空に)

    def wake_up(self, region) -> None:
        self._become(region, "awake")
        region.emit("woke_up", id=self.entity_id, pos=[self.grid_position.x, self.grid_position.y])

    def sleep_check(self, region, tick_no: int) -> None:
        """入眠(覚醒中)・起床(睡眠中)の確率判定。sleep_check_interval_ticks ごとに呼ばれる。意識消失の間は判定しない。"""
        if self.dead or not self.sleep_enabled(region) or self.state.get("unconscious"):
            return
        s = self.spec
        x = self.wakefulness / self.wakefulness_max
        hunger, thirst = level(self.energy, s.hunger_threshold), level(self.hydration, s.thirst_threshold)
        rng = make_rng(region.cfg.seed, "sleep", self.entity_id, tick_no)   # 決定論的(再現可能)
        if self.state.get("awake"):
            if not self.is_safe():
                return
            x = self.effective("wakefulness", x)          # 体制の間は、覚醒度が低く扱われ、入眠しやすい(式は変えない)
            peak = peak_tick(self.sleep_onset_history, s.sleep_cycle_ticks, s.sleep_standard_onset_tick)
            p = sleep_probability(sleep_pressure(x, sleep_rhythm(tick_no, s.sleep_cycle_ticks, peak),
                                                 s, hunger, thirst), s)
            if p >= 1.0 or rng.random() < p:
                self.fall_asleep(region)
        elif rng.random() < wake_probability(x, s, hunger, thirst):
            self.wake_up(region)

    def act(self, region) -> None:
        """AI の行動。move_interval_ticks ごとに 1 回だけ考える(毎 tick は考えない)。眠っている間は考えない。"""
        if not self.state.awake or region.tick_no < self.next_action_tick:
            return
        from .ai import think
        think(self, region)
        self.next_action_tick = region.tick_no + max(1, self.spec.move_interval_ticks)

    # --- 行動 ---
    def try_step(self, direction: str, region) -> bool:
        """Energy が残り、次に動ける tick に達していて、行き先が歩ける場合のみ移動。
        移動後は move_interval_ticks の間、次の 1 歩を進められない。"""
        if (not self.action_allowed("move") or self.energy <= 0 or region.tick_no < self.next_move_tick
                or self.stamina < self.spec.stamina_cost_move):
            return False
        target = step(self.grid_position, direction)
        if not region.walkable(target):
            return False
        interval = max(1, self.spec.move_interval_ticks)
        self.next_move_tick = region.tick_no + interval
        self.energy = max(0.0, self.energy - self.spec.move_energy_cost)
        self.stamina -= self.spec.stamina_cost_move
        self.mark_action(region)
        region.move(self, target, duration_ticks=interval)
        if self.entity_id in region.entities:   # Region 越境時はここで終わり
            self.on_enter(region)
        return True

    def on_enter(self, region) -> None:
        """セルに入ったとき: 種子は踏まれて消滅。"""
        for e in region.entities_at(self.grid_position):
            if e.species == "Seed":
                region.remove(e, "trampled")
        self.after_enter(region)

    def after_enter(self, region) -> None:
        """AI 動物は、空腹で食事が可能なとき、接触した獲物を自動で捕食する(Player は eat コマンド)。"""
        if self.is_hungry() and self.can_eat():
            self.eat_here(region)

    def eat_here(self, region, record_failure: bool = False) -> bool:
        """同一セルの獲物を 1 体消費し、そのエネルギーの一定割合を加算する(スタミナを消費)。"""
        if not self.action_allowed("eat") or self.stamina < self.spec.stamina_cost_eat:
            return False
        here = [e for e in region.entities_at(self.grid_position)
                if e is not self and e.species in self.spec.prey and not e.dead]
        if not here:
            if record_failure:
                self.remember(region, "eat", "no_prey")
            return False
        here.sort(key=lambda e: (self.spec.prey.index(e.species), e.entity_id))
        prey = here[0]
        gain = prey.energy * self.absorption.get(prey.species, self.spec.absorption)   # 得る Energy = 獲物の Energy × 吸収性
        region.remove(prey, "eaten", by=self.entity_id)
        self.energy = min(self.spec.max_energy, self.energy + gain)
        self.stamina -= self.spec.stamina_cost_eat
        self.mark_action(region)
        self.remember(region, "eat", "ate", prey.species, reward=self.preference.get(prey.species, 0.0))   # reward = 嗜好
        return True

    def drink(self, region) -> bool:
        if not self.action_allowed("drink") or self.stamina < self.spec.stamina_cost_drink:
            return False
        if not region.adjacent_water(self.grid_position):
            self.remember(region, "drink", "no_water")      # 水のない場所で飲もうとした(失敗の結果も記憶する)
            return False
        self.hydration = min(self.spec.max_hydration, self.hydration + self.spec.drink_amount)
        self.stamina -= self.spec.stamina_cost_drink
        self.mark_action(region)
        self.remember(region, "drink", "drank")
        return True

    def step_toward(self, target: Pos, region) -> bool:
        """目標に近づく向きを(距離が縮む順に)試す。"""
        dx, dy = target.x - self.grid_position.x, target.y - self.grid_position.y
        cands = []
        if dx:
            cands.append(("e" if dx > 0 else "w", abs(dx)))
        if dy:
            cands.append(("s" if dy > 0 else "n", abs(dy)))
        cands.sort(key=lambda c: -c[1])
        for d, _ in cands:
            if self.try_step(d, region):
                return True
        return False

    def random_step(self, region) -> bool:
        dirs = sorted(DIRS)
        region.rng.shuffle(dirs)
        return any(self.try_step(d, region) for d in dirs)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update(next_move_tick=self.next_move_tick, next_action_tick=self.next_action_tick,
                 birth_tick=self.birth_tick, last_birth_tick=self.last_birth_tick,
                 gestation_started_tick=self.gestation_started_tick,
                 state=self.state.active(), resting=self.resting,
                 wakefulness=None if self.wakefulness is None else round(self.wakefulness, 3),
                 wakefulness_max=round(self.wakefulness_max, 3),
                 stamina=round(self.stamina, 3), stamina_max=round(self.stamina_max, 3),
                 mind=self.mind, strength=self.strength, max_health=self.max_health,
                 last_action_tick=self.last_action_tick, sleep_onset_history=list(self.sleep_onset_history))
        return d

    def to_public(self) -> dict:
        d = super().to_public()
        d["state"] = self.state.active(public_only=True)     # 他のプレイヤーに見せてよい状態だけ(awake・asleep・unconscious。眠っている個体は、大文字で描画する)
        return d

    def to_state(self) -> dict:
        d = super().to_state()
        d.update(preference=dict(self.preference), absorption=dict(self.absorption),
                 perception_range=self.perception_range, perception_interval_ticks=self.perception_interval_ticks,
                 next_perception_tick=self.next_perception_tick,
                 map=None if self.map is None else self.map.to_state(),
                 memory=self.memory.to_state())
        return d

    def load_state(self, d: dict) -> None:
        super().load_state(d)
        self.preference.update(d.get("preference", {}))
        self.absorption.update(d.get("absorption", {}))
        self.perception_range = d.get("perception_range", self.perception_range)
        self.perception_interval_ticks = d.get("perception_interval_ticks", self.perception_interval_ticks)
        self.next_perception_tick = d.get("next_perception_tick", self.next_perception_tick)
        if d.get("map") is not None:
            from .percept_map import PerceptMap
            self.map = PerceptMap.from_state(d["map"])
        if d.get("memory") is not None:
            self.memory = Memory.from_state(d["memory"])
        self.next_move_tick = d.get("next_move_tick", 0)
        self.next_action_tick = d.get("next_action_tick", -1)
        self.birth_tick = d.get("birth_tick", 0)
        self.last_birth_tick = d.get("last_birth_tick")
        self.gestation_started_tick = d.get("gestation_started_tick")
        self.state.reset(n for n in d.get("state", ()) if is_stored(n))    # 保存する状態だけ(導く状態は、読み飛ばす)
        self.resting = d.get("resting", False)
        self.wakefulness = d.get("wakefulness")
        self.mind, self.strength = d.get("mind", self.mind), d.get("strength", self.strength)
        self.max_health = d.get("max_health", self.max_health)
        self.stamina = d.get("stamina", self.stamina)
        self.last_action_tick = d.get("last_action_tick", self.last_action_tick)
        self.sleep_onset_history = list(d.get("sleep_onset_history", []))
        # (in_combat・falling は、state に含まれる)
