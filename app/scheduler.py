"""
Scheduler APScheduler pentru executia periodica a cautarilor.
"""
import asyncio
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


def start_scheduler():
    scheduler.add_job(
        orchestrate_searches,
        trigger=IntervalTrigger(minutes=15),
        id="orchestrate_searches",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        send_daily_digest,
        trigger=CronTrigger(hour=7, minute=0, timezone="Europe/Bucharest"),
        id="daily_digest",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("Scheduler started — orchestrate every 15min, daily digest at 07:00 Europe/Bucharest")


def stop_scheduler():
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


def _is_due_by_time(topic, now: datetime) -> bool:
    """
    Verifică dacă un topic cu run_at_time e scadent:
    ora curentă UTC trebuie să fie în fereastra [run_at_time, run_at_time + 15min]
    și să nu fi rulat deja astăzi după acel timp.
    """
    run_at = topic.run_at_time  # "HH:MM"
    h, m = int(run_at[:2]), int(run_at[3:])
    scheduled_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    window_end = scheduled_today + timedelta(minutes=15)

    # Suntem în fereastră?
    if not (scheduled_today <= now < window_end):
        return False

    # Nu a mai rulat după scheduled_today
    if topic.last_run_at is None:
        return True
    return topic.last_run_at < scheduled_today


def _is_due_by_interval(topic, now: datetime) -> bool:
    if topic.last_run_at is None:
        return True
    next_run = topic.last_run_at + timedelta(hours=topic.periodicity_hours)
    return now >= next_run


async def orchestrate_searches():
    from app.database import SessionLocal
    from app.routers.searches import _run_search
    from app import models

    # Sesiune scurta doar pentru a interoga topicurile due
    db = SessionLocal()
    try:
        topics = db.query(models.Topic).filter(models.Topic.active == True).all()  # noqa: E712
        now = datetime.utcnow()
        due_topics = []
        for topic in topics:
            if topic.run_at_time:
                due = _is_due_by_time(topic, now)
            else:
                due = _is_due_by_interval(topic, now)
            if due:
                due_topics.append((topic.id, topic.name, topic.run_at_time, topic.periodicity_hours))
    except Exception as e:
        logger.error(f"Orchestration query error: {e}")
        return
    finally:
        db.close()

    # Fiecare run cu sesiune proprie + delay intre run-uri consecutive
    for i, (topic_id, topic_name, run_at_time, periodicity_hours) in enumerate(due_topics):
        mode = f"run_at_time={run_at_time}" if run_at_time else f"interval={periodicity_hours}h"
        run_db = SessionLocal()
        try:
            from app import models as _models
            already = run_db.query(_models.SearchRun).filter(
                _models.SearchRun.topic_id == topic_id,
                _models.SearchRun.status == "running",
            ).first()
            if already:
                logger.warning(f"Topic #{topic_id} '{topic_name}' — run #{already.id} deja in progress, sar peste")
                continue
            logger.info(f"Scheduled run for topic #{topic_id} '{topic_name}' [{mode}]")
            await _run_search(topic_id, run_db)
        except Exception as e:
            logger.error(f"Run error for topic #{topic_id}: {e}")
        finally:
            run_db.close()
        if i < len(due_topics) - 1:
            await asyncio.sleep(3)


async def send_daily_digest():
    """
    Trimite un email digest zilnic cu articolele din ultimele 24h
    pentru topicurile cu email_mode='daily_digest'.
    """
    from app.database import SessionLocal
    from app import models
    from app.services.email_service import send_report

    # Faza 1: citeste toate datele din DB si inchide sesiunea
    digest_items = []
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        topics = db.query(models.Topic).filter(
            models.Topic.active == True,  # noqa: E712
            models.Topic.send_email == True,  # noqa: E712
            models.Topic.email_mode == "daily_digest",
        ).all()

        logger.info(f"[Digest] {len(topics)} topicuri cu daily_digest")

        for topic in topics:
            active_emails = [u.email for u in topic.users if u.active]
            if not active_emails:
                continue

            recent_results = (
                db.query(models.SearchResult)
                .filter(
                    models.SearchResult.topic_id == topic.id,
                    models.SearchResult.found_at >= cutoff,
                )
                .order_by(models.SearchResult.found_at.desc())
                .all()
            )

            if not recent_results:
                logger.info(f"[Digest] Topic '{topic.name}': 0 articole noi — skip")
                continue

            digest_items.append({
                "topic_name":    topic.name,
                "keywords":      topic.keywords,
                "days_back":     topic.days_back,
                "user_question": topic.user_question,
                "emails":        active_emails,
                "articles": [
                    {
                        "title":          r.title,
                        "url":            r.url,
                        "authors":        r.authors,
                        "source":         r.source,
                        "published_date": r.published_date,
                        "summary":        r.summary,
                    }
                    for r in recent_results
                ],
            })
    except Exception as e:
        logger.error(f"[Digest] Eroare la citirea DB: {e}")
        return
    finally:
        db.close()  # sesiunea inchisa INAINTE de orice await

    # Faza 2: trimite emailurile fara sesiune DB deschisa
    for item in digest_items:
        logger.info(f"[Digest] Topic '{item['topic_name']}': {len(item['articles'])} articole → {item['emails']}")
        try:
            await send_report(
                to_addresses=item["emails"],
                topic_name=item["topic_name"],
                keywords=item["keywords"],
                days_back=item["days_back"],
                articles=item["articles"],
                run_id=0,
                user_question=item["user_question"],
                telemetry={"provider": "digest", "model": "—", "web_search": "daily digest", "elapsed_s": 0},
            )
        except Exception as e:
            logger.error(f"[Digest] Eroare la trimitere email pentru '{item['topic_name']}': {e}")
