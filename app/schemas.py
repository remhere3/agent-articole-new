from pydantic import BaseModel, EmailStr, field_validator, model_validator
from typing import Optional, List
from datetime import datetime


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


# ── Topic ─────────────────────────────────────────────────────────────────────

class TopicBase(BaseModel):
    name: str
    keywords: Optional[str] = None
    user_question: Optional[str] = None
    days_back: int = 7
    periodicity_hours: float = 24.0
    provider: str = "anthropic"
    fallback_provider: Optional[str] = None
    run_at_time: Optional[str] = None   # "HH:MM" UTC
    email_mode: str = "immediate"
    deduplicate: bool = True
    active: bool = True
    send_email: bool = True

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"anthropic", "tavily", "ollama"}
        if v not in allowed:
            raise ValueError(f"provider must be one of {allowed}")
        return v

    @field_validator("fallback_provider")
    @classmethod
    def validate_fallback_provider(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            allowed = {"anthropic", "tavily", "ollama"}
            if v not in allowed:
                raise ValueError(f"fallback_provider must be one of {allowed}")
        return v

    @field_validator("email_mode")
    @classmethod
    def validate_email_mode(cls, v: str) -> str:
        allowed = {"immediate", "daily_digest"}
        if v not in allowed:
            raise ValueError(f"email_mode must be one of {allowed}")
        return v

    @field_validator("run_at_time")
    @classmethod
    def validate_run_at_time(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            import re
            if not re.match(r'^\d{2}:\d{2}$', v):
                raise ValueError("run_at_time must be in HH:MM format")
            h, m = int(v[:2]), int(v[3:])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("run_at_time has invalid hour/minute")
        return v

    @model_validator(mode="after")
    def require_keywords_or_question(self) -> "TopicBase":
        if not (self.keywords or "").strip() and not (self.user_question or "").strip():
            raise ValueError("Completeaza cel putin 'keywords' sau 'user_question'")
        return self

    @field_validator("days_back")
    @classmethod
    def validate_days_back(cls, v: int) -> int:
        if v < 1 or v > 365:
            raise ValueError("days_back must be between 1 and 365")
        return v

    @field_validator("periodicity_hours")
    @classmethod
    def validate_periodicity(cls, v: float) -> float:
        if v < 0.5:
            raise ValueError("periodicity_hours must be at least 0.5")
        return v


class TopicCreate(TopicBase):
    user_ids: List[int] = []


class TopicUpdate(BaseModel):
    name: Optional[str] = None
    keywords: Optional[str] = None
    user_question: Optional[str] = None
    days_back: Optional[int] = None
    periodicity_hours: Optional[float] = None
    provider: Optional[str] = None
    fallback_provider: Optional[str] = None
    run_at_time: Optional[str] = None
    email_mode: Optional[str] = None
    deduplicate: Optional[bool] = None
    active: Optional[bool] = None
    send_email: Optional[bool] = None
    user_ids: Optional[List[int]] = None


class TopicOut(TopicBase):
    id: int
    user_question: Optional[str] = None
    fallback_provider: Optional[str] = None
    run_at_time: Optional[str] = None
    email_mode: str = "immediate"
    created_at: datetime
    last_run_at: Optional[datetime] = None
    users: List[UserOut] = []
    model_config = {"from_attributes": True}


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
    relevance_score: Optional[float] = None
    found_at: datetime
    model_config = {"from_attributes": True}


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
    results: List[SearchResultOut] = []
    model_config = {"from_attributes": True}


# ── Generic responses ─────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    message: str
    detail: Optional[str] = None
