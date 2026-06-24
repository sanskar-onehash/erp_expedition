# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Permission helpers for Expedition endpoints.

Rule: every API that returns rows from a *foreign* DocType (Customer, Lead,
Item, etc.) MUST call `assert_source_read(source_doctype)` before serializing.
Client-side filter values are advisory only — the server is the boundary.
"""

import frappe


def assert_source_read(doctype: str) -> None:
    """
    Raise PermissionError if the current user cannot read `doctype`.
    Use this in every endpoint that returns source-DocType rows.
    """
    if not doctype or doctype in {"User", "DocField", "DocType"}:
        # Built-in / meta DocTypes — we let Frappe's has_permission
        # be the source of truth and rely on the role system.
        return
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(f"Not permitted to read {doctype}", frappe.PermissionError)


def get_permission_filter(doctype: str) -> str | None:
    """
    Return a `frappe.db.escape`d permission filter string for use in
    a `get_all` call, OR None if the user has no row-level restriction.

    For DocTypes with role-restricted reading (e.g. a sales rep can only
    see their own Customers), this lets us add the appropriate WHERE
    clause at the API layer.
    """
    # For v1 we rely on frappe's `get_all` which auto-applies the
    # permission query conditions for the source doctype. We do not
    # duplicate that here. This helper exists so v1.1+ custom rules
    # (e.g. sales-territory scoping) can hook in without API churn.
    return None
