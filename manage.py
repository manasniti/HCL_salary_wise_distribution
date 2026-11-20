"""
Administrative command-line tool.

    python manage.py init-db
    python manage.py create-user --email admin@yourbank.com --name "Asha Rao" --role Admin
    python manage.py reset-password --email admin@yourbank.com
    python manage.py list-users

Passwords are always entered interactively (hidden) or piped via
--password-stdin. They are never accepted as command-line arguments, so
they do not end up in shell history or process listings.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        sys.exit("Passwords do not match.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Salary Intelligence administration")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="Create database tables and constraints")

    cu = sub.add_parser("create-user", help="Create a user account")
    cu.add_argument("--email", required=True)
    cu.add_argument("--name", required=True)
    cu.add_argument("--role", required=True, choices=["Admin", "HR Manager", "Data Analyst"])
    cu.add_argument("--password-stdin", action="store_true")

    rp = sub.add_parser("reset-password", help="Reset a user's password and unlock the account")
    rp.add_argument("--email", required=True)
    rp.add_argument("--password-stdin", action="store_true")

    sub.add_parser("list-users", help="List user accounts")

    args = parser.parse_args()

    from database import get_session, init_db
    import auth
    from models import User

    if args.cmd == "init-db":
        init_db()
        print("Database schema is ready.")
    elif args.cmd == "create-user":
        init_db()
        try:
            uid = auth.create_user(args.email, args.name, args.role, _read_password(args.password_stdin))
        except auth.UserError as exc:
            sys.exit(f"Error: {exc}")
        print(f"Created user #{uid} ({args.role}).")
    elif args.cmd == "reset-password":
        with get_session() as s:
            user = s.execute(select(User).where(User.email == args.email.strip().lower())).scalar_one_or_none()
        if user is None:
            sys.exit("No such user.")
        try:
            auth.set_password(user.id, _read_password(args.password_stdin), actor=None)
        except auth.UserError as exc:
            sys.exit(f"Error: {exc}")
        print("Password reset; account unlocked.")
    elif args.cmd == "list-users":
        with get_session() as s:
            for u in s.execute(select(User).order_by(User.id)).scalars():
                state = "active" if u.is_active else "inactive"
                print(f"{u.id:>4}  {u.email:<40} {u.role:<14} {state}")


if __name__ == "__main__":
    main()
