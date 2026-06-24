# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""Insight computation — runs hourly via scheduler.

Builders return a list of Expedition Insight rows for a given map.
Read-side: GET /api/method/expedition.api.insight.get_active_for_map.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_days, now_datetime


INSIGHT_TTL_DAYS = 7


def _insight_category_gap(map_name: str) -> list[dict]:
    """Flag a layer whose declared `category` Select options have no rows."""
    layers = _enabled_layers_for(map_name)
    if not layers:
        return []
    pin_table = None
    for lyr in layers:
        if lyr.source_doctype:
            pin_table = lyr.source_doctype
            break
    if not pin_table:
        return []
    meta = frappe.get_meta(pin_table)
    cat_field = meta.get_field("category")
    if not cat_field or not cat_field.options:
        return []
    declared = [c.strip() for c in cat_field.options.split("\n") if c.strip()]
    present = {
        r.category
        for r in frappe.db.sql(
            f"select distinct category from `tab{pin_table}` where category is not null",
            as_dict=True,
        )
    }
    missing = [c for c in declared if c not in present]
    if not missing:
        return []

    map_doc = frappe.get_doc("Expedition Map", map_name)
    lat = lng = None
    if map_doc.viewport:
        try:
            vp = frappe.parse_json(map_doc.viewport)
            c = vp.get("center") if isinstance(vp, dict) else None
            if isinstance(c, (list, tuple)) and len(c) == 2:
                lng, lat = c[0], c[1]
        except Exception:
            pass
    return [
        {
            "title": f"No data: {', '.join(missing)}",
            "map": map_name,
            "summary": (
                f"This map's layer expects categories that have no data: "
                f"{', '.join(missing)}. Either seed or remove the filter."
            ),
            "severity": "low",
            "centroid_lat": lat,
            "centroid_lng": lng,
            "linked_doctype": pin_table,
            "detail_json": f'{{"missing": {missing!r}}}',
        }
    ]


_BUILDERS = {
    "category_gap": _insight_category_gap,
}


def _enabled_layers_for(map_name: str) -> list[dict]:
    return frappe.get_all(
        "Expedition Layer",
        filters={"map": map_name, "enabled": 1},
        fields=["name", "source_doctype", "filter_json"],
        order_by="sequence asc",
    )


def _expire_old_insights() -> None:
    frappe.db.sql(
        """
		update `tabExpedition Insight`
		set is_active = 0
		where expires_at < %s and is_active = 1
	""",
        (now_datetime(),),
    )


def _all_maps() -> list[dict]:
    return frappe.get_all("Expedition Map", fields=["name", "is_public", "is_template"])


def recompute_all() -> int:
    """Run all insight builders across all maps. Returns new insights written."""
    _expire_old_insights()
    written = 0
    for map_doc in _all_maps():
        written += _recompute_for_map(map_doc.name)
    return written


def recompute_for_map(map_name: str) -> int:
    _expire_old_insights()
    return _recompute_for_map(map_name)


def _recompute_for_map(map_name: str) -> int:
    # Hard-delete active insights for this map so deleted/changed
    # builder output doesn't linger. Insights are derived, not authored.
    frappe.db.sql(
        "delete from `tabExpedition Insight` where map = %s and is_active = 1",
        (map_name,),
    )
    written = 0
    for name, builder in _BUILDERS.items():
        try:
            results = builder(map_name)
        except Exception:
            frappe.logger("expedition").exception(
                f"Insight builder {name} failed for map {map_name}"
            )
            continue
        for r in results:
            doc = frappe.new_doc("Expedition Insight")
            doc.update(r)
            doc.insight_type = name
            doc.computed_at = now_datetime()
            doc.expires_at = add_days(now_datetime(), INSIGHT_TTL_DAYS)
            doc.is_active = 1
            doc.insert(ignore_permissions=True)
            written += 1
    return written


@frappe.whitelist(allow_guest=True)
def get_active_for_map(map_name: str) -> list[dict]:
    """Active insights for a given map. Public maps are readable by guests;
    private maps require read on the Expedition Map doc."""
    if not map_name or not frappe.db.exists("Expedition Map", map_name):
        frappe.throw(f"Unknown Expedition Map {map_name}", frappe.DoesNotExistError)
    map_doc = frappe.get_doc("Expedition Map", map_name)
    if not map_doc.is_public and not frappe.has_permission(
        "Expedition Map", "read", doc=map_name
    ):
        frappe.throw(
            f"Not permitted to read Expedition Map {map_name}",
            frappe.PermissionError,
        )
    return frappe.get_all(
        "Expedition Insight",
        filters={"map": map_name, "is_active": 1},
        fields=[
            "name",
            "title",
            "insight_type",
            "severity",
            "summary",
            "detail_json",
            "centroid_lat",
            "centroid_lng",
            "linked_doctype",
            "linked_name",
            "computed_at",
        ],
        order_by="severity desc, computed_at desc",
    )
