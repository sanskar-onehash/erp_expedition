# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe


def _assert_doc_read(source_doctype: str, source_name: str) -> None:
    if not source_doctype or not source_name:
        frappe.throw("Source document is required", frappe.ValidationError)
    if not frappe.db.exists(source_doctype, source_name):
        frappe.throw(
            f"Unknown {source_doctype} {source_name}",
            frappe.DoesNotExistError,
        )
    if not frappe.has_permission(source_doctype, "read", doc=source_name):
        frappe.throw(
            f"Not permitted to read {source_doctype} {source_name}",
            frappe.PermissionError,
        )


def _assert_doc_write(source_doctype: str, source_name: str) -> None:
    _assert_doc_read(source_doctype, source_name)
    if not frappe.has_permission(source_doctype, "write", doc=source_name):
        frappe.throw(
            f"Not permitted to update {source_doctype} {source_name}",
            frappe.PermissionError,
        )


def _validate_assignment_field(source_doctype: str, fieldname: str) -> None:
    if not fieldname:
        frappe.throw("Assignment field is required", frappe.ValidationError)
    if fieldname in {"owner", "modified_by"}:
        frappe.throw(
            f"Field '{fieldname}' is a standard audit field and cannot be used for custom assignment",
            frappe.ValidationError,
        )
    meta = frappe.get_meta(source_doctype)
    df = meta.get_field(fieldname)
    if not df:
        frappe.throw(
            f"Field '{fieldname}' does not exist on {source_doctype}",
            frappe.ValidationError,
        )
    if df.fieldtype != "Link" or df.options != "User":
        frappe.throw(
            f"Field '{fieldname}' must be a Link field to User",
            frappe.ValidationError,
        )


def _validate_user(user: str | None) -> str:
    selected = (user or "").strip()
    if not selected:
        frappe.throw("User is required", frappe.ValidationError)
    if not frappe.db.exists("User", selected):
        frappe.throw(f"Unknown User {selected}", frappe.DoesNotExistError)
    return selected


@frappe.whitelist()
def search_users(txt: str | None = "", limit: int | str = 8) -> list[dict]:
    """Return enabled users for popup assignment autocomplete."""
    from frappe.desk.search import search_link

    page_length = max(1, min(int(limit or 8), 20))
    rows = search_link(
        doctype="User",
        txt=txt or "",
        filters={"enabled": 1, "name": ["not in", ["Guest"]]},
        page_length=page_length,
    )
    return [
        {
            "value": row.get("value"),
            "label": row.get("label") or row.get("value"),
            "description": row.get("description") or "",
        }
        for row in rows
        if row.get("value") and row.get("value") != "Guest"
    ]


@frappe.whitelist()
def create_todo(
    source_doctype: str,
    source_name: str,
    description: str | None = None,
    allocated_to: str | None = None,
    priority: str | None = "Medium",
    date: str | None = None,
) -> str:
    """Create a generic Frappe ToDo linked to any readable source document."""
    _assert_doc_read(source_doctype, source_name)
    assignee = (allocated_to or frappe.session.user or "").strip()
    if assignee:
        _validate_user(assignee)

    doc = frappe.new_doc("ToDo")
    doc.description = description or f"Follow up on {source_doctype} {source_name}"
    doc.reference_type = source_doctype
    doc.reference_name = source_name
    doc.allocated_to = assignee or None
    doc.assigned_by = frappe.session.user
    doc.priority = priority or "Medium"
    if date:
        doc.date = date
    doc.insert(ignore_permissions=False)
    return doc.name


@frappe.whitelist()
def assign_to(
    source_doctype: str,
    source_name: str,
    user: str,
    description: str | None = None,
    priority: str | None = "Medium",
    date: str | None = None,
) -> dict:
    """Use Frappe's standard Assign To flow for a source document."""
    _assert_doc_read(source_doctype, source_name)
    selected_user = _validate_user(user)

    from frappe.desk.form.assign_to import add as add_assignment

    args = {
        "doctype": source_doctype,
        "name": source_name,
        "assign_to": [selected_user],
        "description": description or f"Assignment for {source_doctype} {source_name}",
        "priority": priority or "Medium",
    }
    if date:
        args["date"] = date
    add_assignment(args)
    return {"source_doctype": source_doctype, "source_name": source_name, "user": selected_user}


@frappe.whitelist()
def assign(
    source_doctype: str,
    source_name: str,
    fieldname: str,
    user: str,
) -> dict:
    """Set a generic User assignment field on a source document."""
    _assert_doc_write(source_doctype, source_name)
    _validate_assignment_field(source_doctype, fieldname)
    selected_user = _validate_user(user)

    doc = frappe.get_doc(source_doctype, source_name)
    doc.set(fieldname, selected_user)
    doc.save(ignore_permissions=False)
    return {"source_doctype": source_doctype, "source_name": source_name, "fieldname": fieldname, "value": selected_user}


@frappe.whitelist()
def unassign(
    source_doctype: str,
    source_name: str,
    fieldname: str,
) -> dict:
    """Clear a generic User assignment field on a source document."""
    _assert_doc_write(source_doctype, source_name)
    _validate_assignment_field(source_doctype, fieldname)

    doc = frappe.get_doc(source_doctype, source_name)
    doc.set(fieldname, None)
    doc.save(ignore_permissions=False)
    return {"source_doctype": source_doctype, "source_name": source_name, "fieldname": fieldname, "value": None}
