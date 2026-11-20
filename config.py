"""
Application configuration.

All secrets and environment-specific values are read from environment
variables (optionally loaded from a local `.env` file). Nothing sensitive
is stored in source code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=False)


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer.") from exc
    if value < minimum:
        raise ConfigError(f"Environment variable {name} must be >= {minimum}.")
    return value


def _build_database_url() -> str:
    """Prefer DATABASE_URL; otherwise assemble one from POSTGRES_* variables."""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            # SQLAlchemy 2.x requires the explicit dialect name.
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    host = os.getenv("POSTGRES_HOST", "").strip()
    db = os.getenv("POSTGRES_DB", "").strip()
    user = os.getenv("POSTGRES_USER", "").strip()
    password = os.getenv("POSTGRES_PASSWORD", "")
    port = os.getenv("POSTGRES_PORT", "5432").strip() or "5432"
    sslmode = os.getenv("POSTGRES_SSLMODE", "prefer").strip() or "prefer"

    missing = [n for n, v in {
        "POSTGRES_HOST": host, "POSTGRES_DB": db,
        "POSTGRES_USER": user, "POSTGRES_PASSWORD": password,
    }.items() if not v]
    if missing:
        raise ConfigError(
            "Database is not configured. Set DATABASE_URL or the variables: "
            + ", ".join(missing) + ". See .env.example."
        )
    return (
        f"postgresql+psycopg2://{quote_plus(user)}:{quote_plus(password)}"
        f"@{host}:{port}/{quote_plus(db)}?sslmode={quote_plus(sslmode)}"
    )


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_tagline: str
    organisation_name: str
    currency_symbol: str
    session_idle_timeout_minutes: int
    session_absolute_timeout_minutes: int
    max_failed_logins: int
    lockout_minutes: int
    max_upload_mb: int
    max_upload_rows: int
    bcrypt_rounds: int
    page_size_default: int

    @property
    def database_url(self) -> str:
        # Resolved lazily so the UI can show a friendly error if it is missing.
        return _build_database_url()


def load_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "Salary Intelligence"),
        app_tagline=os.getenv("APP_TAGLINE", "Department Wise Salary Distribution"),
        organisation_name=os.getenv("ORGANISATION_NAME", "Enterprise Banking Analytics"),
        currency_symbol=os.getenv("CURRENCY_SYMBOL", "₹"),
        session_idle_timeout_minutes=_int_env("SESSION_IDLE_TIMEOUT_MINUTES", 20),
        session_absolute_timeout_minutes=_int_env("SESSION_ABSOLUTE_TIMEOUT_MINUTES", 480),
        max_failed_logins=_int_env("MAX_FAILED_LOGINS", 5),
        lockout_minutes=_int_env("LOCKOUT_MINUTES", 15),
        max_upload_mb=_int_env("MAX_UPLOAD_MB", 10),
        max_upload_rows=_int_env("MAX_UPLOAD_ROWS", 100_000),
        bcrypt_rounds=_int_env("BCRYPT_ROUNDS", 12, minimum=10),
        page_size_default=_int_env("PAGE_SIZE_DEFAULT", 25),
    )


settings = load_settings()

EMPTY_STATE_MESSAGE = "No verified salary records available."
