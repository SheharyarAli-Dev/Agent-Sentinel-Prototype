"""
app/database.py
───────────────
SQLAlchemy engine and session factory.

The database file is created automatically on first run — no migration tool
required for this prototype.  All models call `Base.metadata.create_all()`
at startup via main.py.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# ── Engine ─────────────────────────────────────────────────────────────────────
# check_same_thread=False is required for SQLite when used with FastAPI's
# async request handling (multiple threads share the same connection pool).
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=settings.app_env == "development",
)

# ── Session factory ────────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Declarative base ───────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ─────────────────────────────────────────────────────────
def get_db():
    """
    Yield a SQLAlchemy session and ensure it is closed after the request,
    even if an exception is raised mid-request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
