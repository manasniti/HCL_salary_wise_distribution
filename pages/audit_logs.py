"""Audit log viewer (Admin only)."""
from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta

import pandas as pd
import streamlit as st

import auth
from database import read_df
from utils import ui
from utils.security import VIEW_AUDIT, escape_like, log_audit, neutralise_formula


def _where(actions, statuses, email, d_from, d_to):
    clauses, params, exp = ["created_at >= :dfrom AND created_at < :dto"], {
        "dfrom": datetime.combine(d_from, time.min).astimezone(),
        "dto": datetime.combine(d_to + timedelta(days=1), time.min).astimezone(),
    }, []
    if actions:
        clauses.append("action IN :actions"); params["actions"] = list(actions); exp.append("actions")
    if statuses:
        clauses.append("status IN :statuses"); params["statuses"] = list(statuses); exp.append("statuses")
    if email:
        clauses.append("LOWER(COALESCE(user_email, '')) LIKE :email ESCAPE '\\'")
        params["email"] = f"%{escape_like(email.strip().lower())}%"
    return " AND ".join(clauses), params, tuple(exp)


def render() -> None:
    auth.require_permission(VIEW_AUDIT)

    since = datetime.now().astimezone() - timedelta(hours=24)
    s = read_df("""
        SELECT
          COUNT(*) FILTER (WHERE action = 'LOGIN_SUCCESS')                       AS logins,
          COUNT(*) FILTER (WHERE action IN ('LOGIN_FAILED','LOGIN_BLOCKED'))     AS failed,
          COUNT(*) FILTER (WHERE action = 'ACCOUNT_LOCKED')                      AS locked,
          COUNT(*) FILTER (WHERE action IN ('REPORT_EXPORT','EMPLOYEE_EXPORT'))  AS exports,
          COUNT(*) FILTER (WHERE status = 'DENIED')                              AS denied
        FROM audit_logs WHERE created_at >= :since
    """, {"since": since}).iloc[0]
    ui.kpi_cards([
        ("✓", "Sign-ins", f"{int(s.logins):,}", "last 24 hours"),
        ("✕", "Failed sign-ins", f"{int(s.failed):,}", "last 24 hours"),
        ("⊘", "Lockouts", f"{int(s.locked):,}", "last 24 hours"),
        ("⇩", "Exports", f"{int(s.exports):,}", "last 24 hours"),
        ("⚠", "Access denied", f"{int(s.denied):,}", "last 24 hours"),
    ])

    action_opts = read_df("SELECT DISTINCT action FROM audit_logs ORDER BY 1")["action"].tolist()
    with st.expander("Filters", expanded=True, icon=":material/tune:"):
        c1, c2, c3, c4 = st.columns([1.4, 1, 1.2, 1.2])
        actions = c1.multiselect("Action", action_opts, key="aud_actions")
        statuses = c2.multiselect("Status", ["SUCCESS", "FAILURE", "DENIED", "INFO"], key="aud_status")
        email = c3.text_input("User email contains", max_chars=120, key="aud_email")
        rng = c4.date_input("Date range", value=(date.today() - timedelta(days=30), date.today()),
                            max_value=date.today(), key="aud_range")
    d_from, d_to = (rng if isinstance(rng, (list, tuple)) and len(rng) == 2 else (date.today(), date.today()))
    where, params, exp = _where(actions, statuses, email, d_from, d_to)

    total = int(read_df(f"SELECT COUNT(*) AS n FROM audit_logs WHERE {where}", params, exp)["n"].iloc[0])
    if total == 0:
        ui.empty_state("No audit events match these filters.")
        return
    page_size = 50
    pages = max(1, math.ceil(total / page_size))
    page = st.number_input(f"Page (of {pages})", 1, pages, 1, key="aud_page")
    df = read_df(f"""
        SELECT created_at AS "Time", user_email AS "User", action AS "Action", status AS "Status",
               details AS "Details", session_id AS "Session"
        FROM audit_logs WHERE {where} ORDER BY created_at DESC, id DESC LIMIT :lim OFFSET :off
    """, {**params, "lim": page_size, "off": (page - 1) * page_size}, exp)
    df["Details"] = df["Details"].apply(lambda d: json.dumps(d, ensure_ascii=False) if isinstance(d, (dict, list)) else (d or ""))
    df["Time"] = pd.to_datetime(df["Time"], utc=True).dt.tz_convert(datetime.now().astimezone().tzinfo)
    st.dataframe(df, hide_index=True, width="stretch", height=640, column_config={
        "Time": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm:ss"),
        "Details": st.column_config.TextColumn(width="large"),
        "Session": st.column_config.TextColumn(width="small"),
    })
    st.caption(f"{total:,} events · newest first")

    if st.button("Prepare CSV export of filtered events", icon=":material/download:"):
        full = read_df(f"""SELECT created_at, user_email, action, status, details, session_id
                           FROM audit_logs WHERE {where} ORDER BY created_at DESC""", params, exp)
        full["details"] = full["details"].apply(lambda d: json.dumps(d, ensure_ascii=False) if d else "")
        full = full.map(neutralise_formula)
        log_audit("AUDIT_EXPORT", "SUCCESS", {"rows": len(full)})
        st.session_state["_aud_csv"] = full.to_csv(index=False).encode("utf-8-sig")
    if "_aud_csv" in st.session_state:
        st.download_button("Download audit_logs.csv", st.session_state["_aud_csv"],
                           file_name=f"audit_logs_{date.today():%Y%m%d}.csv", mime="text/csv",
                           type="primary", on_click="ignore")
