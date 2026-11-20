"""
Database-driven analytics.

Aggregations run in PostgreSQL; statistical analysis (EDA) runs in
pandas/NumPy on rows read from the database. Nothing here fabricates or
imputes figures: when data is absent, functions return empty results and
the UI shows an empty state.
"""
from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd
import streamlit as st

from database import read_df, scalar
from utils.security import escape_like

CACHE_TTL = 120  # seconds; cleared immediately after every import


def clear_cache() -> None:
    st.cache_data.clear()


def _status_clause(statuses: Sequence[str] | None, alias: str = "e") -> tuple[str, dict, tuple]:
    if statuses:
        return f" AND {alias}.employment_status IN :statuses", {"statuses": list(statuses)}, ("statuses",)
    return "", {}, ()


# --------------------------------------------------------------------------
# Presence checks & options
# --------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def has_data() -> bool:
    return bool(scalar("SELECT EXISTS (SELECT 1 FROM employees)"))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def status_options() -> list[str]:
    df = read_df("SELECT DISTINCT employment_status FROM employees ORDER BY 1")
    return df["employment_status"].tolist()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def department_options() -> list[str]:
    return read_df("SELECT name FROM departments ORDER BY name")["name"].tolist()


def default_statuses(options: list[str]) -> list[str]:
    return ["Active"] if "Active" in options else list(options)


# --------------------------------------------------------------------------
# KPIs & department aggregates (SQL)
# --------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def kpis(statuses: tuple[str, ...] = ()) -> dict:
    clause, params, exp = _status_clause(statuses)
    df = read_df(f"""
        SELECT COUNT(*)                       AS total_employees,
               COUNT(DISTINCT e.department_id) AS total_departments,
               COALESCE(SUM(e.monthly_salary), 0) AS total_monthly_salary,
               AVG(e.monthly_salary)          AS average_salary,
               MAX(e.monthly_salary)          AS highest_salary,
               MIN(e.monthly_salary)          AS lowest_salary
        FROM employees e
        WHERE 1=1 {clause}
    """, params, exp)
    row = df.iloc[0].to_dict()
    out = {k: (float(v) if v is not None and not pd.isna(v) else None) for k, v in row.items()}
    out["total_employees"] = int(out["total_employees"] or 0)
    out["total_departments"] = int(out["total_departments"] or 0)
    out["departments_registered"] = int(scalar("SELECT COUNT(*) FROM departments") or 0)
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def department_summary(statuses: tuple[str, ...] = ()) -> pd.DataFrame:
    clause, params, exp = _status_clause(statuses)
    df = read_df(f"""
        SELECT d.name                    AS department,
               COUNT(e.id)               AS employee_count,
               SUM(e.monthly_salary)     AS total_monthly_salary,
               AVG(e.monthly_salary)     AS average_salary,
               MIN(e.monthly_salary)     AS minimum_salary,
               MAX(e.monthly_salary)     AS maximum_salary,
               SUM(e.monthly_salary) * 12 AS annual_salary_expenditure
        FROM departments d
        JOIN employees e ON e.department_id = d.id
        WHERE 1=1 {clause}
        GROUP BY d.name
        ORDER BY total_monthly_salary DESC
    """, params, exp)
    if df.empty:
        return df
    num = ["total_monthly_salary", "average_salary", "minimum_salary", "maximum_salary", "annual_salary_expenditure"]
    df[num] = df[num].astype(float)
    df["employee_count"] = df["employee_count"].astype(int)
    total = df["total_monthly_salary"].sum()
    df["salary_contribution_pct"] = (df["total_monthly_salary"] / total * 100) if total else 0.0
    df[num] = df[num].round(2)
    return df


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def designation_breakdown(department: str, statuses: tuple[str, ...] = ()) -> pd.DataFrame:
    clause, params, exp = _status_clause(statuses)
    params["dept"] = department
    df = read_df(f"""
        SELECT e.designation, COUNT(*) AS employee_count,
               AVG(e.monthly_salary) AS average_salary,
               SUM(e.monthly_salary) AS total_monthly_salary
        FROM employees e JOIN departments d ON d.id = e.department_id
        WHERE d.name = :dept {clause}
        GROUP BY e.designation ORDER BY total_monthly_salary DESC
    """, params, exp)
    if not df.empty:
        df[["average_salary", "total_monthly_salary"]] = df[["average_salary", "total_monthly_salary"]].astype(float).round(2)
    return df


