import bcrypt
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FPOS_LICENSE_DATABASE_URL", "sqlite:///./test-license.db")
    monkeypatch.setenv(
        "FPOS_LICENSE_ADMIN_PASSWORD_HASH",
        bcrypt.hashpw(b"test-password", bcrypt.gensalt()).decode(),
    )
    monkeypatch.setenv("FPOS_LICENSE_SIGNING_KEY_SEED", "test-signing-seed-that-is-long-enough")

    from app.config import get_settings
    get_settings.cache_clear()
    from app.database import Base, engine
    from app.main import app

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)
