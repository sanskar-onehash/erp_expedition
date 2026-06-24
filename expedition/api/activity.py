# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Activity log endpoints.

v1 keeps this simple: log an activity, list recent activities for a
given related doc or for a given user. The activity layer on the map
queries the list endpoint with a bounds filter; the bottom drawer
queries the per-related-doc variant.
"""

import frappe


@frappe.whitelist()
def log_activity(
    activity_type: str,
    title: str,
    related_doctype: str | None = None,
    related_name: str | None = None,
    map_name: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    notes: str | None = None,
    occurred_at: str | None = None,
    duration_minutes: int | None = None,
    outcome: str | None = None,
) -> str:
    doc = frappe.new_doc("Expedition Activity")
    doc.activity_type = activity_type
    doc.title = title
    doc.user = frappe.session.user
    doc.related_doctype = related_doctype
    doc.related_name = related_name
    doc.map = map_name
    doc.latitude = latitude
    doc.longitude = longitude
    doc.notes = notes
    doc.occurred_at = occurred_at
    doc.duration_minutes = duration_minutes
    doc.outcome = outcome
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


@frappe.whitelist()
def list_for_related(
    related_doctype: str, related_name: str, limit: int = 50
) -> list[dict]:
    if not frappe.has_permission(related_doctype, "read", doc=related_name):
        frappe.throw(
            f"Not permitted to read {related_doctype} {related_name}",
            frappe.PermissionError,
        )
    return frappe.get_all(
        "Expedition Activity",
        filters={"related_doctype": related_doctype, "related_name": related_name},
        fields=[
            "name",
            "title",
            "activity_type",
            "user",
            "occurred_at",
            "latitude",
            "longitude",
            "notes",
            "outcome",
            "duration_minutes",
        ],
        order_by="occurred_at desc",
        limit=min(int(limit), 200),
    )


@frappe.whitelist()
def aggregate_for_related(
    related_doctype: str,
    related_name: str,
    bucket: str = "year",
) -> dict:
    """Aggregate Expedition Activity rows linked to a related entity,
    bucketed by year (default) or month. Returns counts per type per
    bucket, plus total counts.

    Used by the MapPopup "referral / activity" panel to answer the
    question: "how much have we paid to / worked with this customer /
    architect / dealer, and when was the last interaction?" The
    output is generic (counts by type by period) so the same endpoint
    serves any source DocType's relationship history.
    """
    if not frappe.has_permission(related_doctype, "read", doc=related_name):
        frappe.throw(
            f"Not permitted to read {related_doctype} {related_name}",
            frappe.PermissionError,
        )
    if bucket not in ("year", "month"):
        bucket = "year"

    rows = frappe.get_all(
        "Expedition Activity",
        filters={"related_doctype": related_doctype, "related_name": related_name},
        fields=["activity_type", "occurred_at", "outcome", "duration_minutes"],
        order_by="occurred_at desc",
        limit_page_length=0,
    )

    # Group by bucket. We pick the bucket date format up front and
    # bucket via Python instead of SQL DATE_FORMAT because we want
    # both the bucket label AND the year (for sorting).
    buckets = {}
    type_totals = {}
    outcome_totals = {}
    total_duration = 0
    last_at = None
    for r in rows:
        if not r.occurred_at:
            continue
        if isinstance(r.occurred_at, str):
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(r.occurred_at)
            except Exception:
                continue
        else:
            dt = r.occurred_at
        if bucket == "year":
            bk = str(dt.year)
        else:
            bk = f"{dt.year}-{dt.month:02d}"
        if bk not in buckets:
            buckets[bk] = {}
        # Each bucket: { total, by_type: { visit: 2, call: 1 }, by_outcome: {...} }
        b = buckets[bk]
        b["total"] = b.get("total", 0) + 1
        b["by_type"] = b.get("by_type", {})
        b["by_type"][r.activity_type] = b["by_type"].get(r.activity_type, 0) + 1
        b["by_outcome"] = b.get("by_outcome", {})
        if r.outcome:
            b["by_outcome"][r.outcome] = b["by_outcome"].get(r.outcome, 0) + 1
        if r.duration_minutes:
            total_duration += int(r.duration_minutes)

        type_totals[r.activity_type] = type_totals.get(r.activity_type, 0) + 1
        if r.outcome:
            outcome_totals[r.outcome] = outcome_totals.get(r.outcome, 0) + 1
        if last_at is None or dt > last_at:
            last_at = dt

    # Sort buckets descending (most recent first)
    sorted_buckets = [
        {"period": k, **buckets[k]} for k in sorted(buckets.keys(), reverse=True)
    ]

    return {
        "related_doctype": related_doctype,
        "related_name": related_name,
        "total": len(rows),
        "bucket": bucket,
        "last_activity_at": last_at.isoformat() if last_at else None,
        "by_type": type_totals,
        "by_outcome": outcome_totals,
        "total_duration_minutes": total_duration,
        "buckets": sorted_buckets,
    }


@frappe.whitelist()
def list_in_bounds(
    bounds: dict,
    activity_types: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    user: str | None = None,
    limit: int = 2000,
) -> list[dict]:
    """For the activity layer on the map. Optionally scoped to a date
    range and/or a user (matches the client spec: "GPS position overlay
    on different timescales (as logged by the BMS app)")."""
    if not frappe.has_permission("Expedition Activity", "read"):
        frappe.throw(
            "Not permitted to read Expedition Activity", frappe.PermissionError
        )
    filters = [
        ["latitude", "between", [float(bounds["south"]), float(bounds["north"])]],
        ["longitude", "between", [float(bounds["west"]), float(bounds["east"])]],
        ["latitude", "is", "set"],
        ["longitude", "is", "set"],
    ]
    if activity_types:
        filters.append(["activity_type", "in", activity_types])
    if start_date:
        filters.append(["occurred_at", ">=", str(start_date)])
    if end_date:
        filters.append(["occurred_at", "<=", str(end_date)])
    if user:
        filters.append(["user", "=", str(user)])
    return frappe.get_all(
        "Expedition Activity",
        filters=filters,
        fields=[
            "name",
            "title",
            "activity_type",
            "user",
            "occurred_at",
            "latitude",
            "longitude",
            "related_doctype",
            "related_name",
            "outcome",
        ],
        order_by="occurred_at desc",
        limit=min(int(limit), 5000),
    )
