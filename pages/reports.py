"""Report exports (PDF / Excel)."""
from __future__ import annotations

from datetime import date

import streamlit as st

import auth
from utils import analytics as an
from utils import report_generator as rg
from utils import ui
from utils.security import EXPORT_EDA, EXPORT_REPORTS, VIEW_EMPLOYEE_PII, has_permission, log_audit

REPORTS = {
    "department": ("Department salary report", "Per-department headcount, payroll, averages, min/max, annual "
                                                 "expenditure and contribution share, with a payroll chart."),
    "employee": ("Employee salary summary", "One row per employee with monthly and annual salary."),
    "monthly": ("Monthly expenditure report", "Payroll per pay period with month-on-month change and a "
                                               "department × month matrix."),
    "eda": ("EDA report", "Descriptive statistics, quartiles, IQR outliers, department comparison, "
                          "missing values and correlation."),
}


def render() -> None:
    user = auth.require_permission(EXPORT_REPORTS)
    if not an.has_data():
        ui.no_data_guard(user)

    available = [k for k in REPORTS if k != "eda" or has_permission(user["role"], EXPORT_EDA)]
    c1, c2 = st.columns([1.2, 1])
    with c1, st.container(border=True):
        kind = st.radio("Report", available, format_func=lambda k: REPORTS[k][0], key="rep_kind")
        st.caption(REPORTS[kind][1])
    with c2, st.container(border=True):
        fmt = st.segmented_control("Format", ["PDF", "Excel"], default="PDF", key="rep_fmt")
        statuses: tuple[str, ...] = ()
        if kind != "monthly":
            options = an.status_options()
            statuses = tuple(st.multiselect("Employment status", options, default=an.default_statuses(options),
                                            key="rep_status"))
        if kind == "employee" and not has_permission(user["role"], VIEW_EMPLOYEE_PII):
            st.caption("🔒 Employee names will be pseudonymised for your role.")
        generate = st.button("Generate report", type="primary", icon=":material/description:", width="stretch")

    if generate:
        with st.spinner("Building report from the database…"):
            if kind == "department":
                report = rg.department_report(user, statuses)
            elif kind == "employee":
                report = rg.employee_summary_report(user, statuses, has_permission(user["role"], VIEW_EMPLOYEE_PII))
            elif kind == "monthly":
                report = rg.monthly_expenditure_report(user)
            else:
                report = rg.eda_report(user, statuses)
            if report is None:
                st.session_state.pop("_report", None)
                ui.empty_state("No verified salary records available.", "Nothing matches the selected scope.")
                return
            fmt = fmt or "PDF"
            if fmt == "PDF":
                data, mime, ext = rg.to_pdf(report), "application/pdf", "pdf"
            else:
                data, ext = rg.to_excel(report), "xlsx"
                mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            name = f"{kind}_salary_report_{date.today():%Y%m%d}.{ext}"
            log_audit("REPORT_EXPORT", "SUCCESS", {"report": kind, "format": ext, "statuses": list(statuses),
                                                   "rows": sum(len(t) for _, t in report.tables)})
            st.session_state["_report"] = (data, mime, name, report.title)

    if "_report" in st.session_state:
        data, mime, name, title = st.session_state["_report"]
        st.success(f"{title} is ready ({len(data) / 1024:,.0f} KB).")
        st.download_button(f"Download {name}", data, file_name=name, mime=mime, type="primary",
                           icon=":material/download:", on_click="ignore")
