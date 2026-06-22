from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    import logging
    from app import models  # noqa: F401
    logger = logging.getLogger(__name__)
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    logger.info("Database initialized")


def _ensure_columns():
    """Adauga coloanele noi daca nu exista (idempotent, fara Alembic).

    Decizie deliberata: NU folosim Alembic. Pe SQLite single-instance, unde
    evolutia schemei se reduce la adaugare de coloane, `create_all` +
    `ALTER TABLE ADD COLUMN` e suficient. Limitari acceptate constient: nu
    gestioneaza redenumiri/stergeri de coloane, schimbari de tip/constrangeri,
    backfill sau downgrade; o modificare a *definitiei* unei coloane existente
    nu e detectata aici. Daca migram la Postgres sau apar astfel de nevoi,
    reintroducem Alembic (e deja in dependente). Vezi README, sectiunea
    "Migrari de schema".
    """
    from sqlalchemy import text
    migrations = [
        ("topics",       "timeout_seconds",     "INTEGER DEFAULT 300"),
        ("topics",       "periodicity_hours",    "REAL DEFAULT 24.0"),
        ("topics",       "user_question",        "TEXT"),
        ("topics",       "last_triggered_at",    "DATETIME"),
        ("search_runs",  "tokens_input",         "INTEGER"),
        ("search_runs",  "tokens_output",        "INTEGER"),
        ("search_runs",  "api_calls",            "INTEGER"),
        ("search_runs",  "estimated_cost_usd",   "REAL"),
    ]
    with engine.connect() as conn:
        for table, col, col_def in migrations:
            rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            existing = {r[1] for r in rows}
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
        conn.commit()
