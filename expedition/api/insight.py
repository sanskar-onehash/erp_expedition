# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""Insight computation — runs hourly via scheduler.

Builders return a list of Expedition Insight rows for a given map.
Read-side: GET /api/method/expedition.api.insight.get_active_for_map.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, now_datetime
from expedition.api.layer import (
    _aggregate_metric_values,
    _coerce_filter,
    _coerce_linked_metrics,
    _linked_metric_dynamic_doctype_field,
    validate_linked_metrics_json,
)
from expedition.api.permission import assert_map_read, assert_source_read


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


def _money_metric_severity(metric: dict, value) -> str:
    key_text = (
        f"{metric.get('key', '')} {metric.get('label', '')} "
        f"{metric.get('field', '')}"
    ).lower()
    if value in (None, "", 0, 0.0):
        return "info"
    if any(token in key_text for token in ("outstanding", "pending", "overdue", "unpaid", "due")):
        return "high"
    if any(token in key_text for token in ("open", "receivable", "invoice", "payment")):
        return "medium"
    return "low"


def _money_concentration(value, top_sources: list[dict], source_count: int) -> dict:
    total = float(value or 0) if isinstance(value, (int, float)) else 0.0
    active_count = len(top_sources)
    zero_count = max(int(source_count or 0) - active_count, 0)
    if total <= 0:
        return {
            "top_share": 0,
            "top_3_share": 0,
            "active_source_count": active_count,
            "zero_source_count": zero_count,
        }
    top_value = float(top_sources[0]["value"]) if top_sources else 0.0
    top_3_value = sum(
        float(row["value"])
        for row in top_sources[:3]
        if isinstance(row.get("value"), (int, float))
    )
    return {
        "top_share": round(top_value / total, 4),
        "top_3_share": round(top_3_value / total, 4),
        "active_source_count": active_count,
        "zero_source_count": zero_count,
    }


def _metric_status_fields(meta) -> list[str]:
    fields = []
    for fieldname in ("status", "workflow_state"):
        if meta.get_field(fieldname):
            fields.append(fieldname)
    fields.append("docstatus")
    return fields


def _metric_row_status(row: dict) -> str:
    status = row.get("status") or row.get("workflow_state")
    if status:
        return str(status)
    docstatus = row.get("docstatus")
    if docstatus == 0:
        return "Draft"
    if docstatus == 1:
        return "Submitted"
    if docstatus == 2:
        return "Cancelled"
    return "Unknown"


