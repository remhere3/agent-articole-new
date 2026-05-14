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


async def orchestrate_searches():
    """
    Verifica toate topicurile active si ruleaza cautarile care sunt scadente.
    """
    from app.database import SessionLocal
    from app.routers.searches import _run_search
    from app import models

    db = SessionLocal()
    try:
        topics = db.query(models.Topic).filter(models.Topic.active == True).all()  # noqa: E712
        now = datetime.now()

        for topic in topics:
            should_run = (
                topic.last_run_at is None or
                now >= topic.last_run_at + timedelta(hours=topic.periodicity_hours)
            )
            if not should_run:
                continue

            label = "First run" if topic.last_run_at is None else "Scheduled run"
            logger.info(f"{label} for topic #{topic.id} '{topic.name}'")
            timeout = getattr(topic, "timeout_seconds", 300) or 300
            try:
                await asyncio.wait_for(_run_search(topic.id, db), timeout=float(timeout))
            except asyncio.TimeoutError:
                logger.error(
                    f"Topic #{topic.id} '{topic.name}' timed out after {timeout}s"
                )
                # Marcam run-ul activ ca eroare de timeout
                from app import models as _models
                active_run = (
                    db.query(_models.SearchRun)
                    .filter(
                        _models.SearchRun.topic_id == topic.id,
                        _models.SearchRun.status == "running",
                    )
                    .order_by(_models.SearchRun.id.desc())
                    .first()
                )
                if active_run:
                    active_run.status = "error"
                    active_run.error_message = f"Timeout după {timeout}s"
                    active_run.finished_at = datetime.now()
                    db.commit()
    except Exception as e:
        logger.error(f"Orchestration error: {e}")
    finally:
        db.close()