# --------------------------------------------------------------------------
# History (salary_records)
# --------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def pay_periods() -> list[date]:
    return read_df("SELECT DISTINCT pay_period FROM salary_records ORDER BY 1")["pay_period"].tolist()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def monthly_expenditure() -> pd.DataFrame:
    df = read_df("""
        SELECT sr.pay_period,
               COUNT(DISTINCT sr.employee_id) AS paid_employees,
               SUM(sr.monthly_salary)         AS total_salary,
               AVG(sr.monthly_salary)         AS average_salary
        FROM salary_records sr
        GROUP BY sr.pay_period ORDER BY sr.pay_period
    """)
    if not df.empty:
        df[["total_salary", "average_salary"]] = df[["total_salary", "average_salary"]].astype(float)
        df["pay_period"] = pd.to_datetime(df["pay_period"])
        df["mom_change_pct"] = df["total_salary"].pct_change() * 100
    return df


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def department_trend() -> pd.DataFrame:
    df = read_df("""
        SELECT sr.pay_period, d.name AS department,
               SUM(sr.monthly_salary) AS total_salary,
               COUNT(DISTINCT sr.employee_id) AS paid_employees
        FROM salary_records sr JOIN departments d ON d.id = sr.department_id
        GROUP BY sr.pay_period, d.name ORDER BY sr.pay_period, d.name
    """)
    if not df.empty:
        df["total_salary"] = df["total_salary"].astype(float)
        df["pay_period"] = pd.to_datetime(df["pay_period"])
    return df


# --------------------------------------------------------------------------
# Employee-level data
# --------------------------------------------------------------------------
SORTABLE = {
    "Employee ID": "e.employee_code", "Employee Name": "e.full_name", "Department": "d.name",
    "Designation": "e.designation", "Monthly Salary": "e.monthly_salary",
    "Joining Date": "e.joining_date", "Employment Status": "e.employment_status",
}


def _employee_filters(search: str, departments: Sequence[str], statuses: Sequence[str],
                      salary_range: tuple[float, float] | None, joined: tuple[date, date] | None):
    where, params, exp = ["1=1"], {}, []
    if search:
        where.append("(LOWER(e.full_name) LIKE :q ESCAPE '\\' OR LOWER(e.employee_code) LIKE :q ESCAPE '\\'"
                     " OR LOWER(e.designation) LIKE :q ESCAPE '\\')")
        params["q"] = f"%{escape_like(search.strip().lower())}%"
    if departments:
        where.append("d.name IN :depts"); params["depts"] = list(departments); exp.append("depts")
    if statuses:
        where.append("e.employment_status IN :statuses"); params["statuses"] = list(statuses); exp.append("statuses")
    if salary_range:
        where.append("e.monthly_salary BETWEEN :smin AND :smax")
        params["smin"], params["smax"] = float(salary_range[0]), float(salary_range[1])
    if joined:
        where.append("e.joining_date BETWEEN :jfrom AND :jto")
        params["jfrom"], params["jto"] = joined
    return " AND ".join(where), params, tuple(exp)


_EMP_SELECT = """
    SELECT e.employee_code AS "Employee ID", e.full_name AS "Employee Name", d.name AS "Department",
           e.designation AS "Designation", e.monthly_salary AS "Monthly Salary",
           e.monthly_salary * 12 AS "Annual Salary", e.joining_date AS "Joining Date",
           e.employment_status AS "Employment Status"
    FROM employees e JOIN departments d ON d.id = e.department_id
"""


