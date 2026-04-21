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
    active: bool = True
    send_email: bool = True

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        allowed = {"anthropic", "tavily", "ollama"}
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
    active: Optional[bool] = None
    send_email: Optional[bool] = None
    user_ids: Optional[List[int]] = None


class TopicOut(TopicBase):
    id: int
    user_question: Optional[str] = None
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
