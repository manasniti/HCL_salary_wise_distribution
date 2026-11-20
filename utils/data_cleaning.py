"""
Dataset parsing, validation and transactional import.

Uploaded files are validated row by row before anything touches the
database. Only rows that pass every rule are eligible for insertion, and
the whole import runs inside one transaction.
"""
from __future__ import annotations

import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from config import settings
from database import get_session
from models import EMPLOYMENT_STATUSES, Department, Employee, SalaryRecord
from utils import security as sec

REQUIRED_COLUMNS = [
    "employee_id", "employee_name", "department", "designation",
    "monthly_salary", "joining_date", "employment_status",
]
OPTIONAL_COLUMNS = ["pay_period"]

COLUMN_ALIASES = {
    "employee_id": {"employee_id", "emp_id", "employee_code", "emp_code", "employee_no", "employee_number", "staff_id"},
    "employee_name": {"employee_name", "name", "full_name", "emp_name", "staff_name"},
    "department": {"department", "dept", "department_name", "dept_name"},
    "designation": {"designation", "title", "job_title", "position", "grade_title"},
    "monthly_salary": {"monthly_salary", "salary", "monthly_pay", "monthly_gross", "gross_monthly_salary"},
    "joining_date": {"joining_date", "date_of_joining", "doj", "join_date", "hire_date", "joined_on"},
    "employment_status": {"employment_status", "status", "emp_status", "employee_status"},
    "pay_period": {"pay_period", "salary_month", "payroll_month", "period", "month"},
}

STATUS_ALIASES = {s.lower(): s for s in EMPLOYMENT_STATUSES}
STATUS_ALIASES.update({"on-leave": "On Leave", "leave": "On Leave", "on_leave": "On Leave",
                       "terminate": "Terminated", "resign": "Resigned", "retire": "Retired"})

MAX_SALARY = 100_000_000  # sanity ceiling per month in base currency
MIN_JOIN_DATE = date(1950, 1, 1)

TEMPLATE_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    errors: pd.DataFrame
    total_rows: int
    has_pay_period_column: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return int(self.errors["row"].nunique()) if not self.errors.empty else 0


def template_csv() -> bytes:
    return (",".join(TEMPLATE_COLUMNS) + "\n").encode("utf-8")


def _normalise_header(col: str) -> str:
    c = re.sub(r"[^0-9a-zA-Z]+", "_", str(col).strip().lower()).strip("_")
    for canonical, aliases in COLUMN_ALIASES.items():
        if c in aliases:
            return canonical
    return c


def read_upload(filename: str, data: bytes) -> pd.DataFrame:
    """Parse a CSV/XLSX upload into a string-typed DataFrame with canonical headers."""
    ok, msg = sec.validate_upload_bytes(filename, data)
    if not ok:
        raise ValueError(msg)
    if filename.lower().endswith(".xlsx"):
        df = pd.read_excel(io.BytesIO(data), engine="openpyxl", dtype=object, sheet_name=0)
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_values=[""],
                         skip_blank_lines=True)
    if len(df) > settings.max_upload_rows:
        raise ValueError(f"File has {len(df):,} rows; the limit is {settings.max_upload_rows:,}.")
    df.columns = [_normalise_header(c) for c in df.columns]
    dupes = df.columns[df.columns.duplicated()].tolist()
    if dupes:
        raise ValueError(f"Columns map to the same field more than once: {', '.join(sorted(set(dupes)))}.")
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing)
                         + ". Download the template to see the expected headers.")
    keep = [c for c in TEMPLATE_COLUMNS if c in df.columns]
    df = df[keep].dropna(how="all").reset_index(drop=True)
    if df.empty:
        raise ValueError("The file contains headers but no data rows.")
    return df


def _parse_salary(v) -> float | None:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    s = re.sub(r"[\s,₹$€£]|INR|USD|Rs\.?", "", str(v), flags=re.IGNORECASE)
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        return None
    return float(s)


def _parse_date(v, dayfirst: bool) -> date | None:
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
        return None
    if isinstance(v, (pd.Timestamp,)) or hasattr(v, "year"):
        try:
            return pd.Timestamp(v).date()
        except Exception:
            return None
    ts = pd.to_datetime(str(v).strip(), errors="coerce", dayfirst=dayfirst)
    return None if pd.isna(ts) else ts.date()


def _parse_period(v, dayfirst: bool) -> date | None:
    if v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == "":
        return None
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return date(y, mo, 1) if 1 <= mo <= 12 else None
    d = _parse_date(v, dayfirst)
    return date(d.year, d.month, 1) if d else None