def _metric_status_counts(rows: list[dict]) -> list[dict]:
    counts = {}
    for row in rows:
        label = _metric_row_status(row)
        counts[label] = counts.get(label, 0) + 1
    return [
        {"label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _insight_linked_money(map_name: str) -> list[dict]:
    """Summarize configured linked money metrics for every enabled layer."""
    rows = []
    layers = frappe.get_all(
        "Expedition Layer",
        filters={"map": map_name, "enabled": 1},
        fields=[
            "name",
            "title",
            "source_doctype",
            "label_field",
            "filter_json",
            "linked_metrics_json",
        ],
        order_by="sequence asc",
    )
    for layer in layers:
        metrics = _coerce_linked_metrics(layer.get("linked_metrics_json") or "")
        if not metrics:
            continue
        source_doctype = layer.get("source_doctype")
        assert_source_read(source_doctype)
        source_filters = _coerce_filter(layer.get("filter_json")) or []
        source_fields = ["name"]
        label_field = (layer.get("label_field") or "").strip()
        if label_field:
            meta = frappe.get_meta(source_doctype)
            if meta.get_field(label_field):
                source_fields.append(label_field)
        source_rows = frappe.get_all(
            source_doctype,
            fields=source_fields,
            filters=source_filters,
            limit_page_length=5000,
            order_by="name asc",
        )
        source_names = [row.get("name") for row in source_rows if row.get("name")]
        if not source_names:
            continue
        source_labels = {
            row.get("name"): row.get(label_field) or row.get("name")
            for row in source_rows
        }

        metric_details = []
        highest = "info"
        severity_rank = {"info": 0, "low": 1, "medium": 2, "high": 3}
        for metric in metrics:
            validate_linked_metrics_json(source_doctype, [metric])
            metric_meta = frappe.get_meta(metric["source_doctype"])
            fields = [metric["link_field"]]
            dynamic_doctype_field = _linked_metric_dynamic_doctype_field(metric)
            if dynamic_doctype_field and dynamic_doctype_field not in fields:
                fields.append(dynamic_doctype_field)
            if metric["aggregate"] != "count":
                fields.append(metric["field"])
            for status_field in _metric_status_fields(metric_meta):
                if status_field not in fields:
                    fields.append(status_field)
            metric_filters = _coerce_filter(metric["filters"]) or []
            if dynamic_doctype_field:
                metric_filters.append([dynamic_doctype_field, "=", source_doctype])
            metric_filters.append([metric["link_field"], "in", source_names])
            metric_rows = frappe.get_all(
                metric["source_doctype"],
                fields=fields,
                filters=metric_filters,
                limit_page_length=0,
            )
            values = [
                1 if metric["aggregate"] == "count" else row.get(metric["field"])
                for row in metric_rows
            ]
            buckets = {source_name: [] for source_name in source_names}
            for metric_row in metric_rows:
                source_name = metric_row.get(metric["link_field"])
                if source_name in buckets:
                    buckets[source_name].append(
                        1 if metric["aggregate"] == "count" else metric_row.get(metric["field"])
                    )
            top_sources = []
            for source_name, source_values in buckets.items():
                source_value = _aggregate_metric_values(metric["aggregate"], source_values)
                if source_value in (None, "", 0, 0.0):
                    continue
                top_sources.append(
                    {
                        "name": source_name,
                        "label": source_labels.get(source_name) or source_name,
                        "value": source_value,
                        "row_count": len(source_values),
                    }
                )
            top_sources.sort(
                key=lambda row: float(row["value"])
                if isinstance(row.get("value"), (int, float))
                else 0,
                reverse=True,
            )
            value = _aggregate_metric_values(metric["aggregate"], values)
            severity = _money_metric_severity(metric, value)
            if severity_rank[severity] > severity_rank[highest]:
                highest = severity
            concentration = _money_concentration(
                value,
                top_sources,
                len(source_names),
            )
            metric_details.append(
                {
                    "key": metric["key"],
                    "label": metric["label"],
                    "source_doctype": metric["source_doctype"],
                    "aggregate": metric["aggregate"],
                    "field": metric["field"],
                    "value": value,
                    "row_count": len(metric_rows),
                    "statuses": _metric_status_counts(metric_rows),
                    "active_source_count": concentration["active_source_count"],
                    "zero_source_count": concentration["zero_source_count"],
                    "top_share": concentration["top_share"],
                    "top_3_share": concentration["top_3_share"],
                    "top_sources": top_sources[:5],
                }
            )
        if not metric_details:
            continue
        headline = ", ".join(
            f"{item['label']}: {item['value']}"
            for item in metric_details[:3]
        )
        rows.append(
            {
                "title": f"Field Metrics: {layer.get('title') or layer.get('name')}",
                "map": map_name,
                "summary": (
                    f"{layer.get('title') or source_doctype} has {len(source_names)} mapped records. "
                    f"{headline}"
                ),
                "severity": highest,
                "linked_doctype": source_doctype,
                "detail_json": json.dumps(
                    {
                        "layer": layer.get("name"),
                        "source_doctype": source_doctype,
                        "source_count": len(source_names),
                        "metrics": metric_details,
                    },
                    default=str,
                ),
            }
        )
    return rows


_BUILDERS = {
    "category_gap": _insight_category_gap,
    "linked_money": _insight_linked_money,
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


@frappe.whitelist()
def recompute_map(map_name: str) -> dict:
    """Recompute active insights for one readable map on demand."""
    if not map_name or not frappe.db.exists("Expedition Map", map_name):
        frappe.throw(f"Unknown Expedition Map {map_name}", frappe.DoesNotExistError)
    assert_map_read(map_name)
    written = recompute_for_map(map_name)
    return {
        "map": map_name,
        "written": written,
        "insights": get_active_for_map(map_name),
    }


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
    assert_map_read(map_name)
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
