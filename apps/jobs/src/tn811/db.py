"""
db.py — SQLAlchemy session management and database initialization.

Supports SQLite (default) and Postgres (swap the DSN in monitoring.yaml).
The engine is created once per process and shared via a module-level singleton.

Usage:
    from tn811.db import get_session, init_db

    init_db(config)
    with get_session() as session:
        session.add(...)
        session.commit()
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tn811.config import AppConfig
from tn811.models import Base

logger = logging.getLogger(__name__)

# Module-level engine + session factory — initialized once via init_db()
_engine: Engine | None = None
_SessionFactory: sessionmaker | None = None  # type: ignore[type-arg]


def _configure_sqlite(engine: Engine) -> None:
    """Apply SQLite-specific pragmas for performance and data integrity."""

    @event.listens_for(engine, "connect")
    def set_pragmas(dbapi_conn: object, _conn_record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA temp_store=MEMORY")
        cursor.close()


def init_db(config: AppConfig) -> Engine:
    """
    Initialize the database engine and create all tables.

    This must be called once at application startup before any other
    database operations. It is idempotent — calling it multiple times
    is safe (tables are created only if they don't exist).

    Args:
        config: Loaded AppConfig.

    Returns:
        The initialized SQLAlchemy Engine.
    """
    global _engine, _SessionFactory

    db_url = config.db.url
    logger.info("Initializing database", extra={"url": db_url})

    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}

    _engine = create_engine(
        db_url,
        connect_args=connect_args,
        pool_pre_ping=True,
        echo=False,
    )

    if db_url.startswith("sqlite"):
        _configure_sqlite(_engine)

    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False)

    logger.info("Database initialized")
    return _engine


def get_engine() -> Engine:
    """Return the initialized engine. Raises if init_db() not called."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db(config) first.")
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager that yields a SQLAlchemy Session.

    Commits on clean exit, rolls back on exception.

    Usage:
        with get_session() as session:
            session.add(obj)
            # session.commit() called automatically
    """
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_db(config) first.")

    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def health_check() -> bool:
    """Return True if the database is reachable."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health check failed", exc_info=exc)
        return False
