import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

logger = logging.getLogger(__name__)

# Valori considerate "neschimbate" pentru app_secret_key: default-ul din cod si
# placeholder-ul din .env.example. Daca enforce_secret_key e activat, oricare
# dintre acestea (sau gol) declanseaza politica la startup.
_WEAK_SECRET_KEYS = {"dev-secret-change-in-production", "change_this_secret_key", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    version: str = "1.2"

    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-opus-4-8"

    tavily_api_key: Optional[str] = None

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_api_key: Optional[str] = None  # pentru Ollama Cloud

    searxng_base_url: str = "http://localhost:8080"
    searxng_max_articles: int = 25  # cate articole trimite SearXNG la Ollama pentru rezumare

    # Provider author (OpenAlex + CrossRef)
    author_max_works: int = 200      # lucrari/profil: OpenAlex + CrossRef, ambele paginate cu cursor (poate depasi 200/1000)
    author_max_profiles: int = 3     # cate profiluri de autor potrivite se proceseaza (diacritice -> profiluri separate)

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    email_from: str = "Agent Articole <noreply@example.com>"
    # Timeout explicit (secunde) pe intreaga operatie SMTP. Fara el, un server
    # SMTP blocat ar tine jobul ostatic (default-ul aiosmtplib e 60s, lung).
    smtp_timeout: float = 30.0

    app_secret_key: str = "dev-secret-change-in-production"
    # Optional, NU obligatoriu. Cand e True, la startup se verifica ca
    # app_secret_key a fost schimbata fata de placeholder. Implicit False
    # (off) — cheia ramane pur configurabila, fara sa blocheze pornirea.
    enforce_secret_key: bool = False
    database_url: str = "sqlite:///./agent_articole.db"
    debug: bool = False
    app_port: int = 8002

    # Lock de proces unic pentru scheduler (vezi app/scheduler.py). Daca ruleaza
    # mai multi workers pe acelasi host, doar cel care obtine acest flock porneste
    # scheduler-ul, evitand joburi duplicate. Trebuie sa fie pe un FS local comun.
    scheduler_lock_path: str = "/tmp/agent_articole_scheduler.lock"

    ntfy_enabled: bool = False
    ntfy_base_url: str = "https://ntfy.sh"   # ntfy.sh sau http://localhost:8080 pentru local
    ntfy_topic: Optional[str] = None          # ex: agent-articole-xyz

    @property
    def ntfy_url(self) -> Optional[str]:
        if not self.ntfy_enabled or not self.ntfy_topic:
            return None
        return f"{self.ntfy_base_url.rstrip('/')}/{self.ntfy_topic}"

    @property
    def secret_key_is_weak(self) -> bool:
        """True daca app_secret_key e inca o valoare placeholder (neschimbata)."""
        return self.app_secret_key.strip() in _WEAK_SECRET_KEYS

    def verify_secret_key(self) -> None:
        """Aplica politica enforce_secret_key la startup (off implicit).

        Nu face nimic daca toggle-ul e oprit sau cheia a fost schimbata. Daca
        e pornit si cheia e inca cea default: in productie (debug=False) refuza
        pornirea (RuntimeError); in debug doar avertizeaza, ca sa nu incurce
        dezvoltarea locala.
        """
        if not self.enforce_secret_key or not self.secret_key_is_weak:
            return
        msg = (
            "APP_SECRET_KEY este inca valoarea default (placeholder). "
            "enforce_secret_key=True cere o cheie proprie — seteaza APP_SECRET_KEY "
            "in .env (sau dezactiveaza ENFORCE_SECRET_KEY)."
        )
        if self.debug:
            logger.warning("[config] %s (debug=True -> pornesc oricum)", msg)
        else:
            raise RuntimeError(msg)


settings = Settings()
