"""
Scheduler APScheduler pentru executia periodica a cautarilor.
"""
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


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
        now = datetime.utcnow()

        for topic in topics:
            if topic.last_run_at is None:
                # Prima rulare — executa imediat
                logger.info(f"First run for topic #{topic.id} '{topic.name}'")
                await _run_search(topic.id, db)
            else:
                next_run = topic.last_run_at + timedelta(hours=topic.periodicity_hours)
                if now >= next_run:
                    logger.info(f"Scheduled run for topic #{topic.id} '{topic.name}'")
                    await _run_search(topic.id, db)
    except Exception as e:
        logger.error(f"Orchestration error: {e}")
    finally:
        db.close()
