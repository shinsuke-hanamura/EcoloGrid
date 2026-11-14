"""食べ物の嗜好と吸収性(個体ごと・種ごと。docs/tasks/1_18)。"""
from dataclasses import replace

import pytest

from ecologrid.core import factory
from ecologrid.core.species import SPECIES
from helpers import empty_world, land_run, region_for


def _fox_and_rabbit():
    w = empty_world()
    pos = land_run(w, 6)
    r = region_for(w, pos)
    return w, r, r.spawn("Fox", pos), r.spawn("Rabbit", pos)


def test_species_standard_values_keep_current_absorption():
    S = SPECIES
    assert (S["Rabbit"].absorption, S["Fox"].absorption, S["Player"].absorption) == (0.5, 0.6, 0.6)
    assert all(S[k].preference == 0.0 for k in ("Rabbit", "Fox", "Player"))
    assert S["Fox"].absorption_of("Rabbit") == 0.6 and S["Fox"].preference_of("Grass") == 0.0


def test_individual_copies_species_standard_at_spawn():
    w, r, fox, rb = _fox_and_rabbit()
    assert fox.absorption == {"Rabbit": 0.6, "Wheat": 0.6, "Grass": 0.6}
    assert fox.preference == {"Rabbit": 0.0, "Wheat": 0.0, "Grass": 0.0}
    assert rb.absorption == {"Wheat": 0.5, "Grass": 0.5}


def test_energy_gain_uses_absorption_of_the_food():
    w, r, fox, rb = _fox_and_rabbit()
    fox.energy, rb.energy = 20, 50
    fox.absorption["Rabbit"] = 0.2                      # 吸収性 0.2 の食べ物
    assert fox.eat_here(r)
    assert fox.energy == pytest.approx(20 + 50 * 0.2)


def test_zero_absorption_gives_no_energy_but_prey_is_consumed():
    w, r, fox, rb = _fox_and_rabbit()
    fox.energy, rb.energy = 20, 50
    fox.absorption["Rabbit"] = 0.0
    assert fox.eat_here(r)
    assert fox.energy == 20 and rb.entity_id not in r.entities


def test_default_behaviour_unchanged_from_species_rate():
    w, r, fox, rb = _fox_and_rabbit()
    fox.energy, rb.energy = 20, 50
    fox.eat_here(r)
    assert fox.energy == pytest.approx(20 + 50 * SPECIES["Fox"].absorption)


def test_changing_species_standard_later_does_not_change_existing_individuals(monkeypatch):
    w, r, fox, rb = _fox_and_rabbit()
    monkeypatch.setitem(SPECIES, "Fox", replace(SPECIES["Fox"], absorption=0.9, preference=5.0))
    assert fox.absorption["Rabbit"] == 0.6 and fox.preference["Rabbit"] == 0.0       # 既存の個体は、コピーした値のまま
    pos = fox.grid_position
    fox2 = r.spawn("Fox", pos)                                                         # 新しい個体は、新しい標準値
    assert fox2.absorption["Rabbit"] == 0.9 and fox2.preference["Rabbit"] == 5.0


def test_diet_survives_handoff_but_is_not_public():
    w, r, fox, rb = _fox_and_rabbit()
    fox.absorption["Grass"], fox.preference["Grass"] = 0.1, -3.0
    state = fox.to_state()
    assert state["absorption"]["Grass"] == 0.1 and state["preference"]["Grass"] == -3.0
    b = factory.restore(state)
    assert b.absorption["Grass"] == 0.1 and b.preference["Grass"] == -3.0
    pub = fox.to_dict()
    assert "absorption" not in pub and "preference" not in pub                          # 公開用には含めない


def test_toxicity_placeholder_has_no_effect():
    w, r, fox, rb = _fox_and_rabbit()
    assert rb.toxicity == 0.0
    rb.toxicity = 99.0                                                                  # 将来の毒性。当面は効果なし
    fox.energy, rb.energy = 20, 50
    h = fox.health
    fox.eat_here(r)
    assert fox.health == h


def test_config_exposes_species_standards():
    from fastapi.testclient import TestClient
    from ecologrid.api.main import create_app
    from ecologrid.api.settings import Settings
    with TestClient(create_app(Settings(regions_x=1, regions_y=1, autotick=False))) as c:
        sp = c.get("/api/v1/config").json()["species"]
        assert (sp["Rabbit"]["absorption"], sp["Fox"]["absorption"], sp["Player"]["absorption"]) == (0.5, 0.6, 0.6)
        assert sp["Rabbit"]["preference"] == 0.0
