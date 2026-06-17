from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator, field_serializer
from typing import Optional, List
from datetime import datetime
from datetime import timezone as _utc_tz


def _localize(dt: Optional[datetime]) -> Optional[datetime]:
    """Marcheaza datetime naiv ca UTC (valorile sunt stocate in UTC in DB)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_utc_tz.utc)
    return dt


# ── User ─────────────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    name: str
    email: EmailStr
    active: bool = True


class UserCreate(UserBase):
    pass


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    active: Optional[bool] = None


class UserOut(UserBase):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def serialize_created_at(self, v: datetime) -> Optional[str]:
        return _localize(v).isoformat() if v else None


# ── Topic ─────────────────────────────────────────────────────────────────────

class TopicBase(BaseModel):
    # Limite de lungime: 'keywords' si 'user_question' intra direct in prompt-ul
    # trimis catre LLM, deci le marginim ca sa nu poata umfla contextul / costul.
    name: str = Field(min_length=1, max_length=200)
    keywords: Optional[str] = Field(default=None, max_length=1000)
    user_question: Optional[str] = Field(default=None, max_length=2000)
    days_back: int = 7
    periodicity_hours: float = 24.0
    timeout_seconds: int = 300
    provider: str = "anthropic"
    active: bool = True
    send_email: bool = True

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"anthropic", "tavily", "searxng", "author"}
        if v not in allowed:
            raise ValueError(f"provider must be one of {allowed}")
        return v

    @model_validator(mode="after")
    def require_keywords_or_question(self) -> "TopicBase":
        if not (self.keywords or "").strip() and not (self.user_question or "").strip():
            raise ValueError("Completeaza cel putin 'keywords' sau 'user_question'")
        return self

    @field_validator("days_back")
    @classmethod
    def validate_days_back(cls, v: int) -> int:
        if v < 1 or v > 3650:
            raise ValueError("days_back must be between 1 and 3650")
        return v

    @field_validator("periodicity_hours")
    @classmethod
    def validate_periodicity(cls, v: float) -> float:
        if v < 0.5:
            raise ValueError("periodicity_hours must be at least 0.5")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        if v < 30 or v > 3600:
            raise ValueError("timeout_seconds must be between 30 and 3600")
        return v


class TopicCreate(TopicBase):
    user_ids: List[int] = []


class TopicUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    keywords: Optional[str] = Field(default=None, max_length=1000)
    user_question: Optional[str] = Field(default=None, max_length=2000)
    days_back: Optional[int] = None
    periodicity_hours: Optional[float] = None
    timeout_seconds: Optional[int] = None
    provider: Optional[str] = None
    active: Optional[bool] = None
    send_email: Optional[bool] = None
    user_ids: Optional[List[int]] = None


class TopicOut(TopicBase):
    id: int
    user_question: Optional[str] = None
    timeout_seconds: Optional[int] = 300
    created_at: datetime
    last_run_at: Optional[datetime] = None
    users: List[UserOut] = []
    model_config = {"from_attributes": True}

    @field_serializer("created_at", "last_run_at")
    def serialize_dt(self, v: Optional[datetime]) -> Optional[str]:
        return _localize(v).isoformat() if v else None


# ── Search Results ─────────────────────────────────────────────────────────────

class SearchResultOut(BaseModel):
    id: int
    topic_id: int
    run_id: Optional[int] = None
    title: str
    url: str
    authors: Optional[str] = None
    source: Optional[str] = None
    published_date: Optional[str] = None
    summary: Optional[str] = None
    provider: Optional[str] = None
    found_at: datetime
    model_config = {"from_attributes": True}

    @field_serializer("found_at")
    def serialize_found_at(self, v: datetime) -> Optional[str]:
        return _localize(v).isoformat() if v else None


# ── Search Run ─────────────────────────────────────────────────────────────────

class SearchRunOut(BaseModel):
    id: int
    topic_id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str
    results_count: int
    error_message: Optional[str] = None
    provider: Optional[str] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    api_calls: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    results: List[SearchResultOut] = []
    model_config = {"from_attributes": True}

    @field_serializer("started_at", "finished_at")
    def serialize_dt(self, v: Optional[datetime]) -> Optional[str]:
        return _localize(v).isoformat() if v else None


# ── Generic responses ─────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
    detail: Optional[str] = None
