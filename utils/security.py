"""
Security primitives: password hashing, password policy, role-based access
control, audit logging, input sanitisation, PII masking and upload checks.
"""
from __future__ import annotations

import logging
import re
import secrets
from typing import Any, Iterable

import bcrypt

from config import settings
from models import ROLE_ADMIN, ROLE_ANALYST, ROLE_HR, AuditLog

log = logging.getLogger("salary_intelligence.security")

# --------------------------------------------------------------------------
# Permissions (RBAC)
# --------------------------------------------------------------------------
VIEW_DASHBOARD = "view_dashboard"
VIEW_DEPARTMENTS = "view_departments"
VIEW_EMPLOYEES = "view_employees"
VIEW_EMPLOYEE_PII = "view_employee_pii"        # real names, not masked
EXPORT_EMPLOYEES = "export_employees"          # employee-level raw export
VIEW_EDA = "view_eda"
VIEW_VISUALIZATIONS = "view_visualizations"
EXPORT_REPORTS = "export_reports"
EXPORT_EDA = "export_eda"
IMPORT_DATA = "import_data"
MANAGE_USERS = "manage_users"
VIEW_AUDIT = "view_audit"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_ADMIN: frozenset({
        VIEW_DASHBOARD, VIEW_DEPARTMENTS, VIEW_EMPLOYEES, VIEW_EMPLOYEE_PII, EXPORT_EMPLOYEES,
        VIEW_EDA, VIEW_VISUALIZATIONS, EXPORT_REPORTS, EXPORT_EDA, IMPORT_DATA, MANAGE_USERS, VIEW_AUDIT,
    }),
    ROLE_HR: frozenset({
        VIEW_DASHBOARD, VIEW_DEPARTMENTS, VIEW_EMPLOYEES, VIEW_EMPLOYEE_PII, EXPORT_EMPLOYEES,
        VIEW_VISUALIZATIONS, EXPORT_REPORTS,
    }),
    # Analysts work with salary figures but see pseudonymised identities.
    ROLE_ANALYST: frozenset({
        VIEW_DASHBOARD, VIEW_DEPARTMENTS, VIEW_EMPLOYEES, VIEW_EDA, VIEW_VISUALIZATIONS,
        EXPORT_REPORTS, EXPORT_EDA,
    }),
}


def has_permission(role: str | None, permission: str) -> bool:
    return bool(role) and permission in ROLE_PERMISSIONS.get(role, frozenset())


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
PASSWORD_MIN_LENGTH = 12
_BCRYPT_MAX_BYTES = 72


def validate_password_policy(password: str, email: str | None = None) -> list[str]:
    """Return a list of policy violations (empty list == compliant)."""
    problems: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        problems.append(f"At least {PASSWORD_MIN_LENGTH} characters.")
    if len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        problems.append(f"No more than {_BCRYPT_MAX_BYTES} bytes.")
    if not re.search(r"[a-z]", password):
        problems.append("At least one lowercase letter.")
    if not re.search(r"[A-Z]", password):
        problems.append("At least one uppercase letter.")
    if not re.search(r"\d", password):
        problems.append("At least one digit.")
    if not re.search(r"[^A-Za-z0-9]", password):
        problems.append("At least one symbol.")
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in password.lower():
            problems.append("Must not contain your email name.")
    return problems


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


# A random hash computed at start-up so that a login attempt for an unknown
# email costs the same time as one for a real account (no user enumeration
# through timing). It is not a credential and matches nothing.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt(rounds=settings.bcrypt_rounds)).decode("ascii")


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


# --------------------------------------------------------------------------
# Input validation & sanitisation
# --------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}$")
EMPLOYEE_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-/]{0,31}$")
PERSON_NAME_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ .'\-])*$", re.UNICODE)
FREE_TEXT_RE = re.compile(r"^[\w &()./,'+\-]+$", re.UNICODE)


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email or ""))


