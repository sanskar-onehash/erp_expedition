# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
import math
from typing import Any

import frappe

from expedition.api.layer import _coerce_filter
from expedition.api.permission import assert_source_read


AGGREGATES = {"count", "sum", "avg", "min", "max"}


def _as_dict(value: Any) -> dict:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _field_exists(source_doctype: str, fieldname: str) -> bool:
    if fieldname in {"name", "owner", "creation", "modified", "modified_by", "docstatus"}:
        return True
    return bool(frappe.get_meta(source_doctype).get_field(fieldname))


def _append_filter(filters: list, fieldname: str | None, op: str, value: Any) -> None:
    if fieldname:
        filters.append([fieldname, op, value])


def _bounds_filters(latitude_field: str, longitude_field: str, bounds: dict) -> list[list]:
    return [
        [latitude_field, "between", [float(bounds["south"]), float(bounds["north"])]],
        [longitude_field, "between", [float(bounds["west"]), float(bounds["east"])]],
        [latitude_field, "is", "set"],
        [longitude_field, "is", "set"],
    ]


def _polygon_rings(geometry: dict) -> list[list[list[float]]]:
    if not geometry:
        return []
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates", [[]])[0]]
    if geometry.get("type") == "MultiPolygon":
        return [poly[0] for poly in geometry.get("coordinates", []) if poly]
    return []


