# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Zone API — write endpoints for the user-drawn regions on a map.

Read-side comes from `expedition.api.map.load_full`, which
returns zones inside the per-map envelope so the canvas never makes a
second roundtrip.

Write endpoints are intentionally narrow: a single create + a single
delete. The geometry is validated server-side (GeoJSON parse +
zone_type check) so we never persist junk.
"""

from __future__ import annotations

import json

import frappe
from frappe.model.document import Document


def _has_zone_stroke_style() -> bool:
    return frappe.db.has_column("Expedition Zone", "stroke_style")


def _assert_map_write(map_name: str) -> None:
    """Require write on Expedition Map <map_name>."""
    if not frappe.db.exists("Expedition Map", map_name):
        frappe.throw(f"Unknown Expedition Map {map_name}", frappe.DoesNotExistError)
    if not frappe.has_permission("Expedition Map", "write", doc=map_name):
        frappe.throw(
            f"Not permitted to edit Expedition Map {map_name}",
            frappe.PermissionError,
        )


def _coerce_geometry(geom) -> dict:
    if isinstance(geom, str):
        return json.loads(geom)
    if isinstance(geom, dict):
        return geom
    frappe.throw("Geometry must be a JSON string or object")


@frappe.whitelist()
def create_zone(
    map_name: str,
    title: str,
    zone_type: str = "polygon",
    geometry=None,
    color: str | None = None,
    fill_opacity: float = 0.25,
    stroke_color: str | None = None,
    stroke_width: int = 2,
    stroke_style: str | None = "solid",
    tag: str | None = None,
) -> dict:
    """
    Create a zone on a map. Returns the saved document so the
    client can render it without a reload.
    """
    _assert_map_write(map_name)
    if not title or not title.strip():
        frappe.throw("Zone title is required")
    geom = _coerce_geometry(geometry)
    if zone_type not in ("polygon", "circle"):
        frappe.throw(f"Unknown zone_type: {zone_type}")

    doc = frappe.new_doc("Expedition Zone")
    doc.map = map_name
    doc.title = title.strip()
    doc.zone_type = zone_type
    doc.geometry = json.dumps(geom)
    doc.color = color or "#3B82F6"
    doc.fill_opacity = fill_opacity
    doc.stroke_color = stroke_color or "#1E40AF"
    doc.stroke_width = stroke_width
    if _has_zone_stroke_style():
        doc.stroke_style = stroke_style or "solid"
    doc.tag = (tag or "").strip() or None
    doc.insert(ignore_permissions=True)
    return _zone_to_dict(doc)


def _zone_to_dict(doc) -> dict:
    geom = _coerce_geometry(doc.geometry)
    return {
        "name": doc.name,
        "title": doc.title,
        "zone_type": doc.zone_type,
        "geometry": geom,
        "color": doc.color,
        "fill_opacity": doc.fill_opacity,
        "stroke_color": doc.stroke_color,
        "stroke_width": doc.stroke_width,
        "stroke_style": getattr(doc, "stroke_style", None) or "solid",
        "tag": doc.tag,
        "centroid_lat": doc.centroid_lat,
        "centroid_lng": doc.centroid_lng,
    }


@frappe.whitelist()
def update_zone(
    name: str,
    title: str | None = None,
    geometry=None,
    color: str | None = None,
    fill_opacity: float | None = None,
    stroke_color: str | None = None,
    stroke_width: int | None = None,
    stroke_style: str | None = None,
    tag: str | None = None,
) -> dict:
    """Update a drawn zone. Permission gated on the parent map."""
    if not frappe.db.exists("Expedition Zone", name):
        frappe.throw(f"Unknown Expedition Zone {name}", frappe.DoesNotExistError)
    doc = frappe.get_doc("Expedition Zone", name)
    _assert_map_write(doc.map)
    if title is not None:
        if not title.strip():
            frappe.throw("Zone title is required")
        doc.title = title.strip()
    if geometry is not None:
        doc.geometry = json.dumps(_coerce_geometry(geometry))
    if color is not None:
        doc.color = color
    if fill_opacity is not None:
        doc.fill_opacity = fill_opacity
    if stroke_color is not None:
        doc.stroke_color = stroke_color
    if stroke_width is not None:
        doc.stroke_width = int(stroke_width)
    if stroke_style is not None and _has_zone_stroke_style():
        if stroke_style not in ("solid", "dashed", "dotted"):
            frappe.throw("stroke_style must be solid, dashed, or dotted")
        doc.stroke_style = stroke_style
    if tag is not None:
        doc.tag = tag.strip() or None
    doc.save(ignore_permissions=True)
    return _zone_to_dict(doc)


@frappe.whitelist()
def delete_zone(name: str) -> None:
    """Hard-delete a zone by name. Permission gated on the parent map."""
    if not frappe.db.exists("Expedition Zone", name):
        return
    zone = frappe.get_doc("Expedition Zone", name)
    _assert_map_write(zone.map)
    frappe.delete_doc("Expedition Zone", name, ignore_permissions=True)
