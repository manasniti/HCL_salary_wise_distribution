"""Interactive chart gallery."""
from __future__ import annotations

import streamlit as st

import auth
from pages.dashboard import status_filter
from utils import analytics as an
from utils import charts as ch
from utils import ui
from utils.security import VIEW_EMPLOYEE_PII, VIEW_VISUALIZATIONS, has_permission, mask_name


def _chart(fig, key: str) -> None:
    with st.container(border=True):
        st.plotly_chart(fig, config=ch.PLOTLY_CONFIG, key=key)


def render() -> None:
    user = auth.require_permission(VIEW_VISUALIZATIONS)
    if not an.has_data():
        ui.no_data_guard(user)

    c1, c2 = st.columns([2, 1])
    with c1:
        statuses = status_filter("viz_status")
    summary = an.department_summary(statuses)
    if summary.empty:
        ui.empty_state("No employees match the selected statuses.")
        return
    all_depts = summary["department"].tolist()
    with c2:
        focus = st.multiselect("Departments", all_depts, key="viz_depts", placeholder="All departments")
    if focus:
        summary = summary[summary.department.isin(focus)]
        summary = summary.assign(salary_contribution_pct=summary.total_monthly_salary / summary.total_monthly_salary.sum() * 100)

    emp = an.employee_frame(statuses)
    if focus:
        emp = emp[emp.department.isin(focus)]
    can_pii = has_permission(user["role"], VIEW_EMPLOYEE_PII)
    emp = emp.assign(employee_label=[(f"{n} ({c})" if can_pii else mask_name(n, c))
                                     for n, c in zip(emp.full_name, emp.employee_code)])
    k = an.kpis(statuses)

    ui.section("Department comparison")
    a, b = st.columns([1.3, 1])
    with a:
        _chart(ch.department_salary_bar(summary), "v_bar")
    with b:
        _chart(ch.department_pie(summary), "v_pie")
    a, b = st.columns(2)
    with a:
        _chart(ch.average_salary_comparison(summary, k["average_salary"]), "v_avg")
    with b:
        _chart(ch.employee_count_chart(summary), "v_cnt")
    _chart(ch.salary_range_chart(summary), "v_range")

    ui.section("Distribution")
    a, b = st.columns(2)
    stats = an.descriptive_stats(emp["monthly_salary"])
    with a:
        _chart(ch.salary_histogram(emp, 20, stats.get("mean"), stats.get("median")), "v_hist")
    with b:
        _chart(ch.salary_boxplot(emp, by_department=True), "v_box")
    a, b = st.columns(2)
    with a:
        _chart(ch.salary_band_chart(an.salary_bands(emp)), "v_bands")
    with b:
        _chart(ch.status_chart(an.employee_frame(())), "v_status")
    _chart(ch.tenure_scatter(emp), "v_scatter")

    ui.section("Trends", "historical salary records")
    monthly = an.monthly_expenditure()
    if len(monthly) >= 2:
        _chart(ch.payroll_trend(monthly), "v_trend")
        trend = an.department_trend()
        if focus:
            trend = trend[trend.department.isin(focus)]
        _chart(ch.department_trend_chart(trend), "v_dtrend")
    else:
        st.info("Trend charts appear once salary records exist for at least two pay periods.")
