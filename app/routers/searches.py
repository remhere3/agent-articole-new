import logging
import re
import time
import unicodedata
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database import get_db
from app import models, schemas
from app.config import settings


def _normalize_title(title: str) -> str:
    """Normalizeaza titlul pentru deduplicare: lowercase, fara diacritice, fara punctuatie."""
    t = unicodedata.normalize("NFKD", title.lower())
    t = re.sub(r"[^\w\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()

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
        try:
            articles = await _dispatch_search(topic, telemetry)
        except Exception as primary_error:
            if topic.fallback_provider:
                logger.warning(
                    f"  Provider '{topic.provider}' a eșuat: {primary_error}. "
                    f"Încerc fallback: '{topic.fallback_provider}'"
                )
                fb = SimpleNamespace(
                    id=topic.id,
                    name=topic.name,
                    keywords=topic.keywords,
                    user_question=topic.user_question,
                    days_back=topic.days_back,
                    provider=topic.fallback_provider,
                    fallback_provider=None,
                    run_at_time=topic.run_at_time,
                    email_mode=topic.email_mode,
                    deduplicate=topic.deduplicate,
                    active=topic.active,
                    send_email=topic.send_email,
                    users=topic.users,
                    results=topic.results,
                    runs=topic.runs,
                    last_run_at=topic.last_run_at,
                )
                telemetry.clear()
                articles = await _dispatch_search(fb, telemetry)
            else:
                raise

        # Deduplicare dupa URL si titlu normalizat (acelasi articol poate aparea la URL-uri diferite)
        if topic.deduplicate:
            existing_urls = {
                r.url for r in db.query(models.SearchResult.url)
                .filter(models.SearchResult.topic_id == topic_id)
                .all()
            }
            existing_titles = {
                _normalize_title(r.title) for r in db.query(models.SearchResult.title)
                .filter(models.SearchResult.topic_id == topic_id)
                .all()
            }
        else:
            existing_urls: set = set()
            existing_titles: set = set()

        new_count = 0
        skipped_count = 0
        for a in articles:
            url = a.get("url", "")
            norm_title = _normalize_title(a.get("title", ""))
            if topic.deduplicate and (url in existing_urls or norm_title in existing_titles):
                skipped_count += 1
                logger.debug(f"  [dedup] skip: {url[:80]}")
                continue
            existing_urls.add(url)
            existing_titles.add(norm_title)
            new_count += 1
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
                relevance_score=a.get("relevance_score"),
            )
            db.add(result)

        if not topic.deduplicate:
            logger.info(f"  [dedup] dezactivat — toate cele {new_count} articole sunt salvate")
        elif skipped_count:
            logger.info(f"  [dedup] {new_count} noi, {skipped_count} duplicate sarite")

        run.status = "success"
        run.results_count = new_count  # doar articolele efectiv noi
        run.finished_at = datetime.utcnow()
        run.tokens_input  = telemetry.get("tokens_input")
        run.tokens_output = telemetry.get("tokens_output")
        run.api_calls     = telemetry.get("api_calls")
        topic.last_run_at = datetime.utcnow()
        db.commit()
        db.refresh(run)

        elapsed = time.perf_counter() - t0
        logger.info(
            f"╚═ Run #{run.id} SUCCESS | {new_count} noi"
            + (f" ({skipped_count} duplicate)" if skipped_count else "")
            + f" | {elapsed:.1f}s"
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
                results_count=new_count,
                elapsed_s=elapsed,
                subscribers=active_users,
                provider=topic.provider,
                tokens_input=run.tokens_input,
                tokens_output=run.tokens_output,
                api_calls=run.api_calls,
            )

        # Trimite email DOAR daca exista articole noi
        if topic.send_email and topic.users and new_count > 0 and topic.email_mode == "immediate":
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
                telemetry = _build_telemetry(topic, run.results, elapsed, run)
                logger.info(f"  → Trimit email catre: {active_emails} ({new_count} articole noi)")
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
        elif topic.send_email and new_count == 0:
            logger.info("  → Email sarit: 0 articole noi")

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


def _build_telemetry(
    topic: models.Topic,
    articles: list,
    elapsed_s: float,
    run: Optional[models.SearchRun] = None,
) -> dict:
    """Construieste dict cu telemetrie pentru raportul email."""
    if topic.provider == "anthropic":
        model = settings.anthropic_model
        web_search = f"web_search_20250305 (max 8 apeluri) · {model}"
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

    result: dict = {
        "provider":    topic.provider,
        "model":       model,
        "web_search":  web_search,
        "elapsed_s":   elapsed_s,
        "found_total": len(articles),
        "excluded":    0,
    }
    if run is not None:
        result["tokens_input"]  = run.tokens_input
        result["tokens_output"] = run.tokens_output
        result["cache_read"]    = getattr(run, "cache_read", None)
    return result


