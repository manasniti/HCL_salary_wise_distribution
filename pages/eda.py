"""Data Analyst workspace: exploratory data analysis over verified salary data."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import auth
from utils import analytics as an
from utils import charts as ch
from utils import ui
from utils.security import VIEW_EDA, VIEW_EMPLOYEE_PII, has_permission, mask_name

MONEY = "localized"


def _label(df: pd.DataFrame, can_pii: bool) -> pd.DataFrame:
    df = df.copy()
    df["employee_label"] = (df["full_name"] + " (" + df["employee_code"] + ")") if can_pii else \
        [mask_name(n, c) for n, c in zip(df["full_name"], df["employee_code"])]
    return df


def _load(user: dict) -> tuple[pd.DataFrame, str]:
    c1, c2 = st.columns([1, 2])
    periods = an.pay_periods()
    source = c1.radio("Data source", ["Employee master (current)", "Salary records (pay period)"],
                      key="eda_src", disabled=not periods,
                      help="Employee master holds each employee's current salary. Salary records hold what "
                           "was paid in a specific month.")
    if source.startswith("Salary") and periods:
        p = c2.selectbox("Pay period", list(reversed(periods)), format_func=lambda d: d.strftime("%B %Y"),
                         key="eda_period")
        return an.salary_record_frame(p), f"salary records · {p:%b %Y}"
    options = an.status_options()
    statuses = tuple(c2.multiselect("Employment status", options, default=an.default_statuses(options),
                                    key="eda_status"))
    return an.employee_frame(statuses), "employee master"


def render() -> None:
    user = auth.require_permission(VIEW_EDA)
    if not an.has_data():
        ui.no_data_guard(user)
    can_pii = has_permission(user["role"], VIEW_EMPLOYEE_PII)

    df, source_label = _load(user)
    if df.empty:
        ui.empty_state("No records in the selected scope.")
        return
    df = _label(df, can_pii)
    analysis_cols = ["employee_code", "department", "designation", "monthly_salary", "annual_salary",
                     "joining_date", "employment_status", "tenure_years"]
    data = df[analysis_cols]

    tabs = st.tabs(["Overview", "Data quality", "Statistics", "Distribution", "Outliers", "Departments",
                    "Correlation"])

    # ------------------------------------------------------------------ overview
    with tabs[0]:
        ov = an.dataset_overview(data)
        ui.kpi_cards([
            ("▤", "Rows", f"{ov['rows']:,}", source_label),
            ("▥", "Columns", f"{ov['columns']}", f"{ov['numeric_columns']} numeric"),
            ("◌", "Missing cells", f"{ov['missing_cells']:,}", "across all columns"),
            ("⧉", "Duplicate rows", f"{ov['duplicate_rows']:,}", "exact matches"),
            ("◫", "Memory", f"{ov['memory_kb']:,} KB", "in-memory frame"),
        ])
        st.markdown("**Sample (first 10 rows)**")
        sample = df[["employee_label"] + analysis_cols[1:]].head(10)
        st.dataframe(sample, hide_index=True, width="stretch", column_config={
            "employee_label": "Employee",
            "monthly_salary": st.column_config.NumberColumn(format=MONEY),
            "annual_salary": st.column_config.NumberColumn(format=MONEY),
            "joining_date": st.column_config.DateColumn(format="DD MMM YYYY"),
        })

    # ------------------------------------------------------------------ data quality
    with tabs[1]:
        c1, c2 = st.columns(2)
        with c1:
            ui.section("Missing value analysis")
            mv = an.missing_values(data)
            st.dataframe(mv, hide_index=True, width="stretch", column_config={
                "missing_pct": st.column_config.ProgressColumn("Missing %", format="%.2f%%", min_value=0, max_value=100)})
            if mv["missing"].sum() == 0:
                st.success("No missing values — database constraints enforce required fields.")
        with c2:
            ui.section("Data type analysis")
            st.dataframe(an.dtype_table(data), hide_index=True, width="stretch")
        ui.section("Duplicate detection")
        dup = an.duplicate_report(df)
        d1, d2, d3 = st.columns(3)
        d1.metric("Exact duplicate rows", len(dup["exact"]))
        d2.metric("Duplicate employee IDs", dup["id_duplicates"], help="Prevented by a unique constraint")
        d3.metric("Probable duplicates", len(dup["probable"]),
                  help="Same " + ", ".join(dup["probable_keys"]) + " but different employee IDs")
        if len(dup["probable"]):
            show = dup["probable"][["employee_label", "department", "designation", "joining_date", "monthly_salary"]]
            st.dataframe(show, hide_index=True, width="stretch")

    # ------------------------------------------------------------------ statistics
    stats = an.descriptive_stats(df["monthly_salary"])
    with tabs[2]:
        ui.kpi_cards([
            ("μ", "Mean", ch.money(stats["mean"]), "arithmetic average"),
            ("M", "Median", ch.money(stats["median"]), "50th percentile"),
            ("Mo", "Mode", ch.money(stats["mode"]), f"occurs {stats['mode_count']}×"),
            ("σ", "Std deviation", ch.money(stats["std"]), "sample (n−1)"),
            ("σ²", "Variance", f"{stats['variance']:,.0f}", "sample (n−1)"),
            ("CV", "Coeff. of variation", "—" if stats["cv_pct"] is None else f"{stats['cv_pct']:.1f}%", "σ / μ"),
        ])
        c1, c2 = st.columns(2)
        with c1:
            ui.section("Quartiles & spread")
            q = pd.DataFrame({
                "Measure": ["Minimum", "Q1 (25%)", "Q2 / Median (50%)", "Q3 (75%)", "Maximum", "IQR (Q3−Q1)", "Range"],
                "Monthly salary": [stats["min"], stats["q1"], stats["q2"], stats["q3"], stats["max"],
                                   stats["iqr"], stats["range"]],
            }).round(2)
            st.dataframe(q, hide_index=True, width="stretch",
                         column_config={"Monthly salary": st.column_config.NumberColumn(format=MONEY)})
        with c2:
            ui.section("Shape")
            skew, kurt = stats["skewness"], stats["kurtosis"]
            st.metric("Skewness", "—" if skew is None else f"{skew:.3f}")
            st.metric("Excess kurtosis", "—" if kurt is None else f"{kurt:.3f}")
            if skew is not None:
                direction = ("right-skewed — a tail of higher earners pulls the mean above the median"
                             if skew > 0.5 else "left-skewed — the mean sits below the median"
                             if skew < -0.5 else "approximately symmetric")
                st.caption(f"The salary distribution is {direction}.")
        ui.section("pandas describe()")
        desc = data[["monthly_salary", "annual_salary", "tenure_years"]].describe().T.round(2)
        st.dataframe(desc, width="stretch")

    # ------------------------------------------------------------------ distribution
    with tabs[3]:
        bins = st.slider("Histogram bins", 5, 60, 20, key="eda_bins")
        with st.container(border=True):
            st.plotly_chart(ch.salary_histogram(df, bins, stats["mean"], stats["median"]),
                            config=ch.PLOTLY_CONFIG, key="eda_hist")
        c1, c2 = st.columns(2)
        with c1, st.container(border=True):
            st.plotly_chart(ch.salary_boxplot(df, by_department=False), config=ch.PLOTLY_CONFIG, key="eda_box_all")
        with c2, st.container(border=True):
            st.plotly_chart(ch.salary_band_chart(an.salary_bands(df)), config=ch.PLOTLY_CONFIG, key="eda_bands")

    # ------------------------------------------------------------------ outliers
    with tabs[4]:
        k = st.select_slider("IQR multiplier (k)", options=[1.0, 1.5, 2.0, 2.5, 3.0], value=1.5, key="eda_k",
                             help="Fences are Q1 − k·IQR and Q3 + k·IQR. 1.5 is Tukey's standard; 3.0 flags extreme values only.")
        out, lo_f, hi_f = an.iqr_outliers(df, k=k)
        o1, o2, o3, o4 = st.columns(4)
        o1.metric("Lower fence", ch.money(lo_f))
        o2.metric("Upper fence", ch.money(hi_f))
        o3.metric("Outliers", f"{len(out):,}")
        o4.metric("Share of records", f"{len(out) / len(df) * 100:.1f}%")
        if out.empty:
            st.success("No salaries fall outside the IQR fences.")
        else:
            st.dataframe(out[["employee_label", "department", "designation", "monthly_salary", "outlier_type"]],
                         hide_index=True, width="stretch", column_config={
                             "employee_label": "Employee",
                             "monthly_salary": st.column_config.NumberColumn("Monthly salary", format=MONEY)})
            st.caption("Outliers are statistical flags for review, not errors. Senior roles legitimately sit above the fence.")

    # ------------------------------------------------------------------ departments
    with tabs[5]:
        with st.container(border=True):
            st.plotly_chart(ch.salary_boxplot(df, by_department=True), config=ch.PLOTLY_CONFIG, key="eda_box_dept")
        st.dataframe(an.department_stats(df), hide_index=True, width="stretch", column_config={
            c: st.column_config.NumberColumn(format=MONEY) for c in ("mean", "median", "std", "min", "q1", "q3", "max", "iqr")
        })

    # ------------------------------------------------------------------ correlation
    with tabs[6]:
        corr = an.correlation_matrix(df)
        if corr.empty:
            st.info("Correlation needs at least three records with two independent numeric fields.")
        else:
            r = corr.loc["monthly_salary", "tenure_years"]
            strength = "very weak" if abs(r) < 0.2 else "weak" if abs(r) < 0.4 else "moderate" if abs(r) < 0.6 \
                else "strong" if abs(r) < 0.8 else "very strong"
            c1, c2 = st.columns([1, 1.6])
            with c1, st.container(border=True):
                st.plotly_chart(ch.correlation_heatmap(corr), config=ch.PLOTLY_CONFIG, key="eda_corr")
                st.markdown(f"**r = {r:.3f}** — a {strength} {'positive' if r >= 0 else 'negative'} "
                            "linear relationship between tenure and monthly salary.")
                st.caption("Annual salary is excluded because it is exactly 12 × monthly salary (r = 1 by construction). "
                           "Correlation does not imply causation.")
            with c2, st.container(border=True):
                st.plotly_chart(ch.tenure_scatter(df), config=ch.PLOTLY_CONFIG, key="eda_scatter")
