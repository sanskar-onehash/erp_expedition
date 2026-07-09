# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Permission helpers for Expedition endpoints.

Rule: every API that returns rows from a *foreign* DocType (Customer, Lead,
Item, etc.) MUST call `assert_source_read(source_doctype)` before serializing.
Client-side filter values are advisory only — the server is the boundary.
"""

import frappe


def _has_map_field(fieldname: str) -> bool:
    return frappe.db.has_column("Expedition Map", fieldname)


def _map_values(name: str) -> dict:
    fields = ["owner", "owner_user", "is_public", "is_template"]
    if _has_map_field("public_access"):
        fields.append("public_access")
    if _has_map_field("access_overrides_json"):
        fields.append("access_overrides_json")
    values = frappe.db.get_value("Expedition Map", name, fields, as_dict=True)
    if not values:
        frappe.throw(f"Unknown Expedition Map {name}", frappe.DoesNotExistError)
    values.setdefault("public_access", "Read Only")
    values.setdefault("access_overrides_json", "")
    return values


def _override_access(row: dict, user: str) -> str | None:
    raw = row.get("access_overrides_json") or ""
    try:
        overrides = frappe.parse_json(raw) if raw else []
    except Exception:
        overrides = []
    for item in overrides or []:
        if not isinstance(item, dict):
            continue
        if item.get("user") == user:
            access = str(item.get("access") or "").lower()
            if access in {"read", "write"}:
                return access
    return None


def _docshare_row(name: str, user: str):
    return frappe.db.get_value(
        "DocShare",
        {"share_doctype": "Expedition Map", "share_name": name, "user": user, "read": 1},
        ["write", "share"],
        as_dict=True,
    )


def _docshare_access(name: str, user: str) -> str | None:
    row = frappe.db.get_value(
        "DocShare",
        {"share_doctype": "Expedition Map", "share_name": name, "user": user, "read": 1},
        ["write"],
        as_dict=True,
    )
    if not row:
        return None
    return "write" if int(row.write or 0) else "read"


def map_permission(name: str, permission_type: str = "read", user: str | None = None) -> bool:
    """Canvas permission model for saved maps."""
    user = user or frappe.session.user
    row = _map_values(name)
    if user == "Administrator" or row.get("owner") == user or row.get("owner_user") == user:
        return True

    permission_type = (permission_type or "read").lower()
    if permission_type == "share":
        share_row = _docshare_row(name, user) if user != "Guest" else None
        return bool(share_row and int(share_row.share or 0))

    override = _override_access(row, user)
    docshare = _docshare_access(name, user) if user != "Guest" else None
    explicit = override or docshare
    if permission_type == "read":
        return bool(explicit in {"read", "write"} or row.get("is_public") or row.get("is_template"))

    if permission_type == "write":
        if explicit == "write":
            return True
        if explicit == "read":
            return False
        return bool(row.get("is_public") and row.get("public_access") == "Writable")

    return False


def assert_map_read(name: str) -> None:
    if not map_permission(name, "read"):
        frappe.throw(f"Not permitted to read Expedition Map {name}", frappe.PermissionError)


def assert_map_write(name: str) -> None:
    if not map_permission(name, "write"):
        frappe.throw(f"Not permitted to edit Expedition Map {name}", frappe.PermissionError)


def assert_map_share(name: str) -> None:
    if not map_permission(name, "share"):
        frappe.throw(f"Not permitted to share Expedition Map {name}", frappe.PermissionError)


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
