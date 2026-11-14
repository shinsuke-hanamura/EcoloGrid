"""動物(人間を含む)の記憶。docs/spec/animal-memory-spec.md(確定)。

- 短期記憶 = 経験ログ(`log`): 行動・出来事のたびに追加される(上限なし。寝たら整理して空)。RL の経験バッファを兼ねる。
- 長期記憶(`long`): 整理された集計表。key = (action, outcome, subject, d, 区画)。結果ごとに別の項目(打ち消さない)。
- 強さ = (1 + |reward|) × 乱数(0〜1)。reward は (action, outcome) の表(`ate` は嗜好)。
- 整理(入眠・意識消失時のみ): (0) リプレイのフック → (1) 忘却(全長期記憶に忘却係数をかけ、閾値未満を削除)
  → (2) ログを長期記憶に統合 → (3) ログを空に。
- 想起 A(目標指向): 区画の全結果から強さに比例して代表の結果を選び、分類(目標・失敗・忌避)で向かう・外す。
- 想起 B(場所による): 区画に入ったとき、強い記憶ほど思い出しやすい(p = 1 − exp(−強さ/τ))。
"""
import math
from typing import Dict, List, Optional, Tuple

from .rng import make_rng

GOAL, FAIL, AVOID, STEP = "goal", "fail", "avoid", "step"      # 結果の分類

# 整理の直前に呼ばれる RL のリプレイのフック: hook(animal, log)。当面は空(段階 2 以降で RL の更新を登録する)
REPLAY_HOOKS: List = []


def _state_to_json(state):
    """経験の state(内部状態 + 地図への参照。全ステップのログでは窓のバイト列を含む)を、JSON にできる形に。"""
    if state is None:
        return None
    return [{"b64": __import__("base64").b64encode(v).decode("ascii")} if isinstance(v, (bytes, bytearray)) else v
            for v in state]


def _state_from_json(state):
    if state is None:
        return None
    return tuple(__import__("base64").b64decode(v["b64"]) if isinstance(v, dict) else v for v in state)


class Rule:
    """(action, outcome) の規則: reward(None = 呼び出し側が決める。`ate` は嗜好)、分類、丸めの距離 D。"""
    __slots__ = ("reward", "cls", "d")

    def __init__(self, reward: Optional[float], cls: str, d: int):
        self.reward, self.cls, self.d = reward, cls, d


# 報酬の表(種ごとに固定。当面は全種同じ)。D: drink・rest は 2、それ以外は 4(記憶ごとに保存する)。
RULES: Dict[Tuple[str, str], Rule] = {
    ("drink", "drank"): Rule(10.0, GOAL, 2),
    ("drink", "no_water"): Rule(-3.0, FAIL, 2),
    ("eat", "ate"): Rule(None, GOAL, 4),                 # reward = 嗜好 preference[subject]
    ("eat", "no_prey"): Rule(-2.0, FAIL, 4),
    ("rest", "rested"): Rule(3.0, GOAL, 2),
    ("sleep", "slept"): Rule(4.0, GOAL, 4),
    ("none", "hurt"): Rule(-8.0, AVOID, 4),              # 落下(-8)。攻撃は -10(将来)
    ("none", "born"): Rule(3.0, GOAL, 4),
    ("none", "gave_birth"): Rule(8.0, GOAL, 4),
    ("tell", "told"): Rule(2.0, GOAL, 4),
    ("move", "moved"): Rule(0.0, STEP, 4),               # 全ステップのログ(memorise_all_steps)用
    ("idle", "idle"): Rule(0.0, STEP, 4),
}


class Params:
    """個体の記憶のパラメータ(種の標準値を、発生時にコピー。将来は遺伝子)。"""
    __slots__ = ("recall_tau", "recall_max", "recall_refractory_ticks", "recall_distance_exponent",
                 "forgetting_factor", "forgetting_threshold")

    def __init__(self, recall_tau=5.0, recall_max=3, recall_refractory_ticks=600, recall_distance_exponent=0.0,
                 forgetting_factor=0.9, forgetting_threshold=0.5):
        self.recall_tau, self.recall_max = recall_tau, recall_max
        self.recall_refractory_ticks = recall_refractory_ticks
        self.recall_distance_exponent = recall_distance_exponent
        self.forgetting_factor, self.forgetting_threshold = forgetting_factor, forgetting_threshold

    @classmethod
    def from_spec(cls, spec) -> "Params":
        return cls(spec.recall_tau, spec.recall_max, spec.recall_refractory_ticks, spec.recall_distance_exponent,
                   spec.forgetting_factor, spec.forgetting_threshold)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}


