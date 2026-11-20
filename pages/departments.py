"""Department-wise salary analytics with drill-down."""
from __future__ import annotations

import streamlit as st

import auth
from pages.dashboard import status_filter
from utils import analytics as an
from utils import charts as ch
from utils import ui
from utils.security import VIEW_DEPARTMENTS, VIEW_EMPLOYEE_PII, has_permission, mask_name

MONEY = "localized"


def render() -> None:
    user = auth.require_permission(VIEW_DEPARTMENTS)
    if not an.has_data():
        ui.no_data_guard(user)

    statuses = status_filter("dept_status")
    summary = an.department_summary(statuses)
    if summary.empty:
        ui.empty_state("No employees match the selected statuses.")
        return

    ui.section("Department summary", f"{len(summary)} departments · sorted by monthly payroll")
    st.dataframe(
        summary, hide_index=True, width="stretch",
        column_config={
            "department": st.column_config.TextColumn("Department", pinned=True),
            "employee_count": st.column_config.NumberColumn("Employees", format="%d"),
            "total_monthly_salary": st.column_config.NumberColumn("Total monthly", format=MONEY),
            "average_salary": st.column_config.NumberColumn("Average", format=MONEY),
            "minimum_salary": st.column_config.NumberColumn("Minimum", format=MONEY),
            "maximum_salary": st.column_config.NumberColumn("Maximum", format=MONEY),
            "annual_salary_expenditure": st.column_config.NumberColumn("Annual expenditure", format=MONEY),
            "salary_contribution_pct": st.column_config.ProgressColumn(
                "Contribution", format="%.2f%%", min_value=0, max_value=100),
        },
    )

    k = an.kpis(statuses)
    tabs = st.tabs(["Payroll", "Share", "Average", "Headcount", "Range", "Trend"])
    with tabs[0], st.container(border=True):
        st.plotly_chart(ch.department_salary_bar(summary), config=ch.PLOTLY_CONFIG, key="d_bar")
    with tabs[1], st.container(border=True):
        st.plotly_chart(ch.department_pie(summary), config=ch.PLOTLY_CONFIG, key="d_pie")
    with tabs[2], st.container(border=True):
        st.plotly_chart(ch.average_salary_comparison(summary, k["average_salary"]), config=ch.PLOTLY_CONFIG, key="d_avg")
    with tabs[3], st.container(border=True):
        st.plotly_chart(ch.employee_count_chart(summary), config=ch.PLOTLY_CONFIG, key="d_cnt")
    with tabs[4], st.container(border=True):
        st.plotly_chart(ch.salary_range_chart(summary), config=ch.PLOTLY_CONFIG, key="d_rng")
    with tabs[5]:
        trend = an.department_trend()
        if not trend.empty and trend["pay_period"].nunique() >= 2:
            with st.container(border=True):
                st.plotly_chart(ch.department_trend_chart(trend), config=ch.PLOTLY_CONFIG, key="d_trend")
        else:
            st.info("Salary trend appears once salary records exist for at least two pay periods.")

    # ------------------------------------------------------------------ drill-down
    ui.section("Department drill-down")
    dept = st.selectbox("Department", summary["department"].tolist(), key="dept_pick")
    row = summary.set_index("department").loc[dept]
    ui.kpi_cards([
        ("◉", "Employees", f"{int(row.employee_count):,}", f"{row.salary_contribution_pct:.1f}% of payroll"),
        ("◆", "Monthly payroll", ch.compact_money(row.total_monthly_salary),
         f"{ch.compact_money(row.annual_salary_expenditure)} per year"),
        ("≈", "Average", ch.money(row.average_salary), "per month"),
        ("↕", "Range", f"{ch.compact_money(row.minimum_salary)} – {ch.compact_money(row.maximum_salary)}", "min – max"),
    ])

    c1, c2 = st.columns([1.2, 1])
    with c1, st.container(border=True):
        desig = an.designation_breakdown(dept, statuses)
        st.plotly_chart(ch.designation_chart(desig), config=ch.PLOTLY_CONFIG, key="d_desig")
    with c2:
        st.markdown("**Top earners**")
        emp = an.employee_frame(statuses)
        top = emp[emp.department == dept].nlargest(10, "monthly_salary")[
            ["employee_code", "full_name", "designation", "monthly_salary"]].copy()
        if not has_permission(user["role"], VIEW_EMPLOYEE_PII):
            top["full_name"] = [mask_name(n, c) for n, c in zip(top.full_name, top.employee_code)]
        st.dataframe(top, hide_index=True, width="stretch", column_config={
            "employee_code": "Employee ID", "full_name": "Name", "designation": "Designation",
            "monthly_salary": st.column_config.NumberColumn("Monthly salary", format=MONEY),
        })