def clean_text(value: Any, max_len: int = 120) -> str:
    """Trim, collapse internal whitespace, strip control characters."""
    s = "" if value is None else str(value)
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def neutralise_formula(value: Any) -> Any:
    """Prevent CSV/Excel formula injection in exported text cells."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def escape_like(term: str) -> str:
    """Escape LIKE wildcards so user search text is matched literally."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def mask_name(name: str, employee_code: str) -> str:
    """Pseudonymise a person's name for roles without PII access."""
    if not isinstance(name, str) or not name:
        return "—"
    initials = "".join(part[0].upper() for part in name.split() if part)[:3]
    return f"{initials}•••• ({employee_code})"


# --------------------------------------------------------------------------
# Upload checks (byte level; content validation lives in data_cleaning)
# --------------------------------------------------------------------------
ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx"}
_XLSX_MAGIC = b"PK\x03\x04"


def validate_upload_bytes(filename: str, data: bytes) -> tuple[bool, str]:
    name = (filename or "").lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return False, "Only .csv and .xlsx files are accepted."
    if len(data) == 0:
        return False, "The file is empty."
    limit = settings.max_upload_mb * 1024 * 1024
    if len(data) > limit:
        return False, f"File exceeds the {settings.max_upload_mb} MB limit."
    if ext == ".xlsx":
        if not data.startswith(_XLSX_MAGIC):
            return False, "File content is not a valid .xlsx workbook."
        import zipfile, io
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if "[Content_Types].xml" not in names:
                    return False, "Workbook structure is invalid."
                if any(n.lower().endswith("vbaproject.bin") for n in names):
                    return False, "Macro-enabled workbooks are not accepted."
                uncompressed = sum(i.file_size for i in zf.infolist())
                if uncompressed > limit * 20:
                    return False, "Workbook expands to an unsafe size."
        except zipfile.BadZipFile:
            return False, "Workbook is corrupted."
    else:
        if b"\x00" in data[:8192]:
            return False, "CSV appears to be binary, not text."
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                data.decode("cp1252")
            except UnicodeDecodeError:
                return False, "CSV must be UTF-8 or Windows-1252 encoded text."
    return True, "ok"


# --------------------------------------------------------------------------
# Audit logging
# --------------------------------------------------------------------------
def _current_session_identity() -> tuple[int | None, str | None, str | None]:
    try:
        import streamlit as st
        if not st.runtime.exists():
            return None, None, None
        auth = st.session_state.get("auth")
        sid = st.session_state.get("_sid")
        if auth:
            return auth.get("user_id"), auth.get("email"), sid
        return None, None, sid
    except Exception:
        return None, None, None


def _client_ip() -> str | None:
    try:
        import streamlit as st
        if not st.runtime.exists():
            return None
        ip = getattr(st.context, "ip_address", None)
        return ip if isinstance(ip, str) else None
    except Exception:
        return None


def log_audit(
    action: str,
    status: str = "SUCCESS",
    details: dict[str, Any] | None = None,
    *,
    user_id: int | None = None,
    user_email: str | None = None,
    session=None,
) -> None:
    """Write an audit log row. Never raises into the UI: failures go to the server log.

    Pass ``session`` to write inside an existing transaction.
    """
    sid_user, sid_email, sid = _current_session_identity()
    details = dict(details or {})
    ip = _client_ip()
    if ip and "ip" not in details:
        details["ip"] = ip
    entry = AuditLog(
        user_id=user_id if user_id is not None else sid_user,
        user_email=user_email if user_email is not None else sid_email,
        action=action[:60],
        status=status,
        details=_json_safe(details),
        session_id=sid,
    )
    if session is not None:
        session.add(entry)
        return
    try:
        from database import get_session
        with get_session() as s:
            s.add(entry)
    except Exception:  # pragma: no cover
        log.exception("Failed to write audit log for action %s", action)


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def require_any(role: str | None, permissions: Iterable[str]) -> bool:
    return any(has_permission(role, p) for p in permissions)
