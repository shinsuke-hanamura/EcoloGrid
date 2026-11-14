import pytest
from fastapi.testclient import TestClient

from ecologrid.api.main import create_app
from ecologrid.api.settings import Settings
from ecologrid_cli.api import ApiClient
from ecologrid_cli.session import Session


@pytest.fixture
def env():
    """手動で tick を進める小さなサーバ(2x2 Region)と、それに繋ぐ CLI Session。"""
    app = create_app(Settings(regions_x=2, regions_y=2, autotick=False))
    with TestClient(app) as c:
        game = app.state.game
        s = Session(ApiClient(http=c), "tester")
        s.start()
        yield s, game, c
