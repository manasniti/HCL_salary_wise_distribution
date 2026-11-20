"""User management (Admin only)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import auth
from database import read_df
from models import ROLES
from utils import ui
from utils.security import MANAGE_USERS, PASSWORD_MIN_LENGTH, ROLE_PERMISSIONS


def render() -> None:
    actor = auth.require_permission(MANAGE_USERS)

    users = read_df("""
        SELECT id, full_name, email, role, is_active, failed_login_attempts, locked_until, last_login_at, created_at
        FROM users ORDER BY id
    """)
    now = pd.Timestamp.now(tz="UTC")
    users["locked"] = users["locked_until"].apply(lambda t: bool(t is not None and pd.notna(t) and pd.Timestamp(t) > now))

    ui.kpi_cards([
        ("◉", "Accounts", f"{len(users):,}", f"{int(users.is_active.sum())} active"),
        *[("◆", r, f"{int((users.role == r).sum())}", "accounts") for r in ROLES],
        ("⊘", "Locked", f"{int(users.locked.sum())}", "temporarily"),
    ])

    st.dataframe(users.drop(columns=["locked_until"]), hide_index=True, width="stretch", column_config={
        "id": "ID", "full_name": "Name", "email": "Email", "role": "Role",
        "is_active": st.column_config.CheckboxColumn("Active"),
        "failed_login_attempts": "Failed attempts",
        "last_login_at": st.column_config.DatetimeColumn("Last login", format="DD MMM YYYY HH:mm"),
        "created_at": st.column_config.DatetimeColumn("Created", format="DD MMM YYYY"),
        "locked": st.column_config.CheckboxColumn("Locked"),
    })

    tab_new, tab_edit, tab_roles = st.tabs(["Create user", "Edit user", "Role permissions"])

    with tab_new:
        with st.form("new_user", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Full name", max_chars=120)
            email = c2.text_input("Work email", max_chars=254)
            role = c1.selectbox("Role", ROLES, index=2)
            pw = c2.text_input("Initial password", type="password", max_chars=72,
                               help=f"≥{PASSWORD_MIN_LENGTH} chars with upper, lower, digit and symbol. "
                                    "Share it through a secure channel; the user should change it at first sign-in.")
            pw2 = c2.text_input("Confirm password", type="password", max_chars=72)
            if st.form_submit_button("Create user", type="primary"):
                if pw != pw2:
                    st.error("Passwords do not match.")
                else:
                    try:
                        auth.create_user(email, name, role, pw, actor=actor)
                        st.success(f"Created {email.strip().lower()} as {role}.")
                        st.rerun()
                    except auth.UserError as exc:
                        st.error(str(exc))

    with tab_edit:
        if users.empty:
            st.info("No users.")
        else:
            uid = st.selectbox("User", users["id"].tolist(),
                               format_func=lambda i: f"{users.set_index('id').loc[i, 'email']} "
                                                     f"({users.set_index('id').loc[i, 'role']})")
            row = users.set_index("id").loc[uid]
            c1, c2 = st.columns(2)
            with c1, st.form("edit_user"):
                new_role = st.selectbox("Role", ROLES, index=ROLES.index(row.role))
                active = st.toggle("Account active", value=bool(row.is_active))
                unlock = st.checkbox("Clear lockout and failed-attempt counter", value=False, disabled=not row.locked
                                     and row.failed_login_attempts == 0)
                if st.form_submit_button("Save changes", type="primary"):
                    try:
                        auth.update_user(int(uid), actor=actor, role=new_role, is_active=active, unlock=unlock)
                        st.success("Saved.")
                        st.rerun()
                    except auth.UserError as exc:
                        st.error(str(exc))
            with c2, st.form("reset_pw", clear_on_submit=True):
                st.markdown("**Reset password**")
                p1 = st.text_input("New password", type="password", max_chars=72)
                p2 = st.text_input("Confirm", type="password", max_chars=72)
                if st.form_submit_button("Reset password"):
                    if p1 != p2:
                        st.error("Passwords do not match.")
                    else:
                        try:
                            auth.set_password(int(uid), p1, actor=actor)
                            st.success("Password reset and account unlocked.")
                        except auth.UserError as exc:
                            st.error(str(exc))

    with tab_roles:
        perms = sorted({p for s in ROLE_PERMISSIONS.values() for p in s})
        matrix = pd.DataFrame({r: [p in ROLE_PERMISSIONS[r] for p in perms] for r in ROLES},
                              index=[p.replace("_", " ").title() for p in perms])
        st.dataframe(matrix, width="stretch",
                     column_config={r: st.column_config.CheckboxColumn(r) for r in ROLES})
