"""
Scheduler APScheduler pentru executia periodica a cautarilor.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Bucharest")


def start_scheduler():
    """Porneste scheduler-ul si adauga job-ul de orchestrare."""
    scheduler.add_job(
        orchestrate_searches,
        trigger=IntervalTrigger(minutes=15),
        id="orchestrate_searches",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("Scheduler started — checking topics every 15 minutes")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


def _mark_timeout(topic_id: int, timeout: int):
    """Marcheaza run-ul 'running' al unui topic ca eroare de timeout.

    Foloseste o sesiune NOUA, nu cea pasata in _run_search: la timeout corutina
    e anulata la mijlocul unui await (posibil intr-o operatie DB), deci sesiunea
    ei ramane intr-o stare nedefinita si nu poate fi reutilizata in siguranta.
    """
    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        active_run = (
            db.query(models.SearchRun)
            .filter(
                models.SearchRun.topic_id == topic_id,
                models.SearchRun.status == "running",
            )
            .order_by(models.SearchRun.id.desc())
            .first()
        )
        if active_run:
            active_run.status = "error"
            active_run.error_message = f"Timeout după {timeout}s"
            active_run.finished_at = datetime.now()
            db.commit()
    except Exception as e:
        logger.error(f"Failed to mark timeout for topic #{topic_id}: {e}")
    finally:
        db.close()


async def orchestrate_searches():
    """
    Verifica toate topicurile active si ruleaza cautarile care sunt scadente.
    """
    from app.database import SessionLocal
    from app.routers.searches import _run_search
    from app import models

    # Sesiune scurta doar pentru a determina topicurile scadente. O inchidem
    # inainte de a porni rularile, ca sa nu tinem o conexiune deschisa minute
    # intregi. Materializam datele necesare cat timp sesiunea e deschisa.
    db = SessionLocal()
    try:
        topics = db.query(models.Topic).filter(models.Topic.active).all()
        now = datetime.now()
        due = [
            (t.id, t.name, t.last_run_at is None, getattr(t, "timeout_seconds", 300) or 300)
            for t in topics
            if t.last_run_at is None
            or now >= t.last_run_at + timedelta(hours=t.periodicity_hours)
        ]
    except Exception as e:
        logger.error(f"Orchestration error (selectare topicuri): {e}")
        return
    finally:
        db.close()

    for topic_id, name, first_run, timeout in due:
        label = "First run" if first_run else "Scheduled run"
        logger.info(f"{label} for topic #{topic_id} '{name}'")
        # Sesiune dedicata per topic: scurteaza durata si izoleaza esecurile
        # (o sesiune poluata de un topic nu mai afecteaza topicurile urmatoare).
        db = SessionLocal()
        try:
            await asyncio.wait_for(_run_search(topic_id, db), timeout=float(timeout))
        except asyncio.TimeoutError:
            logger.error(f"Topic #{topic_id} '{name}' timed out after {timeout}s")
            _mark_timeout(topic_id, timeout)
        except Exception as e:
            logger.error(f"Topic #{topic_id} '{name}' failed: {e}")
        finally:
            db.close()
