import pytest
from unittest.mock import patch


@pytest.fixture(scope="session")
def app():
    """Flask test app with heavy init patched out."""
    with patch("src.web.poi_engine.initialize"), \
         patch("src.web.examples.seed_cache"):
        from src.web.app import create_app
        _app = create_app()
        _app.testing = True
    return _app


@pytest.fixture
def client(app):
    return app.test_client()