def employees_page(search="", departments=(), statuses=(), salary_range=None, joined=None,
                   sort_by="Employee ID", descending=False, page=1, page_size=25) -> tuple[pd.DataFrame, int]:
    where, params, exp = _employee_filters(search, departments, statuses, salary_range, joined)
    total = int(read_df(
        f"SELECT COUNT(*) AS n FROM employees e JOIN departments d ON d.id = e.department_id WHERE {where}",
        params, exp)["n"].iloc[0])
    order_col = SORTABLE.get(sort_by, "e.employee_code")  # whitelist: never user text
    direction = "DESC" if descending else "ASC"
    params = {**params, "limit": int(page_size), "offset": int(max(page - 1, 0) * page_size)}
    df = read_df(f"{_EMP_SELECT} WHERE {where} ORDER BY {order_col} {direction}, e.id LIMIT :limit OFFSET :offset",
                 params, exp)
    return _coerce_employee_types(df), total


def employees_export(search="", departments=(), statuses=(), salary_range=None, joined=None,
                     sort_by="Employee ID", descending=False) -> pd.DataFrame:
    where, params, exp = _employee_filters(search, departments, statuses, salary_range, joined)
    order_col = SORTABLE.get(sort_by, "e.employee_code")
    df = read_df(f"{_EMP_SELECT} WHERE {where} ORDER BY {order_col} {'DESC' if descending else 'ASC'}, e.id",
                 params, exp)
    return _coerce_employee_types(df)


def _coerce_employee_types(df: pd.DataFrame) -> pd.DataFrame:
    if not df.empty:
        df["Monthly Salary"] = df["Monthly Salary"].astype(float)
        df["Annual Salary"] = df["Annual Salary"].astype(float)
        df["Joining Date"] = pd.to_datetime(df["Joining Date"]).dt.date
    return df


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def salary_bounds() -> tuple[float, float]:
    df = read_df("SELECT MIN(monthly_salary) AS lo, MAX(monthly_salary) AS hi FROM employees")
    lo, hi = df.iloc[0]["lo"], df.iloc[0]["hi"]
    return (float(lo or 0), float(hi or 0))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def employee_frame(statuses: tuple[str, ...] = ()) -> pd.DataFrame:
    """Employee-level analytical frame used by EDA and charts."""
    clause, params, exp = _status_clause(statuses)
    df = read_df(f"""
        SELECT e.employee_code, e.full_name, d.name AS department, e.designation,
               e.monthly_salary, e.monthly_salary * 12 AS annual_salary,
               e.joining_date, e.employment_status
        FROM employees e JOIN departments d ON d.id = e.department_id
        WHERE 1=1 {clause}
    """, params, exp)
    if df.empty:
        return df
    df["monthly_salary"] = df["monthly_salary"].astype(float)
    df["annual_salary"] = df["annual_salary"].astype(float)
    df["joining_date"] = pd.to_datetime(df["joining_date"])
    df["tenure_years"] = ((pd.Timestamp.today().normalize() - df["joining_date"]).dt.days / 365.25).round(2)
    return df


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def salary_record_frame(period: date) -> pd.DataFrame:
    """Same shape as employee_frame, but salaries (and department) as recorded for one pay period."""
    df = read_df("""
        SELECT e.employee_code, e.full_name, d.name AS department, e.designation,
               sr.monthly_salary, sr.monthly_salary * 12 AS annual_salary,
               e.joining_date, e.employment_status, sr.pay_period
        FROM salary_records sr
        JOIN employees e ON e.id = sr.employee_id
        JOIN departments d ON d.id = sr.department_id
        WHERE sr.pay_period = :p
    """, {"p": period})
    if df.empty:
        return df
    df["monthly_salary"] = df["monthly_salary"].astype(float)
    df["annual_salary"] = df["annual_salary"].astype(float)
    df["joining_date"] = pd.to_datetime(df["joining_date"])
    as_of = pd.Timestamp(period)
    df["tenure_years"] = ((as_of - df["joining_date"]).dt.days / 365.25).clip(lower=0).round(2)
    return df.drop(columns=["pay_period"])


# --------------------------------------------------------------------------
# EDA (pure functions over a DataFrame)
# --------------------------------------------------------------------------
def dataset_overview(df: pd.DataFrame) -> dict:
    return {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "numeric_columns": int(df.select_dtypes(include="number").shape[1]),
        "memory_kb": round(df.memory_usage(deep=True).sum() / 1024, 1),
        "missing_cells": int(df.isna().sum().sum()),
        "duplicate_rows": int(df.duplicated().sum()),
    }