def validate_dataframe(df: pd.DataFrame, *, default_pay_period: date | None, dayfirst: bool = True) -> ValidationResult:
    today = date.today()
    current_period = date(today.year, today.month, 1)
    has_period = "pay_period" in df.columns
    errors: list[dict] = []
    rows: list[dict] = []

    for idx, rec in df.iterrows():
        row_no = int(idx) + 2  # header is row 1 in the source file
        problems: list[tuple[str, str]] = []

        raw_code = rec.get("employee_id")
        if isinstance(raw_code, (float, np.floating)) and float(raw_code).is_integer():
            raw_code = str(int(raw_code))  # Excel stores 1001 as 1001.0
        code = sec.clean_text(raw_code, 40)
        if not code:
            problems.append(("employee_id", "Required."))
        elif not sec.EMPLOYEE_CODE_RE.match(code):
            problems.append(("employee_id", "Use 1–32 letters, digits, - _ /."))

        name = sec.clean_text(rec.get("employee_name"), 130)
        if not name:
            problems.append(("employee_name", "Required."))
        elif len(name) > 120 or not sec.PERSON_NAME_RE.match(name):
            problems.append(("employee_name", "Letters, spaces and . ' - only (max 120)."))

        dept = sec.clean_text(rec.get("department"), 110)
        if not dept:
            problems.append(("department", "Required."))
        elif len(dept) > 100 or not sec.FREE_TEXT_RE.match(dept):
            problems.append(("department", "Contains unsupported characters (max 100)."))

        desig = sec.clean_text(rec.get("designation"), 130)
        if not desig:
            problems.append(("designation", "Required."))
        elif len(desig) > 120 or not sec.FREE_TEXT_RE.match(desig):
            problems.append(("designation", "Contains unsupported characters (max 120)."))

        salary = _parse_salary(rec.get("monthly_salary"))
        if salary is None:
            problems.append(("monthly_salary", "Must be a positive number with up to 2 decimals."))
        elif salary <= 0:
            problems.append(("monthly_salary", "Must be greater than zero."))
        elif salary > MAX_SALARY:
            problems.append(("monthly_salary", f"Exceeds the sanity limit of {MAX_SALARY:,}."))

        jd = _parse_date(rec.get("joining_date"), dayfirst)
        if jd is None:
            problems.append(("joining_date", "Not a recognisable date."))
        elif jd > today:
            problems.append(("joining_date", "Cannot be in the future."))
        elif jd < MIN_JOIN_DATE:
            problems.append(("joining_date", f"Earlier than {MIN_JOIN_DATE.isoformat()}."))

        status_raw = sec.clean_text(rec.get("employment_status"), 30).lower()
        status = STATUS_ALIASES.get(status_raw)
        if status is None:
            problems.append(("employment_status", "Must be one of: " + ", ".join(EMPLOYMENT_STATUSES) + "."))

        period = _parse_period(rec.get("pay_period"), dayfirst) if has_period else default_pay_period
        if period is None:
            problems.append(("pay_period", "Required (YYYY-MM)." if has_period else "Choose a pay period."))
        elif period > current_period:
            problems.append(("pay_period", "Cannot be a future month."))
        elif jd is not None and period < date(jd.year, jd.month, 1):
            problems.append(("pay_period", "Is before the employee's joining month."))

        if problems:
            for col, msg in problems:
                errors.append({"row": row_no, "column": col, "value": str(rec.get(col, ""))[:60], "error": msg})
        else:
            rows.append({
                "row": row_no, "employee_id": code, "employee_name": name, "department": dept,
                "designation": desig, "monthly_salary": round(salary, 2), "joining_date": jd,
                "employment_status": status, "pay_period": period,
            })

    valid = pd.DataFrame(rows, columns=["row"] + TEMPLATE_COLUMNS)
    warnings: list[str] = []

    if not valid.empty:
        # Duplicate (employee, period) rows inside the file.
        dup_mask = valid.duplicated(subset=["employee_id", "pay_period"], keep="first")
        for _, r in valid[dup_mask].iterrows():
            errors.append({"row": r["row"], "column": "employee_id", "value": r["employee_id"],
                           "error": f"Duplicate row for pay period {r['pay_period']:%Y-%m}."})
        valid = valid[~dup_mask]

        # Same ID with conflicting identity attributes inside the file.
        conflicts = valid.groupby("employee_id").agg(
            names=("employee_name", lambda s: s.str.lower().nunique()),
            joins=("joining_date", "nunique"),
        )
        bad_ids = set(conflicts[(conflicts.names > 1) | (conflicts.joins > 1)].index)
        if bad_ids:
            for _, r in valid[valid.employee_id.isin(bad_ids)].iterrows():
                errors.append({"row": r["row"], "column": "employee_id", "value": r["employee_id"],
                               "error": "Same employee_id has different names or joining dates in this file."})
            valid = valid[~valid.employee_id.isin(bad_ids)]

    err_df = pd.DataFrame(errors, columns=["row", "column", "value", "error"]).sort_values(["row", "column"])
    if not has_period:
        warnings.append("No pay_period column found: every row is recorded for the pay period you selected.")
    return ValidationResult(valid.reset_index(drop=True), err_df.reset_index(drop=True), len(df), has_period, warnings)


