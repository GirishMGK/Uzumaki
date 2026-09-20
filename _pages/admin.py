"""Hub page: Users & Access (admin only).

Manages the accounts and roles that gate the whole app (see auth.py +
Home.py's login screen) -- create/edit/delete users, create/edit/delete
roles, and set which tools each role can open. Registered in Home.py's
nav only when the logged-in user is an admin, so a non-admin never even
sees this page exists, let alone reaches it by URL.
"""
from __future__ import annotations

import os
import sys

import streamlit as st

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import auth
from _pages.theme import footer

# Defence in depth: Home.py already only registers this page for admins,
# but guard here too in case a session's auth_user somehow lost that flag
# mid-session (e.g. an admin demoted themselves in another tab).
if not st.session_state.get("auth_user", {}).get("is_admin"):
    st.error("You don't have access to this page.")
    st.stop()

st.subheader("🛡️ Users & Access")
st.caption("Manage accounts, roles, and which tools each role can open.")

tab_users, tab_roles = st.tabs(["Users", "Roles & Tool Access"])

with tab_users:
    st.markdown("#### Existing users")
    users = auth.list_users()
    for u in users:
        c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 1, 1])
        c1.write(f"**{u['username']}**")
        c2.write(u["full_name"] or "—")
        c3.write("Administrator (full access)" if u["is_admin"] else u["role_name"])
        if c4.button("Edit", key=f"edit_{u['username']}"):
            st.session_state.admin_editing_user = u["username"]
        if c5.button("Delete", key=f"del_{u['username']}"):
            try:
                auth.delete_user(u["username"])
                st.success(f"Deleted {u['username']}.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    editing = st.session_state.get("admin_editing_user")
    role_names = [r["name"] for r in auth.list_roles()]

    st.markdown("---")
    st.markdown(f"#### {'Edit user: ' + editing if editing else 'Add a new user'}")
    existing = auth.get_user(editing) if editing else None

    with st.form("user_form", clear_on_submit=not editing):
        username = st.text_input(
            "Username", value=existing["username"] if existing else "", disabled=bool(existing),
        )
        full_name = st.text_input("Full name", value=existing["full_name"] if existing else "")
        password = st.text_input(
            "Password" + (" (leave blank to keep current)" if existing else ""), type="password",
        )
        is_admin = st.checkbox("Administrator (full access to every tool)",
                                value=existing["is_admin"] if existing else False)
        role_name = st.selectbox(
            "Role (only matters if not an admin)", role_names or ["(create a role first)"],
            index=(role_names.index(existing["role_name"]) if existing and existing["role_name"] in role_names else 0),
            disabled=not role_names,
        )
        c1, c2 = st.columns(2)
        submitted = c1.form_submit_button("Save", type="primary", use_container_width=True)
        cancelled = c2.form_submit_button("Cancel", use_container_width=True) if existing else False

        if cancelled:
            st.session_state.admin_editing_user = None
            st.rerun()

        if submitted:
            try:
                if existing:
                    auth.update_user(
                        username, password=password or None, full_name=full_name,
                        role_name=role_name if role_names else existing["role_name"],
                        is_admin=is_admin,
                    )
                    st.session_state.admin_editing_user = None
                    st.success(f"Updated {username}.")
                else:
                    if not role_names and not is_admin:
                        st.error("Create a role first, or make this user an Administrator.")
                        st.stop()
                    auth.create_user(username.strip(), password, full_name, role_name, is_admin)
                    st.success(f"Created {username}.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't save: {e}")

with tab_roles:
    st.markdown("#### Existing roles")
    roles = auth.list_roles()
    for r in roles:
        with st.expander(f"**{r['name']}** — {len(r['allowed_tools'])} tool(s)"):
            new_tools = st.multiselect(
                "Allowed tools", auth.TOOL_KEYS, default=r["allowed_tools"], key=f"role_tools_{r['name']}",
            )
            c1, c2 = st.columns(2)
            if c1.button("Save changes", key=f"save_role_{r['name']}", use_container_width=True):
                auth.update_role(r["name"], new_tools)
                st.success(f"Updated {r['name']}.")
                st.rerun()
            if c2.button("Delete role", key=f"del_role_{r['name']}", use_container_width=True):
                try:
                    auth.delete_role(r["name"])
                    st.success(f"Deleted {r['name']}.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    st.markdown("---")
    st.markdown("#### Add a new role")
    with st.form("role_form", clear_on_submit=True):
        new_role_name = st.text_input("Role name", placeholder="e.g. Junior Auditor")
        new_role_tools = st.multiselect("Allowed tools", auth.TOOL_KEYS)
        if st.form_submit_button("Create role", type="primary"):
            try:
                auth.create_role(new_role_name.strip(), new_role_tools)
                st.success(f"Created {new_role_name}.")
                st.rerun()
            except Exception as e:
                st.error(f"Couldn't create role: {e}")

footer()
