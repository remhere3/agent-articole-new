import csv
import io
import logging
import time
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, schemas
from app.config import settings

# Rate limiting: timp (Unix) al ultimului trigger manual per topic_id
_topic_last_trigger: dict[int, float] = {}
TRIGGER_COOLDOWN = 60  # secunde

# Pret Anthropic claude-sonnet-4-6 (Mai 2025)
_PRICE_INPUT_PER_M = 3.0    # USD per 1M input tokens
_PRICE_OUTPUT_PER_M = 15.0  # USD per 1M output tokens

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
        run.finished_at = datetime.now()
        run.tokens_input  = telemetry.get("tokens_input")
        run.tokens_output = telemetry.get("tokens_output")
        run.api_calls     = telemetry.get("api_calls")
        # Estimare cost (doar pentru Anthropic care raporteaza tokeni)
        ti, to = run.tokens_input, run.tokens_output
        if ti and to:
            run.estimated_cost_usd = (
                ti / 1_000_000 * _PRICE_INPUT_PER_M +
                to / 1_000_000 * _PRICE_OUTPUT_PER_M
            )
        topic.last_run_at = datetime.now()
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
        if topic.send_email and topic.users and articles:
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
                telemetry["estimated_cost_usd"] = run.estimated_cost_usd
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
        run.finished_at = datetime.now()
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
    elif topic.provider == "searxng":
        model = settings.ollama_model
        web_search = f"SearXNG ({settings.searxng_base_url}) → Ollama local ({model}) rezuma"
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

    elif topic.provider == "searxng":
        from app.services.search_searxng import search_articles
        return await search_articles(
            keywords=topic.keywords or topic.user_question,
            days_back=topic.days_back,
            searxng_base_url=settings.searxng_base_url,
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

    now = time.time()
    last = _topic_last_trigger.get(topic_id, 0)
    if now - last < TRIGGER_COOLDOWN:
        remaining = int(TRIGGER_COOLDOWN - (now - last))
        raise HTTPException(
            status_code=429,
            detail=f"Cooldown activ. Mai așteaptă {remaining}s înainte de a relansa căutarea."
        )
    _topic_last_trigger[topic_id] = now

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


@router.get("/validate-provider/{provider}")
async def validate_provider(provider: str):
    """Verifica daca cheia API pentru providerul specificat este configurata si functionala."""
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            return {"ok": False, "message": "ANTHROPIC_API_KEY nu este configurata în .env"}
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=settings.anthropic_api_key)
            import asyncio
            models_list = await asyncio.to_thread(client.models.list)
            return {"ok": True, "message": f"Anthropic OK — {len(list(models_list.data))} modele disponibile"}
        except Exception as e:
            return {"ok": False, "message": f"Anthropic error: {str(e)[:200]}"}

    elif provider == "tavily":
        if not settings.tavily_api_key:
            return {"ok": False, "message": "TAVILY_API_KEY nu este configurata în .env"}
        try:
            from tavily import TavilyClient
            import asyncio
            client = TavilyClient(api_key=settings.tavily_api_key)
            result = await asyncio.to_thread(client.search, "test", max_results=1)
            return {"ok": True, "message": f"Tavily OK — {len(result.get('results', []))} rezultate test"}
        except Exception as e:
            return {"ok": False, "message": f"Tavily error: {str(e)[:200]}"}

    elif provider == "ollama":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{settings.ollama_base_url}/api/tags")
                if r.status_code == 200:
                    models_list = r.json().get("models", [])
                    return {"ok": True, "message": f"Ollama OK — {len(models_list)} modele disponibile la {settings.ollama_base_url}"}
                return {"ok": False, "message": f"Ollama răspuns neașteptat: HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "message": f"Ollama nu răspunde la {settings.ollama_base_url}: {str(e)[:200]}"}

    elif provider == "searxng":
        try:
            from app.services.search_searxng import check_searxng_available
            ok = await check_searxng_available(settings.searxng_base_url)
            if ok:
                return {"ok": True, "message": f"SearXNG OK la {settings.searxng_base_url}"}
            return {"ok": False, "message": f"SearXNG nu răspunde la {settings.searxng_base_url}"}
        except Exception as e:
            return {"ok": False, "message": f"SearXNG error: {str(e)[:200]}"}

    return {"ok": False, "message": f"Provider necunoscut: {provider}"}


@router.get("/runs/{run_id}/preview-email", response_class=HTMLResponse)
def preview_email(run_id: int, db: Session = Depends(get_db)):
    """Returneaza previzualizarea HTML a email-ului pentru un run."""
    run = db.get(models.SearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    from app.services.email_service import _build_html_report
    elapsed = None
    if run.finished_at and run.started_at:
        elapsed = (run.finished_at - run.started_at).total_seconds()

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
    telemetry = {
        "provider":    run.provider,
        "model":       run.topic.name if run.topic else "—",
        "web_search":  "—",
        "elapsed_s":   elapsed,
        "found_total": run.results_count,
        "excluded":    0,
        "tokens_input":  run.tokens_input,
        "tokens_output": run.tokens_output,
        "api_calls":     run.api_calls,
        "estimated_cost_usd": run.estimated_cost_usd,
    }
    html = _build_html_report(
        topic_name=run.topic.name if run.topic else f"Run #{run_id}",
        keywords=run.topic.keywords if run.topic else None,
        days_back=run.topic.days_back if run.topic else 7,
        articles=article_dicts,
        run_id=run_id,
        user_question=run.topic.user_question if run.topic else None,
        telemetry=telemetry,
    )
    return HTMLResponse(content=html)


@router.get("/results/export")
def export_results(
    topic_id: Optional[int] = None,
    format: str = "json",
    db: Session = Depends(get_db),
):
    """Exporta rezultatele ca CSV sau JSON (fara limita artificiala)."""
    q = db.query(models.SearchResult).order_by(models.SearchResult.id.desc())
    if topic_id:
        q = q.filter(models.SearchResult.topic_id == topic_id)
    results = q.all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "topic_id", "run_id", "title", "url", "authors",
                         "source", "published_date", "summary", "provider", "found_at"])
        for r in results:
            writer.writerow([
                r.id, r.topic_id, r.run_id, r.title, r.url, r.authors,
                r.source, r.published_date, r.summary, r.provider,
                r.found_at.isoformat() if r.found_at else "",
            ])
        filename = f"articole_topic{topic_id}.csv" if topic_id else "articole_toate.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        data = [
            {
                "id": r.id, "topic_id": r.topic_id, "run_id": r.run_id,
                "title": r.title, "url": r.url, "authors": r.authors,
                "source": r.source, "published_date": r.published_date,
                "summary": r.summary, "provider": r.provider,
                "found_at": r.found_at.isoformat() if r.found_at else None,
            }
            for r in results
        ]
        return JSONResponse(content=data)
