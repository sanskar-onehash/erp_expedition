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
    doc.tag = (tag or "").strip() or None
    doc.insert(ignore_permissions=True)
    return {
        "name": doc.name,
        "title": doc.title,
        "zone_type": doc.zone_type,
        "geometry": geom,
        "color": doc.color,
        "fill_opacity": doc.fill_opacity,
        "stroke_color": doc.stroke_color,
        "stroke_width": doc.stroke_width,
        "tag": doc.tag,
        "centroid_lat": doc.centroid_lat,
        "centroid_lng": doc.centroid_lng,
    }


@frappe.whitelist()
def delete_zone(name: str) -> None:
    """Hard-delete a zone by name. Permission gated on the parent map."""
    if not frappe.db.exists("Expedition Zone", name):
        return
    zone = frappe.get_doc("Expedition Zone", name)
    _assert_map_write(zone.map)
    frappe.delete_doc("Expedition Zone", name, ignore_permissions=True)
