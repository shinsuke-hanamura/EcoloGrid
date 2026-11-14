"""自動プレイ(負荷試験・動作確認用)。水を飲み、獲物を食べ、あとは歩き回る。"""
import random
import time
from typing import Dict, Optional, Tuple

from .session import Session

DIRS = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}
PREY = ("Fox", "Rabbit", "Wheat")     # Player が食べられる種


class Bot:
    def __init__(self, session: Session, seed: Optional[int] = None):
        self.s = session
        self.rng = random.Random(seed)
        self.counts: Dict[str, int] = {"move": 0, "eat": 0, "drink": 0, "rest": 0, "respawn": 0, "wait": 0}

    def _step_toward(self, me: Tuple[int, int], target: Tuple[int, int]) -> Optional[Tuple[str, dict]]:
        v = self.s.view
        dx, dy = target[0] - me[0], target[1] - me[1]
        cands = []
        if dx:
            cands.append(("e" if dx > 0 else "w", abs(dx)))
        if dy:
            cands.append(("s" if dy > 0 else "n", abs(dy)))
        cands.sort(key=lambda c: -c[1])
        for d, _ in cands:
            ddx, ddy = DIRS[d]
            if v.kind(me[0] + ddx, me[1] + ddy) == ".":
                return ("move", {"dir": d})
        return self._random_move(me)

    def _random_move(self, me) -> Optional[Tuple[str, dict]]:
        v = self.s.view
        dirs = sorted(DIRS)
        self.rng.shuffle(dirs)
        for d in dirs:
            if v.kind(me[0] + DIRS[d][0], me[1] + DIRS[d][1]) == ".":
                return ("move", {"dir": d})
        return ("wait", {})

    def decide(self) -> Optional[Tuple[str, dict]]:
        v = self.s.view
        if not v.alive or v.me_pos is None:
            return ("respawn", {})
        me, st = v.me_pos, v.stats
        vt = v.vitals
        if vt["state"] != "awake":
            return None                       # 眠っている間はコマンドが拒否される
        if vt["resting"] and vt["stamina"] < 95 and st["hydration"] >= 50 and st["energy"] >= 60:
            return None                       # 休憩を続ける(移動などの別のコマンドで終える)
        if vt["stamina"] < 20 and st["hydration"] >= 50 and st["energy"] >= 60:
            return ("rest", {})
        if st["hydration"] < 50:
            if v.adjacent_water(me):
                return ("drink", {})
            w = v.nearest_cell(me, "~", 25)
            if w:
                return self._step_toward(me, w)
        if any(e["species"] in PREY for e in v.at(me)) and st["energy"] < 95:
            return ("eat", {})
        if st["energy"] < 60:
            prey = [e for e in v.of_species(PREY)
                    if abs(e["pos"][0] - me[0]) <= 12 and abs(e["pos"][1] - me[1]) <= 12]
            if prey:
                t = min(prey, key=lambda e: abs(e["pos"][0] - me[0]) + abs(e["pos"][1] - me[1]))
                return self._step_toward(me, t["pos"])
        return self._random_move(me)

    def run(self, seconds: float, move_interval: Optional[float] = None) -> Dict[str, int]:
        """seconds の間プレイ。コマンドは、1 歩にかかる時間(move_interval_ticks × 1 tick の長さ)ごとに 1 つだけ送る。"""
        interval = move_interval or self.s.step_seconds("Player")
        end = time.monotonic() + seconds
        next_send = time.monotonic()
        while time.monotonic() < end:
            self.s.poll(wait=0)
            now = time.monotonic()
            if now < next_send:
                time.sleep(min(0.02, next_send - now))
                continue
            next_send = now + interval
            act = self.decide()
            if act:
                r = self.s.send(act[0], **act[1])
                if r in ("queued", "duplicate"):
                    self.counts[act[0]] = self.counts.get(act[0], 0) + 1
        return self.counts
