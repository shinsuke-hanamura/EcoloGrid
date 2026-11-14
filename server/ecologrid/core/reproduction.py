"""繁殖(胎生・卵生・分裂)。個体単位の定期アクション(entity モード)のハンドラとして動く。

- breed_check:    誕生 tick を基準に check_interval_ticks ごとに、その個体が子作り判定
                  (年齢・Energy・間隔・非妊娠が条件)。個体ごとに実行 tick がずれる。
- gestation_step: 妊娠成功の tick を基準に gestation_step_ticks ごとに Energy を消費して育てる。
                  妊娠成功から正確に N tick ごと。期間満了で出産(期間が step の倍数なら期間ちょうど)。
子の配置は母体と同じ Region 内に限る(Region 越境の出産は Phase 1 では扱わない)。
"""
from typing import List, Tuple

from .actions import ENTITY, GLOBAL_SCOPE, START_MANUAL, START_SPAWN, ScheduledAction, register_entity_handler
from .egg import HATCH, hatch_action_id
from .grid import DIRS, Pos, region_of
from .species import FISSION, OVIPAROUS, SPECIES

BREED_CHECK = "breed_check"
GESTATION_STEP = "gestation_step"


def gestation_action_id(species: str) -> str:
    return f"{GESTATION_STEP}:{species}"


def default_scheduled_actions() -> List[Tuple[str, ScheduledAction]]:
    """繁殖定義を持つ全種について、判定と妊娠進行のアクションを global スコープに登録する。"""
    out = []
    for key, sp in SPECIES.items():
        rd = sp.repro
        if rd is None:
            continue
        out.append((GLOBAL_SCOPE, ScheduledAction(
            f"{BREED_CHECK}:{key}", BREED_CHECK, rd.check_interval_ticks,
            mode=ENTITY, species=key, start=START_SPAWN)))
        if rd.type != FISSION:
            out.append((GLOBAL_SCOPE, ScheduledAction(
                gestation_action_id(key), GESTATION_STEP, rd.gestation_step_ticks,
                mode=ENTITY, species=key, start=START_MANUAL)))
        if rd.type == OVIPAROUS:    # 卵の孵化(産卵の tick を基準に incubation_ticks 後)
            out.append((GLOBAL_SCOPE, ScheduledAction(
                hatch_action_id(key), HATCH, rd.incubation_ticks,
                mode=ENTITY, species="Egg", start=START_MANUAL)))
    return out


def _eligible(a, rd, tick_no: int) -> bool:
    return (not a.dead
            and a.gestation_started_tick is None
            and tick_no - a.birth_tick >= rd.min_age_ticks
            and a.energy >= rd.min_energy
            and (a.last_birth_tick is None or tick_no - a.last_birth_tick >= rd.min_interval_ticks))


def _birth_cells(region, mother, n: int) -> List[Pos]:
    """子の置き場所: 母体の周囲の歩けるセルから(同じ Region 内)。無ければ母体のセル。"""
    p = mother.grid_position
    cands = [Pos(p.x + dx, p.y + dy) for dx, dy in DIRS.values()]
    cands = [c for c in cands if region.walkable(c) and region_of(c, region.size) == region.rid]
    return [region.rng.choice(cands) if cands else p for _ in range(n)]


@register_entity_handler(BREED_CHECK)
def breed_check(region, action: ScheduledAction, tick_no: int, a) -> None:
    rd = SPECIES[a.species].repro
    if not _eligible(a, rd, tick_no):
        return
    if region.rng.random() >= rd.success_prob:
        return
    if rd.type == FISSION:
        _fission(region, a, tick_no)
    else:
        a.gestation_started_tick = tick_no
        region.start_timer(a, gestation_action_id(a.species), tick_no)


def _fission(region, a, tick_no: int) -> None:
    a.energy /= 2
    a.last_birth_tick = tick_no
    (cell,) = _birth_cells(region, a, 1)
    child = region.spawn(a.species, cell, init={"energy": a.energy, "birth_tick": tick_no})
    region.emit("reproduced", parent_id=a.entity_id, kind=FISSION, children=[child.entity_id],
                pos=[a.grid_position.x, a.grid_position.y])


@register_entity_handler(GESTATION_STEP)
def gestation_step(region, action: ScheduledAction, tick_no: int, a) -> None:
    rd = SPECIES[a.species].repro
    if a.gestation_started_tick is None:           # 状態不整合の保険
        region.stop_timer(a, action.action_id)
        return
    if a.energy < rd.gestation_energy_cost:        # 育てきれず流産(卵なら失われる)
        a.gestation_started_tick = None
        region.stop_timer(a, action.action_id)
        region.emit("pregnancy_lost", parent_id=a.entity_id, pos=[a.grid_position.x, a.grid_position.y])
        return
    a.energy -= rd.gestation_energy_cost
    if tick_no - a.gestation_started_tick >= rd.gestation_period_ticks:
        region.stop_timer(a, action.action_id)
        _deliver(region, a, rd, tick_no)


def _deliver(region, a, rd, tick_no: int) -> None:
    a.gestation_started_tick = None
    a.last_birth_tick = tick_no
    a.remember(region, "none", "gave_birth")             # 出産した場所(巣)を記憶する
    children = []
    for cell in _birth_cells(region, a, rd.litter_size):
        if rd.type == OVIPAROUS:
            e = region.spawn("Egg", cell, init={"timers": {hatch_action_id(a.species): tick_no}},
                             parent_species=a.species, laid_tick=tick_no,
                             incubation_ticks=rd.incubation_ticks)
        else:
            e = region.spawn(a.species, cell, init={"energy": rd.offspring_energy, "birth_tick": tick_no})
        children.append(e.entity_id)
    region.emit("reproduced", parent_id=a.entity_id, kind=rd.type, children=children,
                pos=[a.grid_position.x, a.grid_position.y])
