"""Teste pentru shutdown-ul gratios (app/scheduler.mark_interrupted_runs).

Verifica faptul ca rularile ramase 'running' la oprire sunt marcate
'interrupted' (cu finished_at + mesaj), iar celelalte statusuri nu sunt atinse.
SessionLocal e redirectat catre un SQLite in-memory propriu testului.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app import models
from app import scheduler


@pytest.fixture
def session_factory(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # mark_interrupted_runs face `from app.database import SessionLocal` la apel.
    monkeypatch.setattr("app.database.SessionLocal", Factory)
    yield Factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _topic(s):
    t = models.Topic(name="T", provider="anthropic")
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


def test_marcheaza_running_ca_interrupted(session_factory):
    s = session_factory()
    t = _topic(s)
    s.add(models.SearchRun(topic_id=t.id, provider="anthropic", status="running"))
    s.commit()
    s.close()

    n = scheduler.mark_interrupted_runs()
    assert n == 1

    s = session_factory()
    run = s.query(models.SearchRun).first()
    assert run.status == "interrupted"
    assert run.finished_at is not None
    assert "oprit" in run.error_message.lower()
    s.close()


def test_nu_atinge_rularile_finalizate(session_factory):
    s = session_factory()
    t = _topic(s)
    s.add_all([
        models.SearchRun(topic_id=t.id, provider="anthropic", status="success"),
        models.SearchRun(topic_id=t.id, provider="tavily", status="error"),
    ])
    s.commit()
    s.close()

    assert scheduler.mark_interrupted_runs() == 0

    s = session_factory()
    statuses = {r.status for r in s.query(models.SearchRun).all()}
    assert statuses == {"success", "error"}
    s.close()


def test_fara_rulari_running_intoarce_zero(session_factory):
    assert scheduler.mark_interrupted_runs() == 0
