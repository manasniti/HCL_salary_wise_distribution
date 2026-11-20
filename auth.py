"""
Authentication, session lifecycle and user management.

* Credentials are verified against bcrypt hashes stored in PostgreSQL.
* Failed attempts are counted per account; accounts lock temporarily.
* Sessions expire after inactivity and after an absolute lifetime.
* Every security-relevant event is written to ``audit_logs``.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

import streamlit as st
from sqlalchemy import func, select

from config import settings
from database import get_session
from models import ROLE_ADMIN, ROLES, User, utcnow
from utils import security as sec


@dataclass
class AuthResult:
    ok: bool
    message: str
    user: dict | None = None


GENERIC_LOGIN_ERROR = "Invalid email or password, or the account is temporarily locked."


# --------------------------------------------------------------------------
# Login / logout
# --------------------------------------------------------------------------
def authenticate(email: str, password: str) -> AuthResult:
    email = sec.normalise_email(email)
    if not email or not password or not sec.is_valid_email(email) or len(password) > 256:
        sec.burn_password_check(password or "x")
        sec.log_audit("LOGIN_FAILED", "FAILURE", {"reason": "malformed_input"}, user_email=email[:254] or None)
        return AuthResult(False, GENERIC_LOGIN_ERROR)

    now = utcnow()
    with get_session() as s:
        user = s.execute(select(User).where(User.email == email).with_for_update()).scalar_one_or_none()

        if user is None:
            sec.burn_password_check(password)
            sec.log_audit("LOGIN_FAILED", "FAILURE", {"reason": "unknown_account"}, user_email=email, session=s)
            return AuthResult(False, GENERIC_LOGIN_ERROR)

        if user.locked_until and user.locked_until > now:
            sec.burn_password_check(password)
            sec.log_audit("LOGIN_BLOCKED", "DENIED", {"reason": "account_locked"},
                          user_id=user.id, user_email=email, session=s)
            return AuthResult(False, GENERIC_LOGIN_ERROR)

        if not sec.verify_password(password, user.password_hash):
            user.failed_login_attempts += 1
            details = {"reason": "bad_password", "attempt": user.failed_login_attempts}
            if user.failed_login_attempts >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                user.failed_login_attempts = 0
                details["locked_for_minutes"] = settings.lockout_minutes
                sec.log_audit("ACCOUNT_LOCKED", "DENIED", details, user_id=user.id, user_email=email, session=s)
            else:
                sec.log_audit("LOGIN_FAILED", "FAILURE", details, user_id=user.id, user_email=email, session=s)
            return AuthResult(False, GENERIC_LOGIN_ERROR)

        if not user.is_active:
            sec.log_audit("LOGIN_BLOCKED", "DENIED", {"reason": "inactive"},
                          user_id=user.id, user_email=email, session=s)
            return AuthResult(False, GENERIC_LOGIN_ERROR)

        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = now
        sec.log_audit("LOGIN_SUCCESS", "SUCCESS", {}, user_id=user.id, user_email=email, session=s)
        return AuthResult(True, "ok", {
            "user_id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role,
        })


def start_session(user: dict) -> None:
    # Fresh session id on every login (prevents re-use of a pre-login identifier).
    st.session_state.clear()
    now = utcnow()
    st.session_state["_sid"] = secrets.token_hex(16)
    st.session_state["auth"] = {**user, "login_at": now, "last_activity": now}


def logout(reason: str = "user") -> None:
    auth = st.session_state.get("auth")
    if auth:
        action = "LOGOUT" if reason == "user" else "SESSION_EXPIRED"
        sec.log_audit(action, "INFO" if reason != "user" else "SUCCESS", {"reason": reason},
                      user_id=auth["user_id"], user_email=auth["email"])
    st.session_state.clear()
    if reason != "user":
        st.session_state["_flash"] = "Your session expired. Please sign in again."


def current_user() -> dict | None:
    return st.session_state.get("auth")


def enforce_session() -> dict | None:
    """Validate the current session. Returns the user dict or None (and logs out)."""
    auth = st.session_state.get("auth")
    if not auth:
        return None
    now = utcnow()
    idle = now - auth["last_activity"]
    lifetime = now - auth["login_at"]
    if idle > timedelta(minutes=settings.session_idle_timeout_minutes):
        logout("idle_timeout")
        return None
    if lifetime > timedelta(minutes=settings.session_absolute_timeout_minutes):
        logout("absolute_timeout")
        return None

    # Re-check the account on every interaction so that deactivation or role
    # changes take effect immediately.
    with get_session() as s:
        row = s.execute(
            select(User.is_active, User.role, User.full_name, User.locked_until).where(User.id == auth["user_id"])
        ).one_or_none()
    if row is None or not row.is_active:
        logout("account_disabled")
        return None
    auth["role"] = row.role
    auth["full_name"] = row.full_name
    auth["last_activity"] = now
    return auth


def seconds_until_idle_timeout() -> int:
    auth = st.session_state.get("auth")
    if not auth:
        return 0
    remaining = timedelta(minutes=settings.session_idle_timeout_minutes) - (utcnow() - auth["last_activity"])
    return max(0, int(remaining.total_seconds()))


def require_permission(permission: str) -> dict:
    """Page guard. Stops rendering if the user lacks the permission."""
    user = current_user()
    if not user or not sec.has_permission(user["role"], permission):
        sec.log_audit("ACCESS_DENIED", "DENIED", {"permission": permission})
        st.error("You do not have permission to view this section.")
        st.stop()
    return user


# --------------------------------------------------------------------------
# User management (used by the CLI and the Admin UI)
# --------------------------------------------------------------------------
class UserError(ValueError):
    pass


def create_user(email: str, full_name: str, role: str, password: str, *, actor: dict | None = None) -> int:
    email = sec.normalise_email(email)
    full_name = sec.clean_text(full_name, 120)
    if not sec.is_valid_email(email):
        raise UserError("Enter a valid email address.")
    if not full_name or not sec.PERSON_NAME_RE.match(full_name):
        raise UserError("Enter a valid full name (letters, spaces, . ' - only).")
    if role not in ROLES:
        raise UserError("Unknown role.")
    problems = sec.validate_password_policy(password, email)
    if problems:
        raise UserError("Password policy: " + " ".join(problems))
    with get_session() as s:
        if s.execute(select(User.id).where(User.email == email)).first():
            raise UserError("A user with this email already exists.")
        user = User(email=email, full_name=full_name, role=role, password_hash=sec.hash_password(password))
        s.add(user)
        s.flush()
        sec.log_audit("USER_CREATED", "SUCCESS", {"target": email, "role": role},
                      user_id=actor["user_id"] if actor else None,
                      user_email=actor["email"] if actor else "cli", session=s)
        return user.id


def count_active_admins(s) -> int:
    return s.execute(
        select(func.count()).select_from(User).where(User.role == ROLE_ADMIN, User.is_active.is_(True))
    ).scalar_one()


def update_user(target_id: int, *, actor: dict, role: str | None = None, is_active: bool | None = None,
                unlock: bool = False) -> None:
    with get_session() as s:
        user = s.get(User, target_id, with_for_update=True)
        if user is None:
            raise UserError("User not found.")
        changes: dict = {}
        demoting_admin = (
            user.role == ROLE_ADMIN and user.is_active
            and ((role is not None and role != ROLE_ADMIN) or is_active is False)
        )
        if demoting_admin and count_active_admins(s) <= 1:
            raise UserError("At least one active Admin must remain.")
        if target_id == actor["user_id"] and (is_active is False or (role and role != user.role)):
            raise UserError("You cannot deactivate or change the role of your own account.")
        if role is not None and role != user.role:
            if role not in ROLES:
                raise UserError("Unknown role.")
            changes["role"] = [user.role, role]
            user.role = role
        if is_active is not None and is_active != user.is_active:
            changes["is_active"] = [user.is_active, is_active]
            user.is_active = is_active
        if unlock:
            user.locked_until = None
            user.failed_login_attempts = 0
            changes["unlocked"] = True
        if changes:
            sec.log_audit("USER_UPDATED", "SUCCESS", {"target": user.email, **changes},
                          user_id=actor["user_id"], user_email=actor["email"], session=s)


def set_password(target_id: int, new_password: str, *, actor: dict | None, current_password: str | None = None) -> None:
    with get_session() as s:
        user = s.get(User, target_id, with_for_update=True)
        if user is None:
            raise UserError("User not found.")
        if current_password is not None and not sec.verify_password(current_password, user.password_hash):
            sec.log_audit("PASSWORD_CHANGE_FAILED", "FAILURE", {"reason": "bad_current_password"},
                          user_id=user.id, user_email=user.email, session=s)
            raise UserError("Current password is incorrect.")
        problems = sec.validate_password_policy(new_password, user.email)
        if problems:
            raise UserError("Password policy: " + " ".join(problems))
        if sec.verify_password(new_password, user.password_hash):
            raise UserError("New password must differ from the current one.")
        user.password_hash = sec.hash_password(new_password)
        user.password_changed_at = utcnow()
        user.failed_login_attempts = 0
        user.locked_until = None
        sec.log_audit("PASSWORD_CHANGED" if current_password is not None else "PASSWORD_RESET", "SUCCESS",
                      {"target": user.email},
                      user_id=actor["user_id"] if actor else None,
                      user_email=actor["email"] if actor else "cli", session=s)


def user_count() -> int:
    with get_session() as s:
        return s.execute(select(func.count()).select_from(User)).scalar_one()
