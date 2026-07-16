# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

from __future__ import annotations

import json

import frappe

from expedition.api.permission import assert_map_read, assert_map_write


def _as_float(value, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        frappe.throw(f"{label} must be a number")


def _coerce_metadata(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        json.loads(value)
        return value
    return json.dumps(value)


def _pin_to_dict(doc) -> dict:
    metadata = None
    if getattr(doc, "metadata_json", None):
        try:
            metadata = json.loads(doc.metadata_json)
        except Exception:
            metadata = None
    return {
        "name": doc.name,
        "title": doc.title,
        "map": doc.map,
        "pin_type": doc.pin_type or "note",
        "status": doc.status or "open",
        "latitude": doc.latitude,
        "longitude": doc.longitude,
        "description": doc.description,
        "visibility": doc.visibility or "map",
        "linked_doctype": doc.linked_doctype,
        "linked_name": doc.linked_name,
        "icon": doc.icon or "pin-marker",
        "color": doc.color or "#F59E0B",
        "metadata": metadata,
        "owner": doc.owner,
        "modified": doc.modified,
    }


def _assert_pin_visible(doc) -> None:
    if getattr(doc, "visibility", None) == "private" and doc.owner != frappe.session.user:
        frappe.throw("Not permitted to access this private pin", frappe.PermissionError)


@frappe.whitelist()
def list_pins(map_name: str, include_archived: int = 0) -> list[dict]:
    assert_map_read(map_name)
    filters = {"map": map_name}
    if not int(include_archived or 0):
        filters["status"] = ["!=", "archived"]
    rows = frappe.get_all(
        "Expedition Map Pin",
        filters=filters,
        fields=[
            "name",
            "title",
            "map",
            "pin_type",
            "status",
            "latitude",
            "longitude",
            "description",
            "visibility",
            "linked_doctype",
            "linked_name",
            "icon",
            "color",
            "metadata_json",
            "owner",
            "modified",
        ],
        order_by="modified asc",
    )
    rows = [
        row
        for row in rows
        if row.get("visibility") != "private" or row.get("owner") == frappe.session.user
    ]
    return [_pin_to_dict(row) for row in rows]


@frappe.whitelist()
def create_pin(
    map_name: str,
    title: str,
    latitude,
    longitude,
    pin_type: str = "note",
    description: str | None = None,
    visibility: str = "map",
    icon: str = "pin-marker",
    color: str = "#F59E0B",
    linked_doctype: str | None = None,
    linked_name: str | None = None,
    metadata=None,
) -> dict:
    assert_map_write(map_name)
    if not title or not title.strip():
        frappe.throw("Pin title is required")
    lat = _as_float(latitude, "Latitude")
    lng = _as_float(longitude, "Longitude")
    if lat < -90 or lat > 90:
        frappe.throw("Latitude must be between -90 and 90")
    if lng < -180 or lng > 180:
        frappe.throw("Longitude must be between -180 and 180")
    if visibility not in ("map", "private"):
        frappe.throw("Visibility must be map or private")

    doc = frappe.new_doc("Expedition Map Pin")
    doc.map = map_name
    doc.title = title.strip()
    doc.pin_type = (pin_type or "note").strip() or "note"
    doc.status = "open"
    doc.latitude = lat
    doc.longitude = lng
    doc.description = (description or "").strip() or None
    doc.visibility = visibility
    doc.icon = (icon or "pin-marker").strip() or "pin-marker"
    doc.color = color or "#F59E0B"
    doc.linked_doctype = linked_doctype or None
    doc.linked_name = linked_name or None
    doc.metadata_json = _coerce_metadata(metadata)
    doc.insert(ignore_permissions=True)
    return _pin_to_dict(doc)


@frappe.whitelist()
def update_pin(name: str, **fields) -> dict:
    if not frappe.db.exists("Expedition Map Pin", name):
        frappe.throw(f"Unknown Expedition Map Pin {name}", frappe.DoesNotExistError)
    doc = frappe.get_doc("Expedition Map Pin", name)
    _assert_pin_visible(doc)
    assert_map_write(doc.map)

    for key in (
        "title",
        "pin_type",
        "description",
        "visibility",
        "icon",
        "color",
        "linked_doctype",
        "linked_name",
        "status",
    ):
        if key in fields:
            value = fields.get(key)
            if key == "title" and (not value or not str(value).strip()):
                frappe.throw("Pin title is required")
            if key == "visibility" and value not in ("map", "private"):
                frappe.throw("Visibility must be map or private")
            if key == "status" and value not in ("open", "resolved", "archived"):
                frappe.throw("Status must be open, resolved, or archived")
            setattr(doc, key, (str(value).strip() if value is not None else None) or None)

    if "latitude" in fields:
        doc.latitude = _as_float(fields.get("latitude"), "Latitude")
    if "longitude" in fields:
        doc.longitude = _as_float(fields.get("longitude"), "Longitude")
    if "metadata" in fields:
        doc.metadata_json = _coerce_metadata(fields.get("metadata"))

    doc.save(ignore_permissions=True)
    return _pin_to_dict(doc)


@frappe.whitelist()
def delete_pin(name: str) -> None:
    if not frappe.db.exists("Expedition Map Pin", name):
        return
    doc = frappe.get_doc("Expedition Map Pin", name)
    _assert_pin_visible(doc)
    assert_map_write(doc.map)
    frappe.delete_doc("Expedition Map Pin", name, ignore_permissions=True)
