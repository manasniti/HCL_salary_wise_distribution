"""
SQLAlchemy ORM models.

Tables: users, departments, employees, salary_records, audit_logs.
Constraints enforce integrity at the database level (not only in the UI).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, Numeric, String, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# JSONB on PostgreSQL, generic JSON elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")

ROLE_ADMIN = "Admin"
ROLE_HR = "HR Manager"
ROLE_ANALYST = "Data Analyst"
ROLES = (ROLE_ADMIN, ROLE_HR, ROLE_ANALYST)

EMPLOYMENT_STATUSES = ("Active", "On Leave", "Probation", "Resigned", "Terminated", "Retired")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in_list("role", ROLES), name="ck_users_role"),
        CheckConstraint("failed_login_attempts >= 0", name="ck_users_failed_nonneg"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_departments_name_nonempty"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    employees: Mapped[list["Employee"]] = relationship(back_populates="department")


# Case-insensitive uniqueness: "Finance" and "finance" are the same department.
Index("uq_departments_name_lower", func.lower(Department.name), unique=True)


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint("monthly_salary > 0", name="ck_employees_salary_positive"),
        CheckConstraint(_in_list("employment_status", EMPLOYMENT_STATUSES), name="ck_employees_status"),
        CheckConstraint("length(trim(employee_code)) > 0", name="ck_employees_code_nonempty"),
        Index("ix_employees_department_status", "department_id", "employment_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    designation: Mapped[str] = mapped_column(String(120), nullable=False)
    monthly_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    joining_date: Mapped[date] = mapped_column(Date, nullable=False)
    employment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="Active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    department: Mapped[Department] = relationship(back_populates="employees")
    salary_records: Mapped[list["SalaryRecord"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan"
    )


class SalaryRecord(Base):
    """One verified monthly salary figure for one employee in one pay period."""
    __tablename__ = "salary_records"
    __table_args__ = (
        UniqueConstraint("employee_id", "pay_period", name="uq_salary_employee_period"),
        CheckConstraint("monthly_salary > 0", name="ck_salary_records_positive"),
        CheckConstraint("EXTRACT(DAY FROM pay_period) = 1", name="ck_salary_records_period_first_day"),
        Index("ix_salary_records_period", "pay_period"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    pay_period: Mapped[date] = mapped_column(Date, nullable=False)  # always first day of month
    monthly_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    import_batch_id: Mapped[str | None] = mapped_column(String(36))
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="salary_records")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("status IN ('SUCCESS', 'FAILURE', 'DENIED', 'INFO')", name="ck_audit_status"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    user_email: Mapped[str | None] = mapped_column(String(254))
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="SUCCESS")
    details: Mapped[dict | None] = mapped_column(JSONType)
    session_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User | None] = relationship(back_populates="audit_logs")


__all__ = [
    "Base", "User", "Department", "Employee", "SalaryRecord", "AuditLog",
    "ROLES", "ROLE_ADMIN", "ROLE_HR", "ROLE_ANALYST", "EMPLOYMENT_STATUSES", "utcnow",
]
