from ecologrid.core.terrain import LAND, ROCK, WATER, Terrain


def test_deterministic():
    a, b = Terrain(7, 64, 64), Terrain(7, 64, 64)
    assert [a.kind(x, y) for x in range(64) for y in range(64)] == \
           [b.kind(x, y) for x in range(64) for y in range(64)]


def test_different_seed_differs():
    a, b = Terrain(1, 64, 64), Terrain(2, 64, 64)
    assert any(a.kind(x, y) != b.kind(x, y) for x in range(64) for y in range(64))


def test_has_all_kinds_and_mostly_land():
    t = Terrain(1, 128, 128)
    kinds = [t.kind(x, y) for x in range(128) for y in range(128)]
    for k in (LAND, WATER, ROCK):
        assert kinds.count(k) > 0
    assert kinds.count(LAND) / len(kinds) > 0.4


def test_out_of_bounds():
    assert not Terrain(1, 8, 8).walkable(__import__("ecologrid.core.grid", fromlist=["Pos"]).Pos(-1, 0))