def preview_against_database(valid: pd.DataFrame) -> dict:
    """Describe what an import would change, without changing anything."""
    codes = valid["employee_id"].unique().tolist()
    with get_session() as s:
        existing = dict(s.execute(
            select(Employee.employee_code, Employee.full_name).where(Employee.employee_code.in_(codes))
        ).all()) if codes else {}
        dept_names = {n.lower() for (n,) in s.execute(select(Department.name)).all()}
    latest = valid.sort_values("pay_period").groupby("employee_id").tail(1)
    renamed = sum(
        1 for _, r in latest.iterrows()
        if r.employee_id in existing and existing[r.employee_id].lower() != r.employee_name.lower()
    )
    return {
        "rows": len(valid),
        "new_employees": len(set(codes) - set(existing)),
        "existing_employees": len(set(codes) & set(existing)),
        "name_changes": renamed,
        "new_departments": sorted({d for d in valid["department"].unique() if d.lower() not in dept_names}),
        "pay_periods": sorted({p.strftime("%Y-%m") for p in valid["pay_period"]}),
    }


def import_dataset(valid: pd.DataFrame, *, actor: dict, source_name: str) -> dict:
    """Insert/update validated rows in a single transaction."""
    if valid.empty:
        raise ValueError("Nothing to import.")
    batch_id = str(uuid.uuid4())
    with get_session() as s:
        # 1. Departments (case-insensitive match).
        dept_map = {n.lower(): i for i, n in s.execute(select(Department.id, Department.name)).all()}
        new_depts = 0
        for name in valid["department"].unique():
            if name.lower() not in dept_map:
                d = Department(name=name)
                s.add(d)
                s.flush()
                dept_map[name.lower()] = d.id
                new_depts += 1

        # 2. Employees. Master attributes follow the most recent pay period.
        codes = valid["employee_id"].unique().tolist()
        existing = {e.employee_code: e for e in s.execute(
            select(Employee).where(Employee.employee_code.in_(codes)).with_for_update()
        ).scalars()}
        latest_db = dict(s.execute(
            select(Employee.employee_code, func.max(SalaryRecord.pay_period))
            .join(SalaryRecord, SalaryRecord.employee_id == Employee.id)
            .where(Employee.employee_code.in_(codes))
            .group_by(Employee.employee_code)
        ).all()) if codes else {}

        latest_rows = valid.sort_values("pay_period").groupby("employee_id").tail(1)
        created = updated = 0
        for _, r in latest_rows.iterrows():
            emp = existing.get(r.employee_id)
            attrs = dict(
                full_name=r.employee_name, department_id=dept_map[r.department.lower()],
                designation=r.designation, monthly_salary=r.monthly_salary,
                joining_date=r.joining_date, employment_status=r.employment_status,
            )
            if emp is None:
                emp = Employee(employee_code=r.employee_id, **attrs)
                s.add(emp)
                existing[r.employee_id] = emp
                created += 1
            elif latest_db.get(r.employee_id) is None or r.pay_period >= latest_db[r.employee_id]:
                for k, v in attrs.items():
                    setattr(emp, k, v)
                updated += 1
        s.flush()

        # 3. Salary records: upsert on (employee_id, pay_period).
        records = [{
            "employee_id": existing[r.employee_id].id,
            "department_id": dept_map[r.department.lower()],
            "pay_period": r.pay_period,
            "monthly_salary": r.monthly_salary,
            "import_batch_id": batch_id,
            "uploaded_by": actor["user_id"],
        } for r in valid.itertuples(index=False)]
        for i in range(0, len(records), 1000):
            stmt = pg_insert(SalaryRecord).values(records[i:i + 1000])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_salary_employee_period",
                set_={
                    "monthly_salary": stmt.excluded.monthly_salary,
                    "department_id": stmt.excluded.department_id,
                    "import_batch_id": stmt.excluded.import_batch_id,
                    "uploaded_by": stmt.excluded.uploaded_by,
                },
            )
            s.execute(stmt)

        summary = {
            "batch_id": batch_id, "source": source_name[:120], "rows": len(records),
            "employees_created": created, "employees_updated": updated, "departments_created": new_depts,
        }
        sec.log_audit("DATA_IMPORT", "SUCCESS", summary,
                      user_id=actor["user_id"], user_email=actor["email"], session=s)
    return summary
