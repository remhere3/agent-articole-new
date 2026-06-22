import logging
import time
from contextlib import asynccontextmanager
from datetime import timezone
from pathlib import Path

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session
from app.database import get_db
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.scheduler import start_scheduler, stop_scheduler, mark_interrupted_runs
from app.routers import users, topics, searches
from app.config import settings as app_settings
from app.log_stream import install_handler, log_event_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

install_handler()  # SSE log handler

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_settings.verify_secret_key()  # politica optionala (enforce_secret_key); off implicit
    init_db()
    logger.info("Database initialized")
    start_scheduler()
    yield
    stop_scheduler()
    mark_interrupted_runs()  # inchide rularile ramase 'running' la oprire


app = FastAPI(
    title="Agent Articole",
    description="Agent de cautare articole stiintifice (Anthropic, Tavily, SearXNG+Ollama, Author)",
    version=app_settings.version,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

app.include_router(users.router)
app.include_router(topics.router)
app.include_router(searches.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    skip = (request.url.path.startswith("/static")
            or request.url.path == "/api/logs/stream"
            or request.url.path == "/api/status")
    if skip:
        return await call_next(request)
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({ms:.0f}ms)")
    return response


@app.get("/api/logs/stream", include_in_schema=False)
async def stream_logs(request: Request):
    return StreamingResponse(
        log_event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"version": app_settings.version})


@app.get("/documentation", response_class=HTMLResponse, include_in_schema=False)
async def documentation(request: Request):
    return templates.TemplateResponse(request, "documentation.html", {"version": app_settings.version})


@app.get("/health")
async def health():
    return {"status": "ok", "version": app_settings.version}


@app.get("/api/status")
async def status(db: Session = Depends(get_db)):
    from app import models
    active_topics = db.query(models.Topic).filter(models.Topic.active == True).count()  # noqa: E712
    total_results = db.query(models.SearchResult).count()
    last_run = db.query(models.SearchRun).order_by(models.SearchRun.id.desc()).first()

    # started_at e stocat naiv UTC (server_default=func.now() -> CURRENT_TIMESTAMP).
    # Il marcam explicit ca UTC, altfel JS-ul din footer il interpreteaza ca ora
    # locala si afiseaza ora gresita pentru Europe/Bucharest.
    last_run_at = None
    if last_run and last_run.started_at:
        dt = last_run.started_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        last_run_at = dt.isoformat()

    return {
        "version": app_settings.version,
        "anthropic_model": app_settings.anthropic_model,
        "anthropic_configured": bool(app_settings.anthropic_api_key),
        "tavily_configured": bool(app_settings.tavily_api_key),
        "ollama_url": app_settings.ollama_base_url,
        "ollama_model": app_settings.ollama_model,
        "searxng_url": app_settings.searxng_base_url,
        "smtp_configured": bool(app_settings.smtp_user),
        "active_topics": active_topics,
        "total_results": total_results,
        "last_run_at": last_run_at,
        "last_run_status": last_run.status if last_run else None,
    }


@app.get("/api/metrics")
async def metrics(db: Session = Depends(get_db)):
    """Observabilitate: agregari per provider — rulari, succese/esecuri/intrerupte,
    durata medie, rezultate, tokeni si cost estimat. Plus un total general.

    Agregarea se face in Python (portabil intre baze de date); la scara acestei
    aplicatii numarul de rulari e mic, deci e acceptabil.
    """
    from collections import defaultdict
    from app import models

    rows = db.query(
        models.SearchRun.provider,
        models.SearchRun.status,
        models.SearchRun.started_at,
        models.SearchRun.finished_at,
        models.SearchRun.results_count,
        models.SearchRun.tokens_input,
        models.SearchRun.tokens_output,
        models.SearchRun.api_calls,
        models.SearchRun.estimated_cost_usd,
    ).all()

    def _blank():
        return {
            "runs": 0, "success": 0, "error": 0, "interrupted": 0, "running": 0,
            "total_results": 0, "tokens_input": 0, "tokens_output": 0,
            "api_calls": 0, "estimated_cost_usd": 0.0,
            "_dur_sum": 0.0, "_dur_n": 0,
        }

    agg = defaultdict(_blank)

    def _accumulate(b, r):
        b["runs"] += 1
        if r.status in ("success", "error", "interrupted", "running"):
            b[r.status] += 1
        b["total_results"] += r.results_count or 0
        b["tokens_input"] += r.tokens_input or 0
        b["tokens_output"] += r.tokens_output or 0
        b["api_calls"] += r.api_calls or 0
        b["estimated_cost_usd"] += r.estimated_cost_usd or 0.0
        if r.started_at and r.finished_at:
            b["_dur_sum"] += (r.finished_at - r.started_at).total_seconds()
            b["_dur_n"] += 1

    for r in rows:
        _accumulate(agg[r.provider or "necunoscut"], r)
        _accumulate(agg["_total"], r)

    def _finalize(b):
        runs = b["runs"]
        dur_n = b.pop("_dur_n")
        dur_sum = b.pop("_dur_sum")
        b["success_rate"] = round(b["success"] / runs, 3) if runs else None
        b["avg_duration_s"] = round(dur_sum / dur_n, 2) if dur_n else None
        b["estimated_cost_usd"] = round(b["estimated_cost_usd"], 6)
        return b

    totals = _finalize(agg.pop("_total", _blank()))  # _blank daca nu exista nicio rulare
    providers = {name: _finalize(b) for name, b in sorted(agg.items())}
    return {"providers": providers, "totals": totals}
