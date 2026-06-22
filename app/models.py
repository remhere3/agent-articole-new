from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text,
    ForeignKey, Table, Float
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


topic_user = Table(
    "topic_user",
    Base.metadata,
    Column("topic_id", Integer, ForeignKey("topics.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    topics = relationship("Topic", secondary=topic_user, back_populates="users")


class Topic(Base):
    __tablename__ = "topics"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    keywords = Column(Text, nullable=True)           # termeni de cautare (optional daca user_question e completat)
    user_question = Column(Text, nullable=True)      # intrebarea libera a utilizatorului
    days_back = Column(Integer, default=7)            # only articles from last N days
    periodicity_hours = Column(Float, default=24.0)  # run every N hours
    timeout_seconds = Column(Integer, default=300)    # max seconds per search run
    provider = Column(String(50), default="anthropic")  # anthropic | tavily | searxng | author
    active = Column(Boolean, default=True)
    send_email = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)  # ultimul trigger manual (cooldown persistent intre restarturi)

    users = relationship("User", secondary=topic_user, back_populates="topics")
    results = relationship("SearchResult", back_populates="topic", cascade="all, delete-orphan")
    runs = relationship("SearchRun", back_populates="topic", cascade="all, delete-orphan")


class SearchResult(Base):
    __tablename__ = "search_results"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=True)
    title = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    authors = Column(Text, nullable=True)
    source = Column(String(255), nullable=True)
    published_date = Column(String(50), nullable=True)
    summary = Column(Text, nullable=True)
    provider = Column(String(50), nullable=True)
    found_at = Column(DateTime(timezone=True), server_default=func.now())

    topic = relationship("Topic", back_populates="results")
    run = relationship("SearchRun", back_populates="results")


class SearchRun(Base):
    __tablename__ = "search_runs"

    id = Column(Integer, primary_key=True, index=True)
    topic_id = Column(Integer, ForeignKey("topics.id"), nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(50), default="running")  # running | success | error
    results_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    provider = Column(String(50), nullable=True)

    tokens_input  = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)
    api_calls     = Column(Integer, nullable=True)  # web_search apeluri (anthropic) / cereri Tavily
    estimated_cost_usd = Column(Float, nullable=True)

    topic = relationship("Topic", back_populates="runs")
    results = relationship("SearchResult", back_populates="run", cascade="all, delete-orphan")


