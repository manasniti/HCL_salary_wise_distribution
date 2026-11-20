"""Reusable UI building blocks (header, KPI cards, empty states, footer)."""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

import streamlit as st

from config import BASE_DIR, EMPTY_STATE_MESSAGE, settings

ASSETS = BASE_DIR / "assets"

# Filled by app.py with the st.Page objects the current user may open.
PAGES: dict = {}


@st.cache_data(show_spinner=False)
def _css() -> str:
    return (ASSETS / "style.css").read_text(encoding="utf-8")


def load_css() -> None:
    st.html(f"<style>{_css()}</style>")


def header(title: str, subtitle: str = "", user: dict | None = None, session_minutes_left: int | None = None) -> None:
    chips = []
    if user:
        chips.append(f'<span class="si-chip"><span class="dot"></span>{escape(user["role"])}</span>')
    if session_minutes_left is not None:
        chips.append(f'<span class="si-chip">Session · {session_minutes_left} min</span>')
    chips.append(f'<span class="si-chip">{datetime.now():%d %b %Y · %H:%M}</span>')
    st.html(f"""
    <div class="si-header">
      <div class="left">
        <div class="mark">SI</div>
        <div>
          <div class="kicker">{escape(settings.app_name)} · {escape(settings.app_tagline)}</div>
          <div class="title">{escape(title)}</div>
          {f'<div class="subtitle">{escape(subtitle)}</div>' if subtitle else ''}
        </div>
      </div>
      <div class="right">{''.join(chips)}</div>
    </div>""")


def kpi_cards(items: list[tuple[str, str, str, str]]) -> None:
    """items: (icon, label, value, sub)."""
    cards = "".join(
        f'<div class="si-kpi"><div class="label"><span class="icon">{escape(icon)}</span>{escape(label)}</div>'
        f'<div class="value">{escape(value)}</div><div class="sub">{escape(sub)}</div></div>'
        for icon, label, value, sub in items
    )
    st.html(f'<div class="si-kpi-grid">{cards}</div>')


def section(title: str, caption: str = "") -> None:
    st.html(f'<div class="si-section"><span class="bar"></span><h3>{escape(title)}</h3>'
            f'<span class="cap">{escape(caption)}</span></div>')


def empty_state(message: str = EMPTY_STATE_MESSAGE, hint: str = "") -> None:
    st.html(f'<div class="si-empty"><div class="glyph">◈</div><div class="msg">{escape(message)}</div>'
            f'{f"<div class=hint>{escape(hint)}</div>" if hint else ""}</div>')


def no_data_guard(user: dict) -> None:
    """Show the canonical empty state and stop rendering the page."""
    from utils.security import IMPORT_DATA, has_permission
    hint = ("Upload a verified CSV or Excel dataset from Administration → Data Import."
            if has_permission(user["role"], IMPORT_DATA)
            else "Ask an administrator to import verified payroll data.")
    empty_state(EMPTY_STATE_MESSAGE, hint)
    if has_permission(user["role"], IMPORT_DATA) and "data_import" in PAGES:
        st.page_link(PAGES["data_import"], label="Go to Data Import", icon=":material/upload_file:")
    st.stop()


def user_card(user: dict) -> None:
    st.html(f"""<div class="si-user-card"><div class="name">{escape(user['full_name'])}</div>
    <div class="email">{escape(user['email'])}</div><span class="si-role">{escape(user['role'])}</span></div>""")


def footer() -> None:
    st.html(f"""
    <div class="si-footer">
      <div><span class="brand">{escape(settings.app_name)}</span><span class="sep">//</span>{escape(settings.organisation_name)}</div>
      <div>Verified records only<span class="sep">·</span>Role-based access<span class="sep">·</span>Audited sessions</div>
      <div>© {datetime.now().year} · Confidential — internal use</div>
    </div>""")


def logo_path() -> Path:
    return ASSETS / "logo.svg"