def missing_values(df: pd.DataFrame) -> pd.DataFrame:
    m = df.isna().sum()
    return pd.DataFrame({"column": m.index, "missing": m.values,
                         "missing_pct": (m.values / max(len(df), 1) * 100).round(2)})


def dtype_table(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "column": df.columns,
        "dtype": [str(t) for t in df.dtypes],
        "non_null": df.notna().sum().values,
        "unique_values": [int(df[c].nunique(dropna=True)) for c in df.columns],
        "example": [str(df[c].dropna().iloc[0]) if df[c].notna().any() else "—" for c in df.columns],
    })


def duplicate_report(df: pd.DataFrame) -> dict:
    exact = df[df.duplicated(keep=False)]
    subset = [c for c in ("full_name", "department", "designation", "joining_date") if c in df.columns]
    probable = df[df.duplicated(subset=subset, keep=False)].sort_values(subset) if subset else df.iloc[0:0]
    id_dupes = int(df["employee_code"].duplicated().sum()) if "employee_code" in df.columns else 0
    return {"exact": exact, "probable": probable, "id_duplicates": id_dupes, "probable_keys": subset}


def descriptive_stats(series: pd.Series) -> dict:
    s = series.dropna().astype(float)
    if s.empty:
        return {}
    q1, q2, q3 = s.quantile([0.25, 0.5, 0.75]).tolist()
    mode = s.mode()
    return {
        "count": int(s.count()), "mean": s.mean(), "median": s.median(),
        "mode": float(mode.iloc[0]) if len(mode) else None,
        "mode_count": int((s == mode.iloc[0]).sum()) if len(mode) else 0,
        "std": s.std(ddof=1) if len(s) > 1 else 0.0,
        "variance": s.var(ddof=1) if len(s) > 1 else 0.0,
        "min": s.min(), "q1": q1, "q2": q2, "q3": q3, "max": s.max(),
        "iqr": q3 - q1, "range": s.max() - s.min(),
        "skewness": float(s.skew()) if len(s) > 2 else None,
        "kurtosis": float(s.kurt()) if len(s) > 3 else None,
        "cv_pct": (s.std(ddof=1) / s.mean() * 100) if len(s) > 1 and s.mean() else None,
    }


def iqr_outliers(df: pd.DataFrame, column: str = "monthly_salary", k: float = 1.5) -> tuple[pd.DataFrame, float, float]:
    s = df[column].astype(float)
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - k * iqr, q3 + k * iqr
    out = df[(s < lo) | (s > hi)].copy()
    out["outlier_type"] = np.where(out[column] > hi, "High", "Low")
    return out.sort_values(column, ascending=False), float(lo), float(hi)


def department_stats(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("department")["monthly_salary"]
    out = pd.DataFrame({
        "employees": g.count(), "mean": g.mean(), "median": g.median(), "std": g.std(ddof=1),
        "min": g.min(), "q1": g.quantile(0.25), "q3": g.quantile(0.75), "max": g.max(),
    })
    out["iqr"] = out["q3"] - out["q1"]
    return out.round(2).sort_values("median", ascending=False).reset_index()


def correlation_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Correlation over independent numeric fields (annual salary is 12×monthly, so excluded)."""
    cols = [c for c in ("monthly_salary", "tenure_years") if c in df.columns]
    if len(cols) < 2 or len(df) < 3:
        return pd.DataFrame()
    return df[cols].corr(method="pearson")


def salary_bands(df: pd.DataFrame, bins: int = 6) -> pd.DataFrame:
    s = df["monthly_salary"]
    if s.nunique() < 2:
        return pd.DataFrame({"band": [f"{s.iloc[0]:,.0f}"], "employees": [len(s)]})
    cut = pd.cut(s, bins=bins, include_lowest=True)
    out = cut.value_counts(sort=False).reset_index()
    out.columns = ["interval", "employees"]
    out["band"] = [f"{iv.left:,.0f} – {iv.right:,.0f}" for iv in out["interval"]]
    return out[["band", "employees"]]
