"""Main dashboard: database-driven KPIs and headline charts."""
from __future__ import annotations

import streamlit as st

import auth
from utils import analytics as an
from utils import charts as ch
from utils import ui
from utils.security import VIEW_DASHBOARD


def status_filter(key: str) -> tuple[str, ...]:
    options = an.status_options()
    chosen = st.multiselect("Employment status", options, default=an.default_statuses(options), key=key,
                            help="KPIs and charts include only employees with these statuses. "
                                 "Clear the selection to include everyone.")
    return tuple(chosen)


def render() -> None:
    user = auth.require_permission(VIEW_DASHBOARD)
    if not an.has_data():
        ui.no_data_guard(user)

    c1, c2 = st.columns([3, 1])
    with c1:
        statuses = status_filter("dash_status")
    k = an.kpis(statuses)
    with c2:
        periods = an.pay_periods()
        st.metric("Pay periods on record", len(periods),
                  help="Distinct months loaded into salary_records")

    if k["total_employees"] == 0:
        ui.empty_state("No employees match the selected statuses.", "Adjust the status filter above.")
        return

    ui.kpi_cards([
        ("◉", "Total Employees", f"{k['total_employees']:,}", "matching status filter"),
        ("▦", "Total Departments", f"{k['total_departments']:,}", f"{k['departments_registered']} registered"),
        ("◆", "Total Monthly Salary", ch.compact_money(k["total_monthly_salary"]),
         f"{ch.compact_money(k['total_monthly_salary'] * 12)} annualised"),
        ("≈", "Average Salary", ch.money(k["average_salary"]), "per employee / month"),
        ("▲", "Highest Salary", ch.money(k["highest_salary"]), "monthly"),
        ("▼", "Lowest Salary", ch.money(k["lowest_salary"]), "monthly"),
    ])

    summary = an.department_summary(statuses)
    left, right = st.columns([1.35, 1])
    with left, st.container(border=True):
        st.plotly_chart(ch.department_salary_bar(summary), config=ch.PLOTLY_CONFIG, key="dash_bar")
    with right, st.container(border=True):
        st.plotly_chart(ch.department_pie(summary), config=ch.PLOTLY_CONFIG, key="dash_pie")

    ui.section("Payroll trend", "from historical salary records")
    monthly = an.monthly_expenditure()
    if len(monthly) >= 2:
        with st.container(border=True):
            st.plotly_chart(ch.payroll_trend(monthly), config=ch.PLOTLY_CONFIG, key="dash_trend")
    else:
        st.info("The trend chart appears once salary records exist for at least two pay periods.")

    ui.section("Department snapshot")
    view = summary[["department", "employee_count", "total_monthly_salary", "average_salary",
                    "salary_contribution_pct"]]
    st.dataframe(
        view, hide_index=True, width="stretch",
        column_config={
            "department": "Department",
            "employee_count": st.column_config.NumberColumn("Employees", format="%d"),
            "total_monthly_salary": st.column_config.NumberColumn("Monthly payroll", format="localized"),
            "average_salary": st.column_config.NumberColumn("Average salary", format="localized"),
            "salary_contribution_pct": st.column_config.ProgressColumn(
                "Share of payroll", format="%.1f%%", min_value=0, max_value=100),
        },
    )
