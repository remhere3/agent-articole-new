from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


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


settings = Settings()