class Experience:
    """短期記憶(経験ログ)の 1 件。state は、action の際の状態: 内部状態(量子化)+ 知覚の範囲の地図への参照。"""
    __slots__ = ("tick", "action", "outcome", "subject", "x", "y", "d", "reward", "strength", "state")

    def __init__(self, tick, action, outcome, subject, x, y, d, reward, strength, state=None):
        self.tick, self.action, self.outcome, self.subject = tick, action, outcome, subject
        self.x, self.y, self.d, self.reward, self.strength, self.state = x, y, d, reward, strength, state

    def to_list(self) -> list:
        return [self.tick, self.action, self.outcome, self.subject, self.x, self.y, self.d,
                self.reward, self.strength, _state_to_json(self.state)]

    def to_dict(self) -> dict:
        return {"tick": self.tick, "action": self.action, "outcome": self.outcome, "subject": self.subject,
                "x": self.x, "y": self.y, "d": self.d, "reward": self.reward, "strength": round(self.strength, 4),
                "state": _state_to_json(self.state)}


class MapUpdate:
    """経験ログに残す、地図の更新の差分(日中の状態の再構成用)。長期記憶には統合しない。"""
    __slots__ = ("tick", "version", "cells", "resources")

    def __init__(self, tick, version, cells, resources):
        self.tick, self.version, self.cells, self.resources = tick, version, cells, resources


class LongEntry:
    """長期記憶の 1 件(集計)。座標は最初の記憶の座標。count・reward_sum・strength は忘却で減る(小数になる)。"""
    __slots__ = ("action", "outcome", "subject", "d", "bx", "by", "x", "y", "count", "reward_sum", "strength",
                 "first_tick", "last_tick")

    def __init__(self, action, outcome, subject, d, bx, by, x, y, count, reward_sum, strength, first_tick, last_tick):
        self.action, self.outcome, self.subject, self.d, self.bx, self.by = action, outcome, subject, d, bx, by
        self.x, self.y, self.count, self.reward_sum, self.strength = x, y, count, reward_sum, strength
        self.first_tick, self.last_tick = first_tick, last_tick

    @property
    def key(self) -> tuple:
        return (self.action, self.outcome, self.subject, self.d, self.bx, self.by)

    @property
    def mean_reward(self) -> float:
        return self.reward_sum / self.count if self.count else 0.0

    def to_list(self) -> list:
        return [self.action, self.outcome, self.subject, self.d, self.bx, self.by, self.x, self.y,
                self.count, self.reward_sum, self.strength, self.first_tick, self.last_tick]

    def to_dict(self) -> dict:
        return {"action": self.action, "outcome": self.outcome, "subject": self.subject, "d": self.d,
                "x": self.x, "y": self.y, "count": round(self.count, 3), "reward_sum": round(self.reward_sum, 3),
                "mean_reward": round(self.mean_reward, 3), "strength": round(self.strength, 4),
                "first_tick": self.first_tick, "last_tick": self.last_tick}


def _key_text(k: tuple) -> str:
    return ":".join(str(v) for v in k)       # 決定論的な乱数の種に使う(str の hash は実行ごとに変わるので使わない)