def _keywords_for_search(topic) -> str:
    """
    Returneaza termenii de cautare potriviti pentru query-urile din search strategies.
    Daca topic.keywords e gol, extrage primele 80 de caractere din user_question
    (trunchiat la limita de cuvant) pentru a evita query-uri prea lungi.
    """
    if topic.keywords:
        return topic.keywords
    q = (topic.user_question or "").strip()
    if len(q) <= 80:
        return q
    return q[:80].rsplit(" ", 1)[0]


async def _dispatch_search(topic: models.Topic, telemetry: dict) -> list:
    """Alege providerul corect si executa cautarea."""
    query = topic.user_question or topic.keywords

    if topic.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")
        from app.services.search_anthropic import search_articles
        return await search_articles(
            keywords=_keywords_for_search(topic),
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
            keywords=_keywords_for_search(topic),
            days_back=topic.days_back,
            api_key=settings.tavily_api_key,
            user_question=topic.user_question or None,
            telemetry=telemetry,
        )

    elif topic.provider == "ollama":
        if not settings.tavily_api_key:
            raise ValueError("TAVILY_API_KEY required for ollama provider")
        from app.services.search_ollama import search_articles
        return await search_articles(
            keywords=_keywords_for_search(topic),
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
    already_running = db.query(models.SearchRun).filter(
        models.SearchRun.topic_id == topic_id,
        models.SearchRun.status == "running",
    ).first()
    if already_running:
        raise HTTPException(status_code=409, detail=f"Run #{already_running.id} already in progress for this topic")
    run = await _run_search(topic_id, db)
    db.refresh(run)
    return run


@router.get("/runs", response_model=List[schemas.SearchRunOut])
def list_runs(
    topic_id: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    response: Response = None,
):
    q = db.query(models.SearchRun)
    if topic_id:
        q = q.filter(models.SearchRun.topic_id == topic_id)
    total = q.count()
    items = q.order_by(models.SearchRun.id.desc()).offset(offset).limit(limit).all()
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return items


@router.get("/runs/{run_id}", response_model=schemas.SearchRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.SearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


from fastapi.responses import StreamingResponse as _StreamingResponse
import csv
import io
import json as _json


@router.get("/results/export")
def export_results(
    topic_id: Optional[int] = None,
    format: str = "csv",
    db: Session = Depends(get_db),
):
    """Exportă rezultatele căutărilor în format CSV sau JSON."""
    q = db.query(models.SearchResult).order_by(models.SearchResult.id.desc())
    if topic_id:
        q = q.filter(models.SearchResult.topic_id == topic_id)
    results = q.all()

    if format == "json":
        data = [
            {
                "id": r.id,
                "topic_id": r.topic_id,
                "title": r.title,
                "url": r.url,
                "authors": r.authors,
                "source": r.source,
                "published_date": r.published_date,
                "summary": r.summary,
                "provider": r.provider,
                "relevance_score": r.relevance_score,
                "found_at": r.found_at.isoformat() if r.found_at else None,
            }
            for r in results
        ]
        return _StreamingResponse(
            iter([_json.dumps(data, ensure_ascii=False, indent=2)]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=articole.json"},
        )

    # CSV (default)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "topic_id", "title", "url", "authors", "source", "published_date", "summary", "provider", "relevance_score", "found_at"])
    for r in results:
        writer.writerow([
            r.id, r.topic_id, r.title, r.url, r.authors or "",
            r.source or "", r.published_date or "", r.summary or "",
            r.provider or "", r.relevance_score or "",
            r.found_at.isoformat() if r.found_at else "",
        ])
    output.seek(0)
    return _StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=articole.csv"},
    )


@router.get("/results", response_model=List[schemas.SearchResultOut])
def list_results(
    topic_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    response: Response = None,
):
    q = db.query(models.SearchResult)
    if topic_id:
        q = q.filter(models.SearchResult.topic_id == topic_id)
    total = q.count()
    items = q.order_by(models.SearchResult.id.desc()).offset(offset).limit(limit).all()
    if response is not None:
        response.headers["X-Total-Count"] = str(total)
    return items


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
