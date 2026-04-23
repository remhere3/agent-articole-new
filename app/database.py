from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)

if "sqlite" in settings.database_url:
    @sa_event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")   # reads nu blocheaza writes
        cur.execute("PRAGMA synchronous=NORMAL") # mai rapid, sigur cu WAL
        cur.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_add_columns()
    _migrate_add_indexes()


def _migrate_add_indexes():
    """Creeaza indexurile lipsa pe tabelele existente (idempotent)."""
    indexes = [
        ("idx_search_results_topic_id", "search_results", "topic_id"),
        ("idx_search_results_found_at",  "search_results", "found_at"),
        ("idx_search_runs_topic_id",     "search_runs",    "topic_id"),
    ]
    import sqlalchemy
    with engine.connect() as conn:
        for idx_name, table, col in indexes:
            conn.execute(sqlalchemy.text(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({col})"
            ))
        conn.commit()


def _migrate_add_columns():
    """Adauga coloane noi la tabelele existente (SQLite nu suporta ALTER TABLE automat)."""
    new_cols = [
        ("search_runs",    "tokens_input",      "INTEGER"),
        ("search_runs",    "tokens_output",     "INTEGER"),
        ("search_runs",    "api_calls",         "INTEGER"),
        ("topics",         "fallback_provider", "VARCHAR(50)"),
        ("topics",         "run_at_time",       "VARCHAR(5)"),
        ("topics",         "email_mode",        "VARCHAR(20) DEFAULT 'immediate'"),
        ("topics",         "deduplicate",       "BOOLEAN DEFAULT 1"),
        ("search_results", "relevance_score",   "REAL"),
    ]
    with engine.connect() as conn:
        for table, col, col_type in new_cols:
            existing = [r[1] for r in conn.execute(
                __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
            )]
            if col not in existing:
                conn.execute(__import__("sqlalchemy").text(
                    f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                ))
        conn.commit()
