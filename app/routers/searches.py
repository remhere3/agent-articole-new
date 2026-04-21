import logging
import time

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app import models, schemas
from app.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/searches", tags=["searches"])


async def _run_search(topic_id: int, db: Session) -> models.SearchRun:
    """Executa cautarea pentru un topic si salveaza rezultatele."""
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise ValueError(f"Topic {topic_id} not found")

    run = models.SearchRun(
        topic_id=topic_id,
        provider=topic.provider,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    logger.info(
        f"╔═ Run #{run.id} START | topic='{topic.name}' | provider={topic.provider} "
        f"| keywords='{topic.keywords or '—'}' | days_back={topic.days_back}"
    )
    t0 = time.perf_counter()
    telemetry: dict = {}

    try:
        articles = await _dispatch_search(topic, telemetry)

        for a in articles:
            result = models.SearchResult(
                topic_id=topic_id,
                run_id=run.id,
                title=a.get("title", ""),
                url=a.get("url", ""),
                authors=a.get("authors"),
                source=a.get("source"),
                published_date=a.get("published_date"),
                summary=a.get("summary"),
                provider=topic.provider,
            )
            db.add(result)

        run.status = "success"
        run.results_count = len(articles)
        run.finished_at = datetime.utcnow()
        run.tokens_input  = telemetry.get("tokens_input")
        run.tokens_output = telemetry.get("tokens_output")
        run.api_calls     = telemetry.get("api_calls")
        topic.last_run_at = datetime.utcnow()
        db.commit()
        db.refresh(run)

        elapsed = time.perf_counter() - t0
        logger.info(
            f"╚═ Run #{run.id} SUCCESS | {len(articles)} articole | {elapsed:.1f}s"
        )

        # Notificare ntfy
        if settings.ntfy_url:
            from app.services.ntfy_service import send_run_notification
            active_users = [u.name for u in topic.users if u.active]
            await send_run_notification(
                ntfy_url=settings.ntfy_url,
                topic_name=topic.name,
                run_id=run.id,
                status="success",
                results_count=len(articles),
                elapsed_s=elapsed,
                subscribers=active_users,
                provider=topic.provider,
                tokens_input=run.tokens_input,
                tokens_output=run.tokens_output,
                api_calls=run.api_calls,
            )

        # Trimite email daca e configurat
        if topic.send_email and topic.users:
            from app.services.email_service import send_report
            active_emails = [u.email for u in topic.users if u.active]
            if active_emails:
                article_dicts = [
                    {
                        "title": r.title,
                        "url": r.url,
                        "authors": r.authors,
                        "source": r.source,
                        "published_date": r.published_date,
                        "summary": r.summary,
                    }
                    for r in run.results
                ]
                telemetry = _build_telemetry(topic, articles, elapsed)
                logger.info(f"  → Trimit email catre: {active_emails}")
                await send_report(
                    to_addresses=active_emails,
                    topic_name=topic.name,
                    keywords=topic.keywords,
                    days_back=topic.days_back,
                    articles=article_dicts,
                    run_id=run.id,
                    user_question=topic.user_question or None,
                    telemetry=telemetry,
                )
                logger.info("  → Email trimis cu succes")

    except Exception as e:
        run.status = "error"
        run.error_message = str(e)
        run.finished_at = datetime.utcnow()
        db.commit()
        elapsed = time.perf_counter() - t0
        logger.error(f"╚═ Run #{run.id} ERROR | {elapsed:.1f}s | {e}")
        if settings.ntfy_url:
            from app.services.ntfy_service import send_run_notification
            await send_run_notification(
                ntfy_url=settings.ntfy_url,
                topic_name=topic.name,
                run_id=run.id,
                status="error",
                results_count=0,
                elapsed_s=elapsed,
                subscribers=[u.name for u in topic.users if u.active],
                provider=topic.provider,
            )

    return run


def _build_telemetry(topic: models.Topic, articles: list, elapsed_s: float) -> dict:
    """Construieste dict cu telemetrie pentru raportul email."""
    if topic.provider == "anthropic":
        model = settings.anthropic_model
        web_search = f"web_search_20250305 (max 5 apeluri) · {model}"
    elif topic.provider == "tavily":
        model = "—"
        web_search = "Tavily API (academic + general, 2 treceri)"
    elif topic.provider == "ollama":
        is_cloud = settings.ollama_base_url.startswith("https://")
        model = settings.ollama_model
        mode = "Ollama Cloud" if is_cloud else "Ollama local"
        web_search = f"Tavily API → {mode} ({model}) rezuma"
    else:
        model = "—"
        web_search = "—"

    return {
        "provider":    topic.provider,
        "model":       model,
        "web_search":  web_search,
        "elapsed_s":   elapsed_s,
        "found_total": len(articles),
        "excluded":    0,  # logat in servicii, nu propagat inca
    }


async def _dispatch_search(topic: models.Topic, telemetry: dict) -> list:
    """Alege providerul corect si executa cautarea."""
    query = topic.user_question or topic.keywords

    if topic.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        from app.services.search_anthropic import search_articles
        return await search_articles(
            keywords=topic.keywords or topic.user_question,
            days_back=topic.days_back,
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            user_question=topic.user_question or None,
            telemetry=telemetry,
        )

    elif topic.provider == "tavily":
        if not settings.tavily_api_key:
            raise ValueError("TAVILY_API_KEY not configured")
        from app.services.search_tavily import search_articles
        return await search_articles(
            keywords=query,
            days_back=topic.days_back,
            api_key=settings.tavily_api_key,
            telemetry=telemetry,
        )

    elif topic.provider == "ollama":
        if not settings.tavily_api_key:
            raise ValueError("TAVILY_API_KEY required for ollama provider")
        from app.services.search_ollama import search_articles
        return await search_articles(
            keywords=topic.keywords or topic.user_question,
            days_back=topic.days_back,
            tavily_api_key=settings.tavily_api_key,
            ollama_base_url=settings.ollama_base_url,
            ollama_model=settings.ollama_model,
            ollama_api_key=settings.ollama_api_key or None,
            user_question=topic.user_question or None,
            telemetry=telemetry,
        )

    raise ValueError(f"Unknown provider: {topic.provider}")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/run/{topic_id}", response_model=schemas.SearchRunOut)
async def trigger_search(topic_id: int, db: Session = Depends(get_db)):
    """Declanseaza manual o cautare pentru un topic."""
    topic = db.get(models.Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    run = await _run_search(topic_id, db)
    db.refresh(run)
    return run


@router.get("/runs", response_model=List[schemas.SearchRunOut])
def list_runs(
    topic_id: Optional[int] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(models.SearchRun).order_by(models.SearchRun.id.desc())
    if topic_id:
        q = q.filter(models.SearchRun.topic_id == topic_id)
    return q.limit(limit).all()


@router.get("/runs/{run_id}", response_model=schemas.SearchRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.SearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/results", response_model=List[schemas.SearchResultOut])
def list_results(
    topic_id: Optional[int] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    q = db.query(models.SearchResult).order_by(models.SearchResult.id.desc())
    if topic_id:
        q = q.filter(models.SearchResult.topic_id == topic_id)
    return q.limit(limit).all()


@router.delete("/results/{result_id}", response_model=schemas.MessageResponse)
def delete_result(result_id: int, db: Session = Depends(get_db)):
    result = db.get(models.SearchResult, result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found")
    db.delete(result)
    db.commit()
    return {"message": f"Result {result_id} deleted"}


@router.delete("/runs/{run_id}", response_model=schemas.MessageResponse)
def delete_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.SearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    db.delete(run)
    db.commit()
    return {"message": f"Run {run_id} deleted"}


class BulkDeleteRequest(BaseModel):
    ids: List[int]


@router.delete("/runs", response_model=schemas.MessageResponse)
def delete_runs_bulk(body: BulkDeleteRequest, db: Session = Depends(get_db)):
    if not body.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    deleted = db.query(models.SearchRun).filter(models.SearchRun.id.in_(body.ids)).all()
    for run in deleted:
        db.delete(run)
    db.commit()
    return {"message": f"{len(deleted)} run(s) deleted"}
