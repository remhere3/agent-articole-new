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
    # Creeaza tabelele noi (idempotent — nu modifica tabelele existente)
    Base.metadata.create_all(bind=engine)
    # Aplica migrarile Alembic (adauga coloane noi, constrangeri etc.)
    _run_alembic_upgrade()


def _run_alembic_upgrade():
    """Ruleaza 'alembic upgrade head' programatic pentru a aplica migrarile."""
    import logging
    from pathlib import Path
    from alembic.config import Config
    from alembic import command
    from sqlalchemy import inspect, text

    logger = logging.getLogger(__name__)

    alembic_cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))

    with engine.connect() as conn:
        inspector = inspect(conn)
        has_alembic_version = "alembic_version" in inspector.get_table_names()
        has_topics = "topics" in inspector.get_table_names()

        if not has_alembic_version and has_topics:
            # DB existent fara Alembic — stamp la baseline
            conn.commit()
            command.stamp(alembic_cfg, "baseline")
            logger.info("DB existent detectat — stamped la baseline Alembic")

    try:
        command.upgrade(alembic_cfg, "head")
        # SQLite non-transactional DDL poate lasa version la revision anterioara;
        # stampam explicit la head dupa un upgrade reusit.
        command.stamp(alembic_cfg, "head")
        logger.info("Alembic upgrade head — OK")
    except Exception as e:
        logger.warning(f"Alembic upgrade warning: {e}")
