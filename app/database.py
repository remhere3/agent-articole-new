from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
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
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_add_columns()


def _migrate_add_columns():
    """Adauga coloane noi la tabelele existente (SQLite nu suporta ALTER TABLE automat)."""
    new_cols = [
        ("search_runs", "tokens_input",  "INTEGER"),
        ("search_runs", "tokens_output", "INTEGER"),
        ("search_runs", "api_calls",     "INTEGER"),
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
