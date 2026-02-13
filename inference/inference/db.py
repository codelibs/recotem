"""SQLAlchemy database engine (read-only access to Django's PostgreSQL)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


def _normalize_url(url: str) -> str:
    """Ensure SQLAlchemy uses the psycopg (v3) driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


engine = create_engine(
    _normalize_url(settings.database_url),
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
