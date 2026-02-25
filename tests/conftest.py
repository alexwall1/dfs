import os

# Sätt SQLite innan appen importeras så att create_app() inte ansluter till PostgreSQL.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest

from app import create_app, db as _db, limiter as _limiter
from app.models import User


@pytest.fixture(scope="session")
def app():
    """Skapa en Flask-app konfigurerad för test (SQLite i minnet)."""
    # Stäng av rate limiting innan appen initialiseras så att limiter.enabled
    # sätts till False redan vid init_app()-anropet inuti create_app().
    _limiter.enabled = False
    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        SECRET_KEY="test-secret",
    )
    with app.app_context():
        _db.create_all()
    yield app


@pytest.fixture(autouse=True)
def db(app):
    """Ge varje test en ren databas genom att rensa tabellerna efteråt."""
    with app.app_context():
        yield _db
        _db.session.rollback()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture()
def client(app):
    """Flask-testklient."""
    return app.test_client()


def skapa_user(db, **kw):
    """Hjälpfunktion för att skapa en testanvändare."""
    password = kw.pop("password", "lösenord123")
    defaults = dict(
        username="testuser",
        full_name="Test Testsson",
        role="handlaggare",
    )
    defaults.update(kw)
    user = User(**defaults)
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    return user


def logga_in(client, username="testuser", password="lösenord123"):
    """Logga in via POST till /login och returnera svaret."""
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )
