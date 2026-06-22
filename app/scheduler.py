"""
Scheduler APScheduler pentru executia periodica a cautarilor.
"""
import asyncio
import fcntl
import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Bucharest")

# Handle-ul fisierului de lock. Il pastram deschis cat traieste procesul: flock
# se elibereaza automat de OS la inchiderea descriptorului / terminarea procesului.
_lock_file = None


def _acquire_singleton_lock() -> bool:
    """Incearca sa obtina un flock exclusiv, ne-blocant, pe fisierul de lock.

    Returneaza True daca acest proces a obtinut lock-ul (=> e singurul care
    trebuie sa porneasca scheduler-ul). False daca lock-ul e deja detinut de
    alt worker de pe acelasi host. Astfel, sub Gunicorn multi-worker, un singur
    proces ruleaza joburile periodice, nu N. Se potriveste cu SQLite (un singur
    host, fisier local). Eroarea de deschidere (cale/permisiuni gresite) se
    propaga intentionat: e o eroare de configurare ce trebuie vazuta la pornire.
    """
    global _lock_file
    f = open(settings.scheduler_lock_path, "w")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return False
    f.write(str(os.getpid()))
    f.flush()
    _lock_file = f
    return True


def start_scheduler():
    """Porneste scheduler-ul si adauga job-ul de orchestrare.

    Doar procesul care obtine lock-ul de singleton porneste efectiv scheduler-ul;
    ceilalti workers ies tacut, ca joburile sa nu ruleze de mai multe ori.
    """
    if not _acquire_singleton_lock():
        logger.info(
            "Scheduler skipped in this process — singleton lock held by another "
            "worker (%s)", settings.scheduler_lock_path
        )
        return
    scheduler.add_job(
        orchestrate_searches,
        trigger=IntervalTrigger(minutes=15),
        id="orchestrate_searches",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("Scheduler started — checking topics every 15 minutes (pid %s)", os.getpid())


def stop_scheduler():
    # Doar procesul care a pornit scheduler-ul (cel cu lock-ul) il opreste.
    if _lock_file is None:
        return
    if scheduler.running:
        scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


def mark_interrupted_runs() -> int:
    """La oprire, marcheaza rularile ramase 'running' ca 'interrupted'.

    Un proces oprit la mijlocul unei cautari (deploy, restart, SIGTERM) ar lasa
    altfel rularea blocata pe veci in status 'running' — ar parea activa la
    repornire. O inchidem explicit. Deployment-ul e single-process (SQLite +
    scheduler singleton), deci e sigur sa maturam toate rularile 'running'.
    Intoarce numarul de rulari marcate.
    """
    from app.database import SessionLocal
    from app import models

    db = SessionLocal()
    try:
        runs = (
            db.query(models.SearchRun)
            .filter(models.SearchRun.status == "running")
            .all()
        )
        for run in runs:
            run.status = "interrupted"
            run.error_message = "Proces oprit in timpul rularii"
            run.finished_at = datetime.now()
        if runs:
            db.commit()
            logger.info("Marcat %d rulari 'running' ca 'interrupted' la oprire", len(runs))
        return len(runs)
    except Exception as e:
        logger.error(f"Failed to mark interrupted runs: {e}")
        return 0
    finally:
        db.close()


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
