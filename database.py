"""
Database engine, session management and safe query helpers.

Every query in the application goes through SQLAlchemy with bound
parameters (``text()`` + params or ORM expressions). No SQL is ever
built by concatenating user input.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator, Mapping

import pandas as pd
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import settings
from models import Base


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        future=True,
    )


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def get_session() -> Iterator[Session]:
    """Transactional session: commits on success, rolls back on any error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables and constraints if they do not exist (idempotent)."""
    Base.metadata.create_all(get_engine())


def tables_exist() -> bool:
    required = {"users", "departments", "employees", "salary_records", "audit_logs"}
    return required.issubset(set(inspect(get_engine()).get_table_names()))


def check_connection() -> tuple[bool, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "connected"
    except Exception as exc:  # pragma: no cover - surfaced in UI
        return False, type(exc).__name__


def read_df(sql: str, params: Mapping[str, Any] | None = None, bind_expanding: tuple[str, ...] = ()) -> pd.DataFrame:
    """Run a parameterised SELECT and return a DataFrame.

    ``bind_expanding`` names parameters that hold lists for ``IN :param`` clauses.
    """
    stmt = text(sql)
    if bind_expanding:
        from sqlalchemy import bindparam
        stmt = stmt.bindparams(*[bindparam(name, expanding=True) for name in bind_expanding])
    with get_engine().connect() as conn:
        return pd.read_sql(stmt, conn, params=dict(params or {}))


def scalar(sql: str, params: Mapping[str, Any] | None = None) -> Any:
    with get_engine().connect() as conn:
        return conn.execute(text(sql), dict(params or {})).scalar()
