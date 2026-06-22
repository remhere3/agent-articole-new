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
from app.scheduler import start_scheduler, stop_scheduler
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
