"""Admin data import: upload → validate → preview → confirm → transactional insert."""
from __future__ import annotations

import hashlib
from datetime import date

import streamlit as st

import auth
from config import settings
from utils import analytics as an
from utils import data_cleaning as dc
from utils import ui
from utils.security import IMPORT_DATA, log_audit


def _period_picker() -> date:
    today = date.today()
    c1, c2 = st.columns(2)
    years = list(range(today.year, 1999, -1))
    y = c1.selectbox("Pay period year", years, key="imp_year")
    months = list(range(1, (today.month if y == today.year else 12) + 1))
    m = c2.selectbox("Pay period month", months, index=len(months) - 1,
                     format_func=lambda i: date(2000, i, 1).strftime("%B"), key="imp_month")
    return date(y, m, 1)


def render() -> None:
    user = auth.require_permission(IMPORT_DATA)

    with st.container(border=True):
        ui.section("1 · Prepare your file")
        st.markdown(
            "Upload **verified** payroll data exported from your HR or payroll system. Required columns: "
            + ", ".join(f"`{c}`" for c in dc.REQUIRED_COLUMNS)
            + ". Optional: `pay_period` (YYYY-MM) to load several months of history at once. "
              "Common alternative headers (e.g. `emp_id`, `salary`, `doj`, `status`) are recognised."
        )
        st.download_button("Download blank template (CSV)", dc.template_csv(), "salary_import_template.csv",
                           "text/csv", icon=":material/table_view:", on_click="ignore")
        st.caption(f"Accepted: .csv (UTF-8 / Windows-1252) or .xlsx · max {settings.max_upload_mb} MB · "
                   f"max {settings.max_upload_rows:,} rows · macro-enabled workbooks are rejected.")

    with st.container(border=True):
        ui.section("2 · Upload & validate")
        upload = st.file_uploader("Dataset", type=["csv", "xlsx"], accept_multiple_files=False, key="imp_file")
        dayfirst = st.toggle("Dates are day-first (DD/MM/YYYY)", value=True, key="imp_dayfirst")
        if upload is None:
            st.session_state.pop("_imp", None)
            return

        data = upload.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        try:
            raw = dc.read_upload(upload.name, data)
        except ValueError as exc:
            log_audit("DATA_UPLOAD_REJECTED", "FAILURE", {"file": upload.name[:120], "reason": str(exc)[:200]})
            st.error(str(exc))
            return
        except Exception:
            log_audit("DATA_UPLOAD_REJECTED", "FAILURE", {"file": upload.name[:120], "reason": "unreadable"})
            st.error("The file could not be read. Check that it is a valid CSV or Excel workbook.")
            return

        default_period = None
        if "pay_period" not in raw.columns:
            st.info("This file has no `pay_period` column. Choose the month these salaries were paid for.")
            default_period = _period_picker()

        result = dc.validate_dataframe(raw, default_pay_period=default_period, dayfirst=dayfirst)
        sig = (digest, default_period, dayfirst)
        if st.session_state.get("_imp_logged") != sig:
            log_audit("DATA_VALIDATION", "INFO", {"file": upload.name[:120], "sha256": digest[:16],
                                                  "rows": result.total_rows, "valid": len(result.valid),
                                                  "error_rows": result.error_count})
            st.session_state["_imp_logged"] = sig

        m1, m2, m3 = st.columns(3)
        m1.metric("Rows in file", f"{result.total_rows:,}")
        m2.metric("Valid rows", f"{len(result.valid):,}")
        m3.metric("Rows with errors", f"{result.error_count:,}")
        for w in result.warnings:
            st.caption("ℹ️ " + w)

        if not result.errors.empty:
            st.warning("Some rows failed validation. Fix them at source and re-upload, or import only the valid rows.")
            st.dataframe(result.errors, hide_index=True, width="stretch", height=260)
            st.download_button("Download error report", result.errors.to_csv(index=False).encode("utf-8-sig"),
                               "validation_errors.csv", "text/csv", on_click="ignore")

    if result.valid.empty:
        st.error("No valid rows to import.")
        return

    with st.container(border=True):
        ui.section("3 · Review & confirm")
        impact = dc.preview_against_database(result.valid)
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("Salary records", f"{impact['rows']:,}")
        i2.metric("New employees", f"{impact['new_employees']:,}")
        i3.metric("Existing employees", f"{impact['existing_employees']:,}")
        i4.metric("Pay periods", len(impact["pay_periods"]))
        if impact["new_departments"]:
            st.caption("New departments: " + ", ".join(impact["new_departments"]))
        st.caption("Pay periods: " + ", ".join(impact["pay_periods"]))
        if impact["name_changes"]:
            st.warning(f"{impact['name_changes']} existing employee(s) have a different name in this file; "
                       "their records will be updated.")
        st.caption("Existing salary records for the same employee and pay period are replaced with the values "
                   "in this file. Employee details follow the most recent pay period.")
        st.dataframe(result.valid.drop(columns=["row"]).head(50), hide_index=True, width="stretch")

        partial = not result.errors.empty
        confirm = st.checkbox(
            ("I confirm the valid rows are verified payroll data and the invalid rows should be skipped."
             if partial else "I confirm this dataset contains verified payroll data."),
            key="imp_confirm")
        if st.button("Import into database", type="primary", disabled=not confirm, icon=":material/database:"):
            try:
                summary = dc.import_dataset(result.valid, actor=user, source_name=upload.name)
            except Exception as exc:
                log_audit("DATA_IMPORT", "FAILURE", {"file": upload.name[:120], "error": type(exc).__name__})
                st.error("Import failed and was rolled back. No data was changed.")
                return
            an.clear_cache()
            st.success(
                f"Imported {summary['rows']:,} salary records · {summary['employees_created']:,} new employees · "
                f"{summary['employees_updated']:,} updated · {summary['departments_created']:,} new departments."
            )
            st.caption(f"Batch ID: {summary['batch_id']}")
            if "dashboard" in ui.PAGES:
                st.page_link(ui.PAGES["dashboard"], label="Open dashboard", icon=":material/space_dashboard:")
