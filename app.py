"""
SALARY INTELLIGENCE – Department Wise Salary Distribution
Entry point:  streamlit run app.py
"""
from __future__ import annotations

import time

import streamlit as st

from config import ConfigError, settings

st.set_page_config(
    page_title=f"{settings.app_name} · {settings.app_tagline}",
    page_icon="💠",
    layout="wide",
    initial_sidebar_state="expanded",
)

from utils import ui  # noqa: E402  (after set_page_config)

ui.load_css()


# --------------------------------------------------------------------------
# Infrastructure checks
# --------------------------------------------------------------------------
def _fatal(title: str, body: str) -> None:
    ui.header("System unavailable", title)
    st.error(body)
    ui.footer()
    st.stop()


try:
    _ = settings.database_url
except ConfigError as exc:
    _fatal("Configuration required", f"{exc}")

import auth  # noqa: E402
from database import check_connection, init_db, tables_exist  # noqa: E402
from utils import security as sec  # noqa: E402


@st.cache_resource(show_spinner=False)
def _bootstrap_schema() -> bool:
    if not tables_exist():
        init_db()
    return True


ok, reason = check_connection()
if not ok:
    _fatal("Database connection failed",
           "Could not connect to PostgreSQL. Check the DATABASE_URL / POSTGRES_* settings in your .env "
           f"file and that the server is reachable. (Error type: {reason})")
_bootstrap_schema()

if auth.user_count() == 0:
    ui.header("First-time setup", "No user accounts exist yet")
    st.warning("For security, the first administrator is created from the server command line, "
               "not from the browser.")
    st.code('python manage.py create-user --email you@yourbank.com --name "Your Name" --role Admin', language="bash")
    st.caption("You will be prompted for a password (hidden input). Reload this page afterwards.")
    ui.footer()
    st.stop()


# --------------------------------------------------------------------------
# Login screen
# --------------------------------------------------------------------------
def login_page() -> None:
    _, mid, _ = st.columns([1, 1.15, 1])
    with mid:
        st.html(f"""
        <div class="si-login-brand">
          <div class="mark">SI</div>
          <div class="kicker">Secure analytics gateway</div>
          <h1>{settings.app_name}</h1>
          <p>{settings.app_tagline}</p>
        </div>""")
        flash = st.session_state.pop("_flash", None)
        if flash:
            st.info(flash)
        with st.form("login", clear_on_submit=False, border=True):
            email = st.text_input("Work email", placeholder="name@yourbank.com", max_chars=254, autocomplete="username")
            password = st.text_input("Password", type="password", max_chars=256, autocomplete="current-password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            # Progressive per-browser-session throttle on top of per-account lockout.
            fails = st.session_state.get("_login_fails", 0)
            if fails >= 3:
                time.sleep(min(2 ** (fails - 2), 8))
            result = auth.authenticate(email, password)
            if result.ok:
                auth.start_session(result.user)
                st.rerun()
            st.session_state["_login_fails"] = fails + 1
            st.error(result.message)
        st.html('<div class="si-login-note">🔒 bcrypt-verified · sessions expire after '
                f'{settings.session_idle_timeout_minutes} min of inactivity · all access is audited</div>')


user = auth.enforce_session()

if not user:
    nav = st.navigation([st.Page(login_page, title="Sign in", icon=":material/lock:", url_path="login")],
                        position="hidden")
    nav.run()
    ui.footer()
    st.stop()


# --------------------------------------------------------------------------
# Authenticated shell
# --------------------------------------------------------------------------
from pages import (  # noqa: E402
    account, audit_logs, dashboard, data_import, departments, eda, employees, reports, users, visualizations,
)

PAGE_SPECS = [
    # (section, module, title, icon, url_path, permission, subtitle)
    ("Overview", dashboard, "Dashboard", ":material/space_dashboard:", "dashboard", sec.VIEW_DASHBOARD,
     "Organisation-wide payroll KPIs from verified records"),
    ("Analytics", departments, "Department Analytics", ":material/apartment:", "departments", sec.VIEW_DEPARTMENTS,
     "Department-wise salary distribution and drill-down"),
    ("Analytics", visualizations, "Visualizations", ":material/insights:", "visualizations", sec.VIEW_VISUALIZATIONS,
     "Interactive salary chart gallery"),
    ("Analytics", eda, "Analyst Workspace", ":material/query_stats:", "eda", sec.VIEW_EDA,
     "Exploratory data analysis of salary data"),
    ("Records", employees, "Employees", ":material/badge:", "employees", sec.VIEW_EMPLOYEES,
     "Searchable, access-controlled employee salary register"),
    ("Records", reports, "Reports", ":material/summarize:", "reports", sec.EXPORT_REPORTS,
     "Export PDF and Excel reports"),
    ("Administration", data_import, "Data Import", ":material/upload_file:", "data_import", sec.IMPORT_DATA,
     "Validate and load verified payroll datasets"),
    ("Administration", users, "User Management", ":material/manage_accounts:", "users", sec.MANAGE_USERS,
     "Accounts, roles and access"),
    ("Administration", audit_logs, "Audit Logs", ":material/policy:", "audit_logs", sec.VIEW_AUDIT,
     "Security and activity trail"),
    ("Account", account, "My Account", ":material/person:", "account", None,
     "Profile and password"),
]

sections: dict[str, list] = {}
meta: dict[str, tuple[str, str]] = {}
ui.PAGES.clear()
for section_name, module, title, icon, url_path, perm, subtitle in PAGE_SPECS:
    if perm is not None and not sec.has_permission(user["role"], perm):
        continue
    page = st.Page(module.render, title=title, icon=icon, url_path=url_path, default=(url_path == "dashboard"))
    sections.setdefault(section_name, []).append(page)
    meta[url_path] = (title, subtitle)
    ui.PAGES[url_path] = page

st.logo(str(ui.logo_path()), size="large")
nav = st.navigation(sections, position="sidebar")

with st.sidebar:
    ui.user_card(user)
    mins = max(1, auth.seconds_until_idle_timeout() // 60)
    st.html(f'<div class="si-session">Idle timeout <b>{settings.session_idle_timeout_minutes} min</b> · '
            f'signed in {user["login_at"].astimezone():%H:%M}</div>')
    if st.button("Sign out", icon=":material/logout:", width="stretch"):
        auth.logout("user")
        st.rerun()

title, subtitle = meta.get(nav.url_path, (nav.title, ""))
ui.header(title, subtitle, user, session_minutes_left=mins)
nav.run()
ui.footer()
