"""Secure, searchable employee salary register with server-side paging."""
from __future__ import annotations

import io
import math
from datetime import date

import pandas as pd
import streamlit as st

import auth
from utils import analytics as an
from utils import ui
from utils.security import (
    EXPORT_EMPLOYEES, VIEW_EMPLOYEE_PII, VIEW_EMPLOYEES, has_permission, log_audit, mask_name, neutralise_formula,
)

MONEY = "localized"


def _masked(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["Employee Name"] = [mask_name(n, c) for n, c in zip(df["Employee Name"], df["Employee ID"])]
    return df


def render() -> None:
    user = auth.require_permission(VIEW_EMPLOYEES)
    if not an.has_data():
        ui.no_data_guard(user)
    can_pii = has_permission(user["role"], VIEW_EMPLOYEE_PII)
    can_export = has_permission(user["role"], EXPORT_EMPLOYEES)

    lo, hi = an.salary_bounds()
    with st.expander("Filters", expanded=True, icon=":material/tune:"):
        c1, c2, c3 = st.columns([1.4, 1.2, 1.2])
        search = c1.text_input("Search", placeholder="Name, employee ID or designation", max_chars=80,
                               key="emp_search")
        depts = c2.multiselect("Department", an.department_options(), key="emp_depts")
        statuses = c3.multiselect("Employment status", an.status_options(), key="emp_status")
        c4, c5 = st.columns(2)
        salary = c4.slider("Monthly salary range", min_value=float(lo), max_value=float(max(hi, lo + 1)),
                           value=(float(lo), float(max(hi, lo + 1))), step=max(1.0, round((hi - lo) / 200, 0)),
                           key="emp_salary")
        joined = c5.date_input("Joining date between", value=(date(1950, 1, 1), date.today()),
                               min_value=date(1950, 1, 1), max_value=date.today(), key="emp_joined")
        joined = tuple(joined) if isinstance(joined, (list, tuple)) and len(joined) == 2 else None
        c6, c7, c8 = st.columns(3)
        sort_by = c6.selectbox("Sort by", list(an.SORTABLE.keys()), index=4, key="emp_sort")
        descending = c7.toggle("Descending", value=True, key="emp_desc")
        page_size = c8.selectbox("Rows per page", [10, 25, 50, 100], index=1, key="emp_ps")

    salary_range = None if salary == (float(lo), float(max(hi, lo + 1))) else salary
    filters = dict(search=search, departments=tuple(depts), statuses=tuple(statuses),
                   salary_range=salary_range, joined=joined)

    # Reset to page 1 whenever filters change.
    sig = repr((filters, sort_by, descending, page_size))
    if st.session_state.get("_emp_sig") != sig:
        st.session_state["_emp_sig"] = sig
        st.session_state["emp_page"] = 1

    _, total = an.employees_page(**filters, page=1, page_size=1)
    pages = max(1, math.ceil(total / page_size))
    page = min(st.session_state.get("emp_page", 1), pages)

    df, total = an.employees_page(**filters, sort_by=sort_by, descending=descending, page=page, page_size=page_size)
    if total == 0:
        ui.empty_state("No employees match these filters.", "Broaden the search or clear filters.")
        return
    if not can_pii:
        df = _masked(df)
        st.caption("🔒 Names are pseudonymised for your role.")

    st.dataframe(df, hide_index=True, width="stretch", height=min(38 * (len(df) + 1) + 4, 940), column_config={
        "Employee ID": st.column_config.TextColumn(pinned=True),
        "Monthly Salary": st.column_config.NumberColumn(format=MONEY),
        "Annual Salary": st.column_config.NumberColumn(format=MONEY),
        "Joining Date": st.column_config.DateColumn(format="DD MMM YYYY"),
    })

    p1, p2, p3, p4 = st.columns([1, 1, 2, 2])
    if p1.button("◀ Previous", disabled=page <= 1, width="stretch"):
        st.session_state["emp_page"] = page - 1
        st.rerun()
    if p2.button("Next ▶", disabled=page >= pages, width="stretch"):
        st.session_state["emp_page"] = page + 1
        st.rerun()
    start = (page - 1) * page_size + 1
    p3.markdown(f"Showing **{start:,}–{start + len(df) - 1:,}** of **{total:,}** · page **{page}** / {pages}")

    # ------------------------------------------------------------------ export
    if can_export:
        with p4:
            fmt = st.segmented_control("Export", ["CSV", "Excel"], default="Excel", key="emp_fmt",
                                       label_visibility="collapsed")
            if st.button("Prepare export of all filtered rows", icon=":material/download:", width="stretch"):
                full = an.employees_export(**filters, sort_by=sort_by, descending=descending)
                full = full.map(neutralise_formula)
                if fmt == "CSV":
                    data, mime, ext = full.to_csv(index=False).encode("utf-8-sig"), "text/csv", "csv"
                else:
                    buf = io.BytesIO()
                    full.to_excel(buf, index=False, sheet_name="Employees", engine="openpyxl")
                    data, ext = buf.getvalue(), "xlsx"
                    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                log_audit("EMPLOYEE_EXPORT", "SUCCESS", {"rows": len(full), "format": ext,
                                                         "filters": {k: v for k, v in filters.items() if v}})
                st.session_state["_emp_export"] = (data, mime, f"employees_{date.today():%Y%m%d}.{ext}")
            if "_emp_export" in st.session_state:
                data, mime, name = st.session_state["_emp_export"]
                st.download_button(f"Download {name}", data, file_name=name, mime=mime, type="primary",
                                   width="stretch", on_click="ignore")
    else:
        p4.caption("Employee-level export is limited to Admin and HR Manager roles. "
                   "Aggregated reports are available under Reports.")
