"""Fixturi partajate pentru testele de nivel 2 (endpoint-uri + provideri).

`client` porneste aplicatia FastAPI in memorie (TestClient) cu o baza SQLite
in-memory izolata per test, suprascriind dependenta `get_db`. Lifespan-ul NU e
declansat (TestClient e instantiat fara context manager), deci scheduler-ul real
si init_db pe DB-ul de productie nu pornesc in timpul testelor.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app import models  # noqa: F401 — inregistreaza tabelele pe Base.metadata
from app.main import app


@pytest.fixture
def db_engine():
    """Engine SQLite in-memory, partajat pe o singura conexiune (StaticPool).

    StaticPool + check_same_thread=False sunt necesare ca TestClient (alt thread)
    sa vada acelasi `:memory:` — altfel fiecare conexiune ar primi o baza goala.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def client(db_engine):
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # Cooldown-ul de trigger e stare globala in modul — il resetam intre teste.
    from app.routers import searches
    searches._topic_last_trigger.clear()

    yield TestClient(app)

    app.dependency_overrides.clear()