def _point_in_ring(lng: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        intersects = ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _point_in_geometry(lng: float, lat: float, geometry: dict) -> bool:
    return any(_point_in_ring(lng, lat, ring) for ring in _polygon_rings(geometry))


def _geometry_bounds(geometry: dict) -> dict | None:
    coords = []
    for ring in _polygon_rings(geometry):
        coords.extend(ring)
    if not coords:
        return None
    lngs = [float(c[0]) for c in coords]
    lats = [float(c[1]) for c in coords]
    return {"south": min(lats), "west": min(lngs), "north": max(lats), "east": max(lngs)}


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    earth = 6378137.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return earth * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _aggregate_values(values: list[Any], aggregate: str) -> float | int | None:
    if aggregate == "count":
        return len(values)
    numbers = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            numbers.append(number)
    if not numbers:
        return None
    if aggregate == "sum":
        return sum(numbers)
    if aggregate == "avg":
        return sum(numbers) / len(numbers)
    if aggregate == "min":
        return min(numbers)
    if aggregate == "max":
        return max(numbers)
    return None


@frappe.whitelist()
def aggregate(
    source_doctype: str,
    aggregate: str = "count",
    metric_field: str | None = None,
    filters: str | list | None = None,
    relation_field: str | None = None,
    relation_value: str | None = None,
    latitude_field: str | None = None,
    longitude_field: str | None = None,
    bounds: str | dict | None = None,
    zone_geometry: str | dict | None = None,
    limit: int = 10000,
) -> dict:
    """Generic rollup over any readable DocType.

    Optional spatial arguments let the same endpoint power zone metrics.
    Polygon filtering is done after a bounding-box prefilter so it stays
    generic without requiring database-specific GIS extensions.
    """
    assert_source_read(source_doctype)
    agg = str(aggregate or "count").lower()
    if agg not in AGGREGATES:
        frappe.throw(f"Unsupported aggregate '{aggregate}'", frappe.ValidationError)
    if agg != "count" and not metric_field:
        frappe.throw("metric_field is required for numeric aggregates", frappe.ValidationError)
    if metric_field and not _field_exists(source_doctype, metric_field):
        frappe.throw(f"Unknown metric field '{metric_field}'", frappe.ValidationError)
    if relation_field and not _field_exists(source_doctype, relation_field):
        frappe.throw(f"Unknown relation field '{relation_field}'", frappe.ValidationError)

    rows_filters = _coerce_filter(filters) or []
    if relation_field and relation_value is not None:
        _append_filter(rows_filters, relation_field, "=", relation_value)

    geom = _as_dict(zone_geometry)
    spatial_bounds = _as_dict(bounds)
    if geom and not spatial_bounds:
        spatial_bounds = _geometry_bounds(geom) or {}
    if spatial_bounds:
        if not latitude_field or not longitude_field:
            frappe.throw("latitude_field and longitude_field are required for spatial metrics", frappe.ValidationError)
        rows_filters.extend(_bounds_filters(latitude_field, longitude_field, spatial_bounds))

    fields = ["name"]
    if metric_field and metric_field not in fields:
        fields.append(metric_field)
    if geom:
        for fieldname in (latitude_field, longitude_field):
            if fieldname and fieldname not in fields:
                fields.append(fieldname)

    rows = frappe.get_all(
        source_doctype,
        filters=rows_filters,
        fields=fields,
        limit_page_length=min(max(int(limit or 10000), 1), 50000),
    )
    if geom:
        rows = [
            row
            for row in rows
            if row.get(latitude_field) is not None
            and row.get(longitude_field) is not None
            and _point_in_geometry(float(row.get(longitude_field)), float(row.get(latitude_field)), geom)
        ]

    values = rows if agg == "count" else [row.get(metric_field) for row in rows]
    return {
        "source_doctype": source_doctype,
        "aggregate": agg,
        "metric_field": metric_field,
        "value": _aggregate_values(values, agg),
        "count": len(rows),
    }


@frappe.whitelist()
def summarize_zone(
    zone_name: str,
    layer_names: str | list | None = None,
    metrics: str | list | None = None,
) -> dict:
    """Return generic per-layer counts and optional metric specs for a zone."""
    if not frappe.db.exists("Expedition Zone", zone_name):
        frappe.throw(f"Unknown Expedition Zone {zone_name}", frappe.DoesNotExistError)
    zone = frappe.get_doc("Expedition Zone", zone_name)
    if not frappe.has_permission("Expedition Map", "read", doc=zone.map):
        frappe.throw("Not permitted to read this map", frappe.PermissionError)
    geometry = _as_dict(zone.geometry)
    if not geometry:
        return {"zone": zone_name, "layers": [], "metrics": []}

    requested_layers = _as_list(layer_names)
    layer_filters = {"map": zone.map, "enabled": 1}
    if requested_layers:
        layer_filters["name"] = ["in", requested_layers]
    layers = frappe.get_all(
        "Expedition Layer",
        filters=layer_filters,
        fields=["name", "title", "source_doctype", "latitude_field", "longitude_field", "filter_json"],
        order_by="sequence asc",
    )

    layer_summaries = []
    for layer in layers:
        result = aggregate(
            source_doctype=layer.source_doctype,
            aggregate="count",
            filters=layer.filter_json,
            latitude_field=layer.latitude_field,
            longitude_field=layer.longitude_field,
            zone_geometry=geometry,
        )
        layer_summaries.append(
            {
                "layer": layer.name,
                "title": layer.title,
                "source_doctype": layer.source_doctype,
                "count": result["count"],
            }
        )

    metric_summaries = []
    for spec in _as_list(metrics):
        if not isinstance(spec, dict):
            continue
        metric_summaries.append(
            {
                "label": spec.get("label") or spec.get("metric_field") or spec.get("aggregate") or "Metric",
                **aggregate(
                    source_doctype=spec.get("source_doctype"),
                    aggregate=spec.get("aggregate") or "count",
                    metric_field=spec.get("metric_field"),
                    filters=spec.get("filters"),
                    relation_field=spec.get("relation_field"),
                    relation_value=spec.get("relation_value"),
                    latitude_field=spec.get("latitude_field"),
                    longitude_field=spec.get("longitude_field"),
                    zone_geometry=geometry,
                ),
            }
        )

    return {"zone": zone_name, "layers": layer_summaries, "metrics": metric_summaries}


@frappe.whitelist()
def radius_coverage(
    radius_layer: str,
    target_layer: str,
    bounds: str | dict | None = None,
    limit: int = 10000,
) -> dict:
    """Generic coverage analysis between a radius source layer and target layer."""
    radius_doc = frappe.get_doc("Expedition Layer", radius_layer)
    target_doc = frappe.get_doc("Expedition Layer", target_layer)
    assert_source_read(radius_doc.source_doctype)
    assert_source_read(target_doc.source_doctype)

    spatial_bounds = _as_dict(bounds)
    radius_filters = _coerce_filter(radius_doc.filter_json) or []
    target_filters = _coerce_filter(target_doc.filter_json) or []
    if spatial_bounds:
        radius_filters.extend(_bounds_filters(radius_doc.latitude_field, radius_doc.longitude_field, spatial_bounds))
        target_filters.extend(_bounds_filters(target_doc.latitude_field, target_doc.longitude_field, spatial_bounds))

    radius_fields = ["name", radius_doc.latitude_field, radius_doc.longitude_field]
    radius_field = radius_doc.radius_field or ""
    if radius_field and radius_field not in radius_fields:
        radius_fields.append(radius_field)
    radius_rows = frappe.get_all(
        radius_doc.source_doctype,
        filters=radius_filters,
        fields=radius_fields,
        limit_page_length=min(max(int(limit or 10000), 1), 50000),
    )
    target_rows = frappe.get_all(
        target_doc.source_doctype,
        filters=target_filters,
        fields=["name", target_doc.latitude_field, target_doc.longitude_field],
        limit_page_length=min(max(int(limit or 10000), 1), 50000),
    )

    coverage_counts = []
    covered = 0
    overlap = 0
    uncovered_names = []
    fallback_radius = float(radius_doc.radius_meters or 5000)
    for target in target_rows:
        tlat = target.get(target_doc.latitude_field)
        tlng = target.get(target_doc.longitude_field)
        if tlat is None or tlng is None:
            continue
        matches = 0
        for source in radius_rows:
            slat = source.get(radius_doc.latitude_field)
            slng = source.get(radius_doc.longitude_field)
            if slat is None or slng is None:
                continue
            radius_m = fallback_radius
            if radius_field:
                try:
                    radius_m = float(source.get(radius_field) or fallback_radius)
                except (TypeError, ValueError):
                    radius_m = fallback_radius
            if _haversine_m(float(tlat), float(tlng), float(slat), float(slng)) <= radius_m:
                matches += 1
        coverage_counts.append(matches)
        if matches:
            covered += 1
        else:
            uncovered_names.append(target.name)
        if matches > 1:
            overlap += 1

    total = len(coverage_counts)
    return {
        "radius_layer": radius_layer,
        "target_layer": target_layer,
        "total": total,
        "covered": covered,
        "uncovered": total - covered,
        "overlap": overlap,
        "coverage_percent": (covered / total * 100) if total else 0,
        "uncovered_names": uncovered_names[:200],
    }
