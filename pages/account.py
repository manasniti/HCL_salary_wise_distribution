"""Signed-in user's own account: profile and password change."""
from __future__ import annotations

import streamlit as st

import auth
from database import read_df
from utils.security import PASSWORD_MIN_LENGTH, ROLE_PERMISSIONS


def render() -> None:
    user = auth.current_user()
    info = read_df("SELECT last_login_at, password_changed_at, created_at FROM users WHERE id = :id",
                   {"id": user["user_id"]}).iloc[0]
    c1, c2 = st.columns([1, 1])
    with c1, st.container(border=True):
        st.markdown(f"### {user['full_name']}")
        st.markdown(f"`{user['email']}` · **{user['role']}**")
        st.caption(f"Account created {info.created_at:%d %b %Y} · "
                   f"password last changed {info.password_changed_at:%d %b %Y}")
        st.markdown("**What your role can do**")
        st.markdown("\n".join(f"- {p.replace('_', ' ').capitalize()}" for p in sorted(ROLE_PERMISSIONS[user["role"]])))
    with c2, st.form("change_pw", clear_on_submit=True):
        st.markdown("### Change password")
        cur = st.text_input("Current password", type="password", max_chars=256)
        new = st.text_input("New password", type="password", max_chars=72,
                            help=f"≥{PASSWORD_MIN_LENGTH} characters with upper, lower, digit and symbol.")
        new2 = st.text_input("Confirm new password", type="password", max_chars=72)
        if st.form_submit_button("Update password", type="primary"):
            if new != new2:
                st.error("New passwords do not match.")
            else:
                try:
                    auth.set_password(user["user_id"], new, actor=user, current_password=cur)
                    st.success("Password updated.")
                except auth.UserError as exc:
                    st.error(str(exc))
