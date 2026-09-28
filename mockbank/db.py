"""SQLAlchemy engine and session factory."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from mockbank.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            future=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def new_session() -> Session:
    return session_factory()()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request, rolled back on error."""
    db = new_session()
    try:
        yield db
    finally:
        db.close()


def reset_engine() -> None:
    """Used by tests to point the app at a different database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
