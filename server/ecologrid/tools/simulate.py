"""ヘッドレスで生態系だけを回し、個体数の推移を表示する(バランス調整用)。

  python -m ecologrid.tools.simulate --ticks 300000 --seed 1 --every 30000

時間は tick 数で指定する(1 tick = ゲーム内の 1 秒。86,400 tick = 1 日)。
Zzz 列は、眠っている(睡眠・意識消失)個体の割合(%)。faint / rest は、ここまでの意識消失・休憩開始の累計。
"""
import argparse
from collections import Counter

from ecologrid.core.clock import format_duration
from ecologrid.core.config import WorldConfig
from ecologrid.core.world import World


def asleep_percent(w: World, species: str) -> str:
    ents = [e for r in w.regions.values() for e in r.entities_of(species)]
    if not ents:
        return "-"
    return f"{100 * sum(not e.state.get('awake') for e in ents) / len(ents):.0f}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ticks", type=int, default=300_000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--every", type=int, default=30_000)
    d = WorldConfig()
    p.add_argument("--regions-x", type=int, default=d.regions_x)
    p.add_argument("--regions-y", type=int, default=d.regions_y)
    p.add_argument("--region-size", type=int, default=d.region_size, help="1 Region の一辺(セル)")
    p.add_argument("--rabbits", type=int, default=None, help="初期の Rabbit(世界全体の頭数)")
    p.add_argument("--foxes", type=int, default=None, help="初期の Fox(世界全体の頭数)")
    p.add_argument("--no-recall", action="store_true", help="想起(目標指向)を使わない(記憶あり/なしの比較用)")
    p.add_argument("--memorise-all-steps", action="store_true", help="全ステップを経験ログに追加する(RL の学習用)")
    p.add_argument("--scale-animals", action="store_true",
                   help="初期の動物を、世界の面積に比例させる(既定の 4×4×32 を基準。--rabbits/--foxes があればそちらを優先)")
    a = p.parse_args()
    cfg = WorldConfig(seed=a.seed, regions_x=a.regions_x, regions_y=a.regions_y, region_size=a.region_size,
                      recall_enabled=not a.no_recall, memorise_all_steps=a.memorise_all_steps)
    if a.scale_animals:
        ratio = (cfg.width * cfg.height) / (d.width * d.height)
        cfg.initial_counts = {k: max(1, round(v * ratio)) for k, v in d.initial_counts.items()}
    if a.rabbits is not None:
        cfg.initial_counts["Rabbit"] = a.rabbits
    if a.foxes is not None:
        cfg.initial_counts["Fox"] = a.foxes
    w = World(cfg)
    for msg in w.config_warnings:
        print("warning:", msg)
    w.generate()
    land = sum(len(r.land_cells) for r in w.regions.values())
    print(f"world {cfg.width}x{cfg.height} cells = {cfg.regions_x}x{cfg.regions_y} Regions of {cfg.region_size}x{cfg.region_size}"
          f" (land {land}), initial {cfg.initial_counts}, seed {cfg.seed}")
    deaths = Counter()                      # 動物(Rabbit・Fox)の死因: thirst(脱水)/ hunger(飢餓)/ other
    from ecologrid.core.living import LivingBeing
    orig_death = LivingBeing.on_death

    def counting_death(self, region):
        if self.species in ("Rabbit", "Fox"):
            deaths["thirst" if self.hydration <= 0 else "hunger" if self.energy <= 0 else "other"] += 1
        orig_death(self, region)

    LivingBeing.on_death = counting_death
    events = Counter()
    collect = w._collect_events

    def counting() -> None:               # 意識消失・休憩開始などの累計(イベントのログは件数が限られるため)
        for r in w.regions.values():
            for ev in r.events:
                events[ev["type"]] += 1
        collect()

    w._collect_events = counting
    cols = ("Grass", "Wheat", "Seed", "Rabbit", "Fox")
    print(f"{'tick':>9} {'経過':>14}  " + "  ".join(f"{k:>6}" for k in cols)
          + f"  {'R Zzz%':>6} {'F Zzz%':>6} {'faint':>6} {'rest':>6}")
    for i in range(a.ticks + 1):
        if i % a.every == 0:
            c = w.census()
            print(f"{i:>9,} {format_duration(i):>14}  " + "  ".join(f"{c.get(k, 0):>6}" for k in cols)
                  + f"  {asleep_percent(w, 'Rabbit'):>6} {asleep_percent(w, 'Fox'):>6}"
                  + f" {events['fainted']:>6} {events['rest_started']:>6}", flush=True)
        w.tick()
    print("deaths (Rabbit/Fox):", dict(deaths), "| eaten events:", events.get("entity_removed", 0))
    print("memory:", w.memory_stats())


if __name__ == "__main__":
    main()