class Memory:
    def __init__(self, params: Optional[Params] = None):
        self.p = params or Params()
        self.log: List[object] = []                         # Experience | MapUpdate(古い順)
        self.long: Dict[tuple, LongEntry] = {}              # 長期記憶
        self._agg: Dict[tuple, LongEntry] = {}              # ログ(当日分)の、key ごとの集計。想起と整理に使う
        self.d_values: set = set()
        self._long_place: Dict[tuple, List[LongEntry]] = {}      # (d, bx, by) -> 長期記憶(整理のたびに作り直す)
        self._agg_place: Dict[tuple, List[LongEntry]] = {}       # (d, bx, by) -> 当日の集計(追加のたびに更新)
        self._last_place_keys: Optional[tuple] = None
        self._last_recalled: Dict[str, int] = {}
        self.seq = 0
        self.organized = 0                                  # 整理した回数
        self.last_organize_tick = -1

    # --- 記録 ---
    def record(self, seed: int, owner_id: str, tick: int, action: str, outcome: str, subject: str,
               x: int, y: int, reward: Optional[float] = None, state=None) -> Experience:
        rule = RULES[(action, outcome)]
        r = rule.reward if reward is None else reward
        if r is None:
            r = 0.0
        self.seq += 1
        s = (1.0 + abs(r)) * make_rng(seed, "strength", owner_id, tick, self.seq).random()     # 強さ
        e = Experience(tick, action, outcome, subject or "", x, y, rule.d, r, s, state)
        self.log.append(e)
        if rule.cls != STEP:
            self._aggregate(e)
        return e

    def record_step(self, tick: int, action: str, outcome: str, x: int, y: int, state=None) -> Experience:
        """全ステップのログ(memorise_all_steps)のエントリ。長期記憶には統合しない(RL のリプレイ用)。"""
        rule = RULES[(action, outcome)]
        e = Experience(tick, action, outcome, "", x, y, rule.d, 0.0, 0.0, state)
        self.log.append(e)
        return e

    def record_map_update(self, tick: int, version: int, cells, resources) -> None:
        self.log.append(MapUpdate(tick, version, cells, resources))

    def _aggregate(self, e: Experience) -> None:
        k = (e.action, e.outcome, e.subject, e.d, e.x // e.d, e.y // e.d)
        a = self._agg.get(k)
        if a is None:
            a = self._agg[k] = LongEntry(e.action, e.outcome, e.subject, e.d, k[4], k[5], e.x, e.y, 0, 0.0, 0.0, e.tick, e.tick)
            self._agg_place.setdefault((e.d, k[4], k[5]), []).append(a)
            self.d_values.add(e.d)
        a.count += 1
        a.reward_sum += e.reward
        a.strength += e.strength
        a.last_tick = e.tick

    # --- 整理(入眠・意識消失時のみ) ---
    def organize(self, tick: int, replay=None) -> dict:
        p = self.p
        if replay is not None:
            replay(self.log)                                 # (0) RL のリプレイ(整理の直前。当面はフックだけ)
        ff, th = p.forgetting_factor, p.forgetting_threshold
        for e in self.long.values():                         # (1) 忘却
            e.count *= ff
            e.reward_sum *= ff
            e.strength *= ff
        forgotten = [k for k, e in self.long.items() if e.strength < th]
        for k in forgotten:
            del self.long[k]
        for k, a in self._agg.items():                       # (2) 統合(今日の分は、今回は忘却されない)
            e = self.long.get(k)
            if e is None:
                self.long[k] = a
            else:
                e.count += a.count
                e.reward_sum += a.reward_sum
                e.strength += a.strength
                e.last_tick = max(e.last_tick, a.last_tick)
        n_log = len(self.log)
        self.log, self._agg, self._agg_place = [], {}, {}    # (3) ログを空に
        self._rebuild_index()
        self.organized += 1
        self.last_organize_tick = tick
        return {"forgotten": len(forgotten), "log_entries": n_log, "long": len(self.long)}

    def _rebuild_index(self) -> None:
        self._long_place = {}
        self.d_values = {e.d for e in self._agg.values()}
        for e in self.long.values():
            self._long_place.setdefault((e.d, e.bx, e.by), []).append(e)
            self.d_values.add(e.d)
        self._last_place_keys = None

    # --- 想起 A(目標指向): 強さに比例する選択 ---
    def _candidates(self, action: str, subject: Optional[str]) -> Dict[tuple, Dict[str, list]]:
        """(action, subject)の区画ごとの、結果ごとの [強さの合計, 最初の座標(x, y)]。長期記憶 + 当日の集計。"""
        out: Dict[tuple, Dict[str, list]] = {}
        for src in (self.long.values(), self._agg.values()):
            for e in src:
                if e.action != action or (subject is not None and e.subject != subject):
                    continue
                cell = out.setdefault((e.d, e.bx, e.by), {})
                ent = cell.get(e.outcome)
                if ent is None:
                    cell[e.outcome] = [e.strength, e.x, e.y]
                else:
                    ent[0] += e.strength
        return out

    def recall_goal(self, seed: int, owner_id: str, tick: int, action: str, subject: Optional[str],
                    x: int, y: int) -> Optional[Tuple[int, int, str, float]]:
        """action(+ subject)の目標の場所を、記憶から 1 つ選ぶ。(x, y, 代表の結果, 重み)。なければ None。
        区画の全結果から、強さに比例して代表の結果を選ぶ(分類が「目標」でなければ区画を外す)。
        区画どうしも、代表の結果の強さに比例して選ぶ(距離の割引 κ は当面 0)。"""
        cands = self._candidates(action, subject)
        if not cands:
            return None
        kappa = self.p.recall_distance_exponent
        chosen = []
        for bucket in sorted(cands):
            outs = cands[bucket]
            names = sorted(outs)
            total = sum(outs[o][0] for o in names)
            if total <= 0:
                continue
            rng = make_rng(seed, "recallg", owner_id, tick, action, subject or "", _key_text(bucket))
            pick, acc = names[-1], 0.0
            u = rng.random() * total
            for o in names:
                acc += outs[o][0]
                if u < acc:
                    pick = o
                    break
            if RULES.get((action, pick), RULES[(action, names[0])]).cls != GOAL:
                continue                                      # 失敗・忌避が代表になった区画は、今回は候補から外れる
            w = outs[pick][0]
            if kappa:
                w *= (1.0 / (1.0 + max(abs(outs[pick][1] - x), abs(outs[pick][2] - y)))) ** kappa
            chosen.append((w, bucket, pick, outs[pick][1], outs[pick][2]))
        if not chosen:
            return None
        total = sum(c[0] for c in chosen)
        if total <= 0:
            return None
        rng = make_rng(seed, "recallb", owner_id, tick, action, subject or "")
        u, acc = rng.random() * total, 0.0
        for w, bucket, pick, bx, by in chosen:
            acc += w
            if u < acc:
                return (bx, by, pick, w)
        w, bucket, pick, bx, by = chosen[-1]
        return (bx, by, pick, w)

    # --- 想起 B(場所による) ---
    def recall_place(self, seed: int, owner_id: str, tick: int, x: int, y: int, commit: bool = True) -> List[LongEntry]:
        """いま居る場所(丸めた座標が同じ)の記憶を、強さに応じた確率で思い出す。強い記憶ほど思い出しやすい。
        区画に入ったとき(前回の判定と区画が変わったとき)だけ判定する。不応期・件数の上限あり。"""
        ds = sorted(self.d_values)
        keys = tuple((d, x // d, y // d) for d in ds)
        if keys == self._last_place_keys:
            return []
        out: List[LongEntry] = []
        p = self.p
        for k in keys:
            for e in list(self._long_place.get(k, ())) + list(self._agg_place.get(k, ())):
                ek = _key_text(e.key)
                if tick - self._last_recalled.get(ek, -10 ** 12) < p.recall_refractory_ticks:
                    continue
                prob = 1.0 - math.exp(-e.strength / p.recall_tau)
                if make_rng(seed, "recallp", owner_id, tick, ek).random() < prob:
                    out.append(e)
        out.sort(key=lambda e: (-e.strength, e.key))
        out = out[:p.recall_max]
        if commit:
            self._last_place_keys = keys
            for e in out:
                self._last_recalled[_key_text(e.key)] = tick
        return out

    # --- 集計・シリアライズ ---
    def stats(self) -> dict:
        exp = sum(1 for e in self.log if isinstance(e, Experience))
        return {"log": len(self.log), "log_experiences": exp, "long": len(self.long),
                "organized": self.organized, "bytes_estimate": 80 * len(self.log) + 120 * len(self.long)}

    def to_state(self) -> dict:
        return {"seq": self.seq, "organized": self.organized, "last_organize_tick": self.last_organize_tick,
                "params": self.p.to_dict(),
                "log": [e.to_list() for e in self.log if isinstance(e, Experience)],     # MapUpdate は運ばない
                "long": [e.to_list() for e in self.long.values()],
                "last_recalled": sorted(self._last_recalled.items())}

    @classmethod
    def from_state(cls, d: dict) -> "Memory":
        m = cls(Params(**d.get("params", {})))
        m.seq, m.organized = d.get("seq", 0), d.get("organized", 0)
        m.last_organize_tick = d.get("last_organize_tick", -1)
        for row in d.get("long", []):
            e = LongEntry(*row)
            m.long[e.key] = e
        for row in d.get("log", []):
            row = list(row)
            row[9] = _state_from_json(row[9])
            e = Experience(*row)
            m.log.append(e)
            if RULES[(e.action, e.outcome)].cls != STEP:
                m._aggregate(e)
        m._last_recalled = {k: v for k, v in d.get("last_recalled", [])}
        m._rebuild_index()
        return m
