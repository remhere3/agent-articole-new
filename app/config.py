from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-6"

    tavily_api_key: Optional[str] = None

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2"
    ollama_api_key: Optional[str] = None  # pentru Ollama Cloud

    searxng_base_url: str = "http://localhost:8080"

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    email_from: str = "Agent Articole <noreply@example.com>"

    app_secret_key: str = "dev-secret-change-in-production"
    database_url: str = "sqlite:///./agent_articole.db"
    debug: bool = False
    app_port: int = 8007

    ntfy_enabled: bool = False
    ntfy_base_url: str = "https://ntfy.sh"   # ntfy.sh sau http://localhost:8080 pentru local
    ntfy_topic: Optional[str] = None          # ex: agent-articole-xyz

    @property
    def ntfy_url(self) -> Optional[str]:
        if not self.ntfy_enabled or not self.ntfy_topic:
            return None
        return f"{self.ntfy_base_url.rstrip('/')}/{self.ntfy_topic}"


settings = Settings()
