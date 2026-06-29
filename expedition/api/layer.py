# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Layer data fetch — the single most permission-sensitive endpoint in
Expedition. The flow:

  1. Resolve Expedition Layer by name
  2. Check user can read the Layer doc (standard Frappe perm)
  3. Resolve the source DocType + lat/lng fields
  4. Enforce `frappe.has_permission(source_doctype, "read")` server-side
  5. Use `frappe.get_all` so any DocType-level permission query
     conditions are auto-applied (row-level security)
  6. Return a GeoJSON FeatureCollection

Client-side filter JSON is applied as an ADDITIONAL filter on top of
the server's permission filter — never as a substitute. This is the
hard rule that prevents privilege escalation through crafted clients.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import frappe

from expedition.api.icon import assert_icon_readable
from expedition.api.permission import assert_source_read


LAYOUT_FIELD_TYPES = {
    "Section Break",
    "Column Break",
    "Tab Break",
    "HTML",
    "Table",
    "Table MultiSelect",
    "Button",
    "Image",
    "Fold",
}

FILTERABLE_FIELD_TYPES = {
    "Attach",
    "Attach Image",
    "Barcode",
    "Check",
    "Code",
    "Color",
    "Currency",
    "Data",
    "Date",
    "Datetime",
    "Duration",
    "Dynamic Link",
    "Email",
    "Float",
    "Geolocation",
    "Int",
    "JSON",
    "Link",
    "Long Text",
    "Markdown Editor",
    "Password",
    "Percent",
    "Phone",
    "Rating",
    "Read Only",
    "Select",
    "Small Text",
    "Text",
    "Text Editor",
    "Time",
    "URL",
}

STANDARD_FILTER_FIELDS = [
    {
        "fieldname": "name",
        "fieldtype": "Data",
        "label": "ID",
        "options": "",
        "standard": 1,
    },
    {
        "fieldname": "owner",
        "fieldtype": "Link",
        "label": "Owner",
        "options": "User",
        "standard": 1,
    },
    {
        "fieldname": "creation",
        "fieldtype": "Datetime",
        "label": "Created On",
        "options": "",
        "standard": 1,
    },
    {
        "fieldname": "modified",
        "fieldtype": "Datetime",
        "label": "Last Modified On",
        "options": "",
        "standard": 1,
    },
    {
        "fieldname": "modified_by",
        "fieldtype": "Link",
        "label": "Last Modified By",
        "options": "User",
        "standard": 1,
    },
    {
        "fieldname": "docstatus",
        "fieldtype": "Select",
        "label": "Document Status",
        "options": "0\n1\n2",
        "standard": 1,
    },
]

DOCSTATUS_FILTER_OPTIONS = [
    {"label": "Draft", "value": 0},
    {"label": "Submitted", "value": 1},
    {"label": "Cancelled", "value": 2},
]

TEXT_FIELD_TYPES = {
    "Attach",
    "Attach Image",
    "Barcode",
    "Code",
    "Color",
    "Data",
    "Email",
    "Geolocation",
    "JSON",
    "Long Text",
    "Markdown Editor",
    "Password",
    "Phone",
    "Read Only",
    "Small Text",
    "Text",
    "Text Editor",
    "URL",
}
NUMERIC_FIELD_TYPES = {"Currency", "Duration", "Float", "Int", "Percent", "Rating"}
DATE_FIELD_TYPES = {"Date", "Datetime", "Time"}
LINK_FIELD_TYPES = {"Link", "Dynamic Link"}

COMMON_OPERATORS = [
    {"value": "=", "label": "equals", "requires_value": True},
    {"value": "!=", "label": "not equals", "requires_value": True},
    {"value": "in", "label": "in", "requires_value": True, "multi": True},
    {"value": "not in", "label": "not in", "requires_value": True, "multi": True},
    {"value": "is", "label": "is set", "requires_value": False, "fixed_value": "set"},
    {
        "value": "is",
        "label": "is not set",
        "requires_value": False,
        "fixed_value": "not set",
    },
]

TEXT_OPERATORS = [
    {"value": "like", "label": "contains", "requires_value": True},
    {"value": "not like", "label": "not contains", "requires_value": True},
]

ORDER_OPERATORS = [
    {"value": ">", "label": "greater than", "requires_value": True},
    {"value": ">=", "label": "greater or equal", "requires_value": True},
    {"value": "<", "label": "less than", "requires_value": True},
    {"value": "<=", "label": "less or equal", "requires_value": True},
    {"value": "between", "label": "between", "requires_value": True, "range": True},
]


def _coerce_filter(filter_json: str | dict | None) -> list | None:
    """Parse a Frappe filter spec from JSON. Returns a list of filters or None."""
    if not filter_json:
        return None
    parsed = json.loads(filter_json) if isinstance(filter_json, str) else filter_json
    if not parsed:
        return None
    if not isinstance(parsed, list):
        frappe.throw(
            "Filter must be a Frappe filter list (array of [field, op, value] tuples)"
        )
    # Light validation: each entry must be a 2- or 3-tuple.
    normalized = []
    for f in parsed:
        if not isinstance(f, (list, tuple)) or len(f) not in (2, 3):
            frappe.throw("Malformed filter entry")
        if len(f) == 2:
            field, value = f
            op = "="
        else:
            field, op, value = f
        op_key = _operator_key(op)
        if op_key in {"in", "not in", "between"}:
            value = _parse_multi_value(value)
        elif op_key in {"like", "not like"} and isinstance(value, str) and "%" not in value:
            value = f"%{value}%"
        normalized.append([field, op, value])
    return normalized


def _operator_key(op: str | None) -> str:
    return str(op or "=").strip().lower()


def _operators_for_field(fieldtype: str | None) -> list[dict]:
    ft = fieldtype or "Data"
    ops = [COMMON_OPERATORS[0], COMMON_OPERATORS[1]]
    if ft in TEXT_FIELD_TYPES or ft in LINK_FIELD_TYPES or ft in {"Select"}:
        ops += TEXT_OPERATORS
    if ft in NUMERIC_FIELD_TYPES or ft in DATE_FIELD_TYPES:
        ops += ORDER_OPERATORS
    ops += [COMMON_OPERATORS[2], COMMON_OPERATORS[3], COMMON_OPERATORS[4], COMMON_OPERATORS[5]]
    return ops


def _field_meta_dict(f) -> dict:
    return {
        "fieldname": f.fieldname,
        "fieldtype": f.fieldtype,
        "label": f.label or f.fieldname,
        "options": f.options or "",
        "reqd": int(getattr(f, "reqd", 0) or 0),
        "hidden": int(getattr(f, "hidden", 0) or 0),
        "read_only": int(getattr(f, "read_only", 0) or 0),
        "standard": 0,
        "operators": _operators_for_field(f.fieldtype),
    }


def _filter_field_map(source_doctype: str) -> dict[str, dict]:
    meta = frappe.get_meta(source_doctype)
    fields: dict[str, dict] = {}
    for f in STANDARD_FILTER_FIELDS:
        fields[f["fieldname"]] = {**f, "operators": _operators_for_field(f["fieldtype"])}
    for f in meta.fields:
        if not f.fieldname:
            continue
        if f.fieldtype in LAYOUT_FIELD_TYPES:
            continue
        if f.fieldtype not in FILTERABLE_FIELD_TYPES:
            continue
        fields[f.fieldname] = _field_meta_dict(f)
    return fields


def _select_options(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [line.strip() for line in str(raw).splitlines() if line.strip()]


def _select_filter_options(field_meta: dict) -> list:
    if field_meta.get("fieldname") == "docstatus":
        return DOCSTATUS_FILTER_OPTIONS
    return _select_options(field_meta.get("options"))


def _parse_multi_value(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return [part.strip() for part in text.split(",") if part.strip()]


def _validate_filter_rows(source_doctype: str, filters: list | None) -> None:
    if not filters:
        return
    field_map = _filter_field_map(source_doctype)
    for raw in filters:
        if len(raw) == 3:
            field, op, value = raw
        elif len(raw) == 2:
            field, value = raw
            op = "="
        else:
            frappe.throw("Malformed filter entry", frappe.ValidationError)
        if field not in field_map:
            frappe.throw(
                f"Field '{field}' is not filterable on {source_doctype}",
                frappe.ValidationError,
            )
        meta = field_map[field]
        allowed = {_operator_key(o["value"]) for o in meta.get("operators", [])}
        op_key = _operator_key(op)
        if op_key == "==":
            op_key = "="
        if op_key not in allowed:
            frappe.throw(
                f"Operator '{op}' is not valid for field '{field}'",
                frappe.ValidationError,
            )
        if op_key in {"in", "not in"} and not _parse_multi_value(value):
            frappe.throw(
                f"Filter '{field} {op}' needs at least one value",
                frappe.ValidationError,
            )
        if op_key == "between":
            values = _parse_multi_value(value)
            if len(values) != 2:
                frappe.throw(
                    f"Filter '{field} between' needs exactly two values",
                    frappe.ValidationError,
                )
        if op_key == "is" and str(value or "").strip().lower() not in {"set", "not set"}:
            frappe.throw(
                f"Filter '{field} is' must use set or not set",
                frappe.ValidationError,
            )


def validate_filter_json(source_doctype: str, filter_json: str | dict | None) -> None:
    """Validate an Expedition layer filter against a source DocType schema."""
    if not filter_json:
        return
    assert_source_read(source_doctype)
    _validate_filter_rows(source_doctype, _coerce_filter(filter_json))


def _coerce_group_config(raw: str | dict | None) -> dict:
    """Parse group_config_json. Shape: { "<value>": { color, icon, label } }."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for value, cfg in parsed.items():
        if not isinstance(cfg, dict):
            continue
        entry = {}
        if isinstance(cfg.get("color"), str) and cfg["color"].startswith("#"):
            entry["color"] = cfg["color"]
        if isinstance(cfg.get("icon"), str) and cfg["icon"]:
            entry["icon"] = cfg["icon"]
        if isinstance(cfg.get("label"), str):
            entry["label"] = cfg["label"]
        if entry:
            out[str(value)] = entry
    return out


def _assert_icons_readable(icon: str | None = None, group_config_json: str | dict | None = None) -> None:
    assert_icon_readable(icon)
    cfg = _coerce_group_config(group_config_json)
    for item in cfg.values():
        assert_icon_readable(item.get("icon"))


def _coerce_popup_fields(raw: str | list | None) -> list[str]:
    """Parse popup_fields_json. Shape: ["field_a", "field_b"]."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(f) for f in parsed if isinstance(f, str) and f]


def _full_row_context(
    source_doctype: str, doc_name: str | None, fallback: dict
) -> dict:
    """Build the Jinja context for popup_template rendering.

    Pulls the full source doc (subject to per-doc read permission) and
    exposes it as both `doc` and as top-level fields. Falls back to the
    already-fetched column subset if the doc is missing or unreadable.
    """
    ctx: dict[str, Any] = dict(fallback)
    if not doc_name:
        ctx["doc"] = frappe._dict(fallback)
        return ctx
    try:
        # `ignore_permissions=False` (the default) enforces per-row
        # read permission. If the user can't read this specific row,
        # the template renders with the partial `fallback` context —
        # never with the full doc. This is defense in depth: the
        # outer `get_all` already filtered by perm query conditions.
        if frappe.has_permission(source_doctype, "read", doc=doc_name):
            doc = frappe.get_doc(source_doctype, doc_name)
            full = doc.as_dict()
            ctx.update(full)
            ctx["doc"] = doc
        else:
            ctx["doc"] = frappe._dict(fallback)
    except frappe.DoesNotExistError:
        ctx["doc"] = frappe._dict(fallback)
    return ctx


@frappe.whitelist(allow_guest=True)
def get_features(
    layer: str,
    bounds: dict | None = None,
    limit: int = 5000,
    offset: int = 0,
) -> dict:
    """
    Return a GeoJSON FeatureCollection for the given Expedition Layer.

    Args:
            layer: name of the Expedition Layer doc
            bounds: optional {south, west, north, east} viewport filter
            limit:  hard cap (default 5000 — clusters take care of the rest)
            offset: pagination offset

    Returns:
            {type: "FeatureCollection", features: [...], total: int, truncated: bool}

    Open to guests so the canvas renders for anonymous visitors on
    public maps. Row-level permission on the source DocType is enforced
    via `assert_source_read` — guests will see zero rows for source
    doctypes they cannot read.
    """
    # Expedition Layer itself is meta-only metadata; the real permission
    # boundary is the source DocType checked below. We intentionally do
    # not gate on `has_permission("Expedition Layer")` so the source
    # DocType's own permission model is the single source of truth.

    layer_doc = frappe.get_doc("Expedition Layer", layer)
    if not layer_doc.enabled:
        return {
            "type": "FeatureCollection",
            "features": [],
            "total": 0,
            "truncated": False,
        }

    # Hard rule: server-side permission check on the source DocType.
    assert_source_read(layer_doc.source_doctype)

    filters = _coerce_filter(layer_doc.filter_json) or []

    # Apply viewport bounds as additional lat/lng WHERE clauses. This is
    # safe because the source lat/lng field types are validated at layer
    # save time (Float), so we cannot SQL-inject via these field names.
    if bounds:
        south = float(bounds.get("south"))
        west = float(bounds.get("west"))
        north = float(bounds.get("north"))
        east = float(bounds.get("east"))
        filters += [
            [layer_doc.latitude_field, ">=", south],
            [layer_doc.latitude_field, "<=", north],
            [layer_doc.longitude_field, ">=", west],
            [layer_doc.longitude_field, "<=", east],
        ]
        # Exclude nulls
        filters += [
            [layer_doc.latitude_field, "is", "set"],
            [layer_doc.longitude_field, "is", "set"],
        ]

    # frappe.get_all auto-applies permission_query_conditions for the
    # source doctype, so row-level security flows through unchanged.
    #
    # `fields` MUST be explicit. Without it, get_all returns only `name`,
    # so the lat/lng extraction below silently yields zero features.
    limit = min(int(limit), 10000)
    offset = max(int(offset), 0)

    fields = ["name", layer_doc.latitude_field, layer_doc.longitude_field]
    if layer_doc.label_field and layer_doc.label_field not in fields:
        fields.append(layer_doc.label_field)

    # If the layer has a popup_template, we need the full row context (every
    # column on the source doc) to render it. Without this, only the
    # `fields` list above is fetched and templates can only reference
    # those columns. We use doc.as_dict() semantics: pull the whole doc,
    # restricted by the same permission boundary, and let Jinja reference
    # any field the user can see.
    popup_template = (layer_doc.popup_template or "").strip()

    rows: list[dict[str, Any]] = frappe.get_all(
        layer_doc.source_doctype,
        fields=fields,
        filters=filters,
        limit=limit,
        limit_start=offset,
        order_by=f"{layer_doc.latitude_field} asc",
    )

    total = frappe.db.count(layer_doc.source_doctype, filters=filters)

    features = []
    group_config = _coerce_group_config(layer_doc.group_config_json)
    group_by_field = (layer_doc.group_by_field or "").strip() or None
    popup_fields = _coerce_popup_fields(layer_doc.popup_fields_json)
    # Add group_by_field to fetched fields if not already present
    if group_by_field and group_by_field not in fields:
        fields.append(group_by_field)

    # Re-fetch if we added group_by_field
    if group_by_field and group_by_field not in rows[0] if rows else False:
        rows = frappe.get_all(
            layer_doc.source_doctype,
            fields=fields,
            filters=filters,
            limit=limit,
            limit_start=offset,
            order_by=f"{layer_doc.latitude_field} asc",
        )

    for r in rows:
        lat = r.get(layer_doc.latitude_field)
        lng = r.get(layer_doc.longitude_field)
        if lat is None or lng is None:
            continue
        # Strip the lat/lng from properties; they're in geometry.
        props = {
            k: v
            for k, v in r.items()
            if k not in (layer_doc.latitude_field, layer_doc.longitude_field)
        }
        props["_doctype"] = layer_doc.source_doctype
        props["_name"] = r.get("name")
        props["_label"] = (
            r.get(layer_doc.label_field) if layer_doc.label_field else r.get("name")
        )

        # Resolve per-feature group style (segmentation).
        group_value = None
        if group_by_field:
            group_value = r.get(group_by_field)
            props["_group_value"] = group_value
            override = (
                group_config.get(str(group_value)) if group_value is not None else None
            )
            if override:
                if override.get("color"):
                    props["_color"] = override["color"]
                if override.get("icon"):
                    props["_icon"] = override["icon"]

        # Render the Jinja popup_template against the source row. The
        # rendered HTML is attached as `_popup_html`. `safe_render=True`
        # blocks `.__` attribute traversal (Frappe default), so users
        # cannot reach into Python objects via crafted templates.
        if popup_template:
            try:
                ctx = _full_row_context(layer_doc.source_doctype, r.get("name"), r)
                ctx["layer"] = {
                    "title": layer_doc.title,
                    "name": layer_doc.name,
                }
                props["_popup_html"] = frappe.render_template(popup_template, ctx)
            except (
                frappe.exceptions.ValidationError,
                frappe.exceptions.SecurityException,
            ):
                # Bad template (illegal attribute access, etc.) is a
                # configuration error on the Layer doc — surface it to
                # the caller so the layer admin can fix it.
                raise
            except Exception:
                # Per-feature render failure (e.g. transient DB error
                # loading a row's full context) should not 500 the
                # whole layer fetch. Drop this feature's popup; the
                # client falls back to the property-row table.
                props["_popup_html"] = ""

        # Compute default popup rows from popup_fields_json (used when
        # no popup_template is set). The client uses this to render the
        # table without an extra round-trip.
        if popup_fields and not popup_template:
            props["_popup_fields"] = popup_fields

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": props,
            }
        )

    return {
        "type": "FeatureCollection",
        "features": features,
        "total": total,
        "truncated": total > offset + len(features),
        "layer": {
            "name": layer_doc.name,
            "title": layer_doc.title,
            "source_doctype": layer_doc.source_doctype,
            "click_action": layer_doc.click_action or "popup",
            "group_by_field": group_by_field,
            "group_config": group_config,
            "popup_fields": popup_fields,
            "style": {
                "color": layer_doc.color,
                "icon": layer_doc.icon,
                "size": layer_doc.size,
                "cluster": layer_doc.cluster,
                "heatmap": layer_doc.heatmap,
                "stroke_color": layer_doc.stroke_color,
                "stroke_width": layer_doc.stroke_width,
                "fill_opacity": layer_doc.fill_opacity,
            },
        },
    }


def _empty_bounds() -> dict:
    return {"south": 0, "west": 0, "north": 0, "east": 0, "count": 0}


def _filter_hash(layer_doc) -> str:
    """
    Hash the parts of a Layer that change the bounds envelope. Used as
    the cache-key suffix so a filter / field / source change auto-busts
    the cached bounds without explicit invalidation.
    """
    h = hashlib.sha1()
    h.update((layer_doc.source_doctype or "").encode("utf-8"))
    h.update(b"|")
    h.update((layer_doc.latitude_field or "").encode("utf-8"))
    h.update(b"|")
    h.update((layer_doc.longitude_field or "").encode("utf-8"))
    h.update(b"|")
    h.update((layer_doc.filter_json or "").encode("utf-8"))
    return h.hexdigest()[:12]


@frappe.whitelist(allow_guest=True)
def get_layer_bounds(layer: str) -> dict:
    """
    Return the lat/lng envelope of every row in the layer's source
    DocType (after the layer's filter is applied). Single SQL MIN/MAX
    pair — no row materialisation. Cached server-side for 5 minutes
    keyed by (layer, filter_hash) so a filter change auto-busts.

    Used by the client to fit the map to "all enabled data" without
    pulling the full feature set across the wire. Permission: the same
    `assert_source_read(source_doctype)` gate as `get_features`. We
    cannot apply `permission_query_conditions` to a raw MIN/MAX query
    the way `frappe.get_all` does, so this endpoint returns the
    *unrestricted* envelope for the source DocType — the client
    re-applies its own per-layer permission check via `assert_source_read`
    on the way in.
    """
    layer_doc = frappe.get_doc("Expedition Layer", layer)
    if not layer_doc.enabled:
        return _empty_bounds()
    assert_source_read(layer_doc.source_doctype)

    cache_key = f"expedition:layer_bounds:{layer_doc.name}:{_filter_hash(layer_doc)}"
    cached = frappe.cache.get_value(cache_key)
    if cached:
        return cached

    filters = _coerce_filter(layer_doc.filter_json) or []
    lat_f, lng_f = layer_doc.latitude_field, layer_doc.longitude_field

    # Single MIN/MAX query. Lat/lng fieldnames are validated at layer
    # save time (Float type), source_doctype is a Link to a real
    # DocType, filter is parsed above — all safe to interpolate.
    # The 5-min TTL covers filter edits: when a user changes a filter
    # and saves, the filter_hash changes → fresh bounds on the next
    # call. The OLD entry under the old hash lingers in cache but
    # is no longer reachable.
    where_clauses = [f"`{lat_f}` is not null", f"`{lng_f}` is not null"]
    where_params: list = []
    if filters:
        # Build WHERE for the user-supplied filter list. We piggy-back
        # on `frappe.db.sql`'s named-style parameter substitution for
        # values; fieldnames are whitelisted via the same escape
        # used elsewhere in this file.
        #
        # NOTE: composing the full Frappe filter spec into a
        # MIN/MAX query (with joins, child-table lookups, etc.) is
        # out of scope. Filters that need joins aren't supported
        # here — they fall back to scanning the feature cache on
        # the client, which already worked before this endpoint.
        for fltr in filters:
            if len(fltr) == 3:
                field, op, val = fltr
            else:
                field, val = fltr
                op = "="
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", field or ""):
                # Skip illegal field names — don't fail the whole call.
                continue
            op_key = _operator_key(op)
            if op in ("=", "=="):
                where_clauses.append(f"`{field}` = %s")
                where_params.append(val)
            elif op_key == "!=":
                where_clauses.append(f"`{field}` != %s")
                where_params.append(val)
            elif op_key in (">", ">=", "<", "<="):
                where_clauses.append(f"`{field}` {op} %s")
                where_params.append(val)
            elif op_key in ("like", "not like"):
                where_clauses.append(f"`{field}` {op_key} %s")
                where_params.append(val)
            elif op_key in ("in", "not in"):
                values = _parse_multi_value(val)
                if values:
                    placeholders = ", ".join(["%s"] * len(values))
                    where_clauses.append(f"`{field}` {op_key} ({placeholders})")
                    where_params.extend(values)
            elif op_key == "between":
                values = _parse_multi_value(val)
                if len(values) == 2:
                    where_clauses.append(f"`{field}` between %s and %s")
                    where_params.extend(values)
            elif op_key == "is":
                is_value = str(val or "").strip().lower()
                if is_value == "set":
                    where_clauses.append(f"`{field}` is not null")
                    where_clauses.append(f"`{field}` != ''")
                elif is_value == "not set":
                    where_clauses.append(f"(`{field}` is null or `{field}` = '')")

    where_sql = " and ".join(where_clauses)
    row = frappe.db.sql(
        f"""
        select min(`{lat_f}`) as south, min(`{lng_f}`) as west,
               max(`{lat_f}`) as north, max(`{lng_f}`) as east,
               count(*) as n
        from `tab{layer_doc.source_doctype}`
        where {where_sql}
        """,
        tuple(where_params),
        as_dict=True,
    )[0]

    n = int(row.get("n") or 0)
    if n == 0:
        bounds = _empty_bounds()
    else:
        bounds = {
            "south": float(row["south"]),
            "west": float(row["west"]),
            "north": float(row["north"]),
            "east": float(row["east"]),
            "count": n,
        }
    frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
    return bounds


# --- CRUD endpoints (slice: layer system & filters) ------------------


@frappe.whitelist()
def list_for_map(map_name: str) -> list[dict]:
    """Return all Expedition Layer rows (enabled or not) for the given map.

    Includes the resolved style so the client can render without a second
    round-trip. Ordered by `sequence` ascending, then modified desc.
    """
    if not frappe.has_permission("Expedition Layer", "read"):
        frappe.throw("Not permitted", frappe.PermissionError)
    if not frappe.has_permission("Expedition Map", "read", doc=map_name):
        frappe.throw("Not permitted to read this map", frappe.PermissionError)

    rows = frappe.get_all(
        "Expedition Layer",
        filters={"map": map_name},
        fields=[
            "name",
            "title",
            "map",
            "source_doctype",
            "latitude_field",
            "longitude_field",
            "label_field",
            "filter_json",
            "group_by_field",
            "group_config_json",
            "popup_template",
            "popup_fields_json",
            "click_action",
            "color",
            "icon",
            "size",
            "cluster",
            "heatmap",
            "stroke_color",
            "stroke_width",
            "fill_opacity",
            "enabled",
            "sequence",
            "radius_enabled",
            "radius_field",
            "radius_meters",
            "radius_opacity",
        ],
        order_by="sequence asc, modified desc",
    )
    out = []
    for r in rows:
        # Parse the group_config_json into a dict for the client
        r["group_config"] = _coerce_group_config(r.get("group_config_json"))
        r["popup_fields"] = _coerce_popup_fields(r.get("popup_fields_json"))
        out.append({**r, "style": _layer_style_dict(r)})
    return out


@frappe.whitelist()
def create(
    map_name: str,
    title: str,
    source_doctype: str,
    latitude_field: str = "latitude",
    longitude_field: str = "longitude",
    label_field: str | None = None,
    color: str | None = None,
    size: str = "m",
    cluster: int = 1,
    enabled: int = 1,
    icon: str | None = None,
    filter_json: str | None = None,
    group_by_field: str | None = None,
    group_config_json: str | None = None,
    popup_template: str | None = None,
    popup_fields_json: str | None = None,
    click_action: str = "popup",
    heatmap: int = 0,
    radius_enabled: int = 0,
    radius_field: str | None = None,
    radius_meters: int = 5000,
    radius_opacity: float = 0.18,
) -> dict:
    """Create a new Expedition Layer attached to a map. Returns the new doc
    (with resolved style) so the client can add it to the in-memory store
    in one round-trip.
    """
    if not frappe.has_permission("Expedition Layer", "create"):
        frappe.throw("Not permitted to create layers", frappe.PermissionError)
    if not frappe.has_permission("Expedition Map", "read", doc=map_name):
        frappe.throw("Not permitted to read this map", frappe.PermissionError)
    assert_source_read(source_doctype)
    _assert_icons_readable(icon, group_config_json)

    # Next sequence for this map
    last_seq = frappe.db.sql(
        "select ifnull(max(sequence), 0) + 1 from `tabExpedition Layer` where map = %s",
        (map_name,),
    )[0][0]

    doc = frappe.new_doc("Expedition Layer")
    doc.update(
        {
            "title": title,
            "map": map_name,
            "source_doctype": source_doctype,
            "latitude_field": latitude_field,
            "longitude_field": longitude_field,
            "label_field": label_field or "",
            "filter_json": filter_json or "",
            "group_by_field": group_by_field or "",
            "group_config_json": group_config_json or "",
            "popup_template": popup_template or "",
            "popup_fields_json": popup_fields_json or "",
            "click_action": click_action or "popup",
            "color": color or "#3B82F6",
            "size": size,
            "cluster": int(cluster),
            "enabled": int(enabled),
            "icon": icon or "",
            "sequence": int(last_seq),
            "use_source_permissions": 1,
            "heatmap": int(heatmap),
            "radius_enabled": int(radius_enabled),
            "radius_field": radius_field or "",
            "radius_meters": int(radius_meters),
            "radius_opacity": float(radius_opacity),
        }
    )
    doc.insert(ignore_permissions=True)
    return _layer_to_dto(doc)


@frappe.whitelist()
def update(layer_name: str, **fields) -> dict:
    """Update an Expedition Layer. Pass any subset of editable fields.

    Editable: title, color, size, cluster, enabled, label_field,
    latitude_field, longitude_field, filter_json, icon, heatmap,
    stroke_color, stroke_width, fill_opacity, sequence.
    """
    if not frappe.has_permission("Expedition Layer", "write", doc=layer_name):
        frappe.throw("Not permitted to update this layer", frappe.PermissionError)

    doc = frappe.get_doc("Expedition Layer", layer_name)
    _assert_icons_readable(fields.get("icon"), fields.get("group_config_json"))

    allowed = {
        "title",
        "color",
        "size",
        "cluster",
        "enabled",
        "label_field",
        "latitude_field",
        "longitude_field",
        "filter_json",
        "group_by_field",
        "group_config_json",
        "popup_template",
        "popup_fields_json",
        "click_action",
        "icon",
        "heatmap",
        "stroke_color",
        "stroke_width",
        "fill_opacity",
        "sequence",
        "radius_enabled",
        "radius_field",
        "radius_meters",
        "radius_opacity",
    }
    changed = False
    for k, v in fields.items():
        if k in allowed:
            setattr(doc, k, v)
            changed = True
    if changed:
        doc.save(ignore_permissions=True)
    return _layer_to_dto(doc)


@frappe.whitelist()
def delete(layer_name: str) -> dict:
    """Delete an Expedition Layer."""
    if not frappe.has_permission("Expedition Layer", "delete", doc=layer_name):
        frappe.throw("Not permitted to delete this layer", frappe.PermissionError)
    frappe.delete_doc("Expedition Layer", layer_name, ignore_permissions=True)
    return {"deleted": layer_name}


@frappe.whitelist()
def reorder(map_name: str, layer_names: list[str]) -> dict:
    """Persist a new ordering. layer_names is the desired sequence from top
    to bottom. Layer docs not in the list get a sequence past the end.
    """
    if not frappe.has_permission("Expedition Layer", "write"):
        frappe.throw("Not permitted", frappe.PermissionError)
    if not frappe.has_permission("Expedition Map", "read", doc=map_name):
        frappe.throw("Not permitted", frappe.PermissionError)
    seq = 1
    for n in layer_names:
        if not n:
            continue
        if frappe.db.get_value("Expedition Layer", n, "map") != map_name:
            continue
        frappe.db.set_value(
            "Expedition Layer", n, "sequence", seq, update_modified=False
        )
        seq += 1
    return {"ok": True}


@frappe.whitelist()
def get_filter_schema(source_doctype: str) -> dict:
    """Return filterable field metadata for a source DocType.

    This intentionally mirrors the information Frappe's list/report filters
    need: field labels, fieldtypes, options, and the operator set that makes
    sense for each field. The client still serializes to the existing
    Frappe-style `filter_json` list.
    """
    assert_source_read(source_doctype)
    fields = list(_filter_field_map(source_doctype).values())
    fields.sort(key=lambda f: (0 if f.get("standard") else 1, f.get("label") or f["fieldname"]))
    for f in fields:
        if f.get("fieldtype") == "Select":
            f["select_options"] = _select_filter_options(f)
        elif f.get("fieldtype") == "Check":
            f["select_options"] = [
                {"label": "Yes", "value": 1},
                {"label": "No", "value": 0},
            ]
    return {"doctype": source_doctype, "fields": fields}


@frappe.whitelist()
def list_source_fields(source_doctype: str) -> list[dict]:
    """Backward-compatible field list for existing callers."""
    return get_filter_schema(source_doctype).get("fields", [])


@frappe.whitelist()
def get_filter_value_options(
    source_doctype: str,
    field: str,
    txt: str | None = "",
    limit: int = 20,
) -> dict:
    """Return autocomplete options for a filter value field."""
    assert_source_read(source_doctype)
    field_map = _filter_field_map(source_doctype)
    if field not in field_map:
        frappe.throw(
            f"Field '{field}' is not filterable on {source_doctype}",
            frappe.ValidationError,
        )
    field_meta = field_map[field]
    fieldtype = field_meta.get("fieldtype")
    limit = min(max(int(limit or 20), 1), 50)
    txt = str(txt or "")

    if fieldtype == "Check":
        return {
            "options": [
                {"label": "Yes", "value": 1},
                {"label": "No", "value": 0},
            ],
            "truncated": False,
        }

    if fieldtype == "Select":
        values = _select_filter_options(field_meta)
        if txt:
            needle = txt.lower()
            values = [
                v for v in values
                if needle in str(v.get("label") if isinstance(v, dict) else v).lower()
            ]
        return {
            "options": [
                v if isinstance(v, dict) else {"label": v, "value": v}
                for v in values[:limit]
            ],
            "truncated": len(values) > limit,
        }

    if fieldtype == "Link" and field_meta.get("options"):
        link_doctype = field_meta.get("options")
        try:
            from frappe.desk.search import search_link

            rows = search_link(
                doctype=link_doctype,
                txt=txt,
                page_length=limit,
                ignore_user_permissions=False,
            )
            options = []
            for row in rows or []:
                value = row.get("value") if isinstance(row, dict) else row[0]
                label = row.get("description") if isinstance(row, dict) else ""
                options.append({"label": label or value, "value": value})
            return {"options": options, "truncated": len(options) >= limit}
        except Exception:
            if not frappe.has_permission(link_doctype, "read"):
                return {"options": [], "truncated": False}
            rows = frappe.get_list(
                link_doctype,
                filters=[["name", "like", f"%{txt}%"]] if txt else None,
                fields=["name"],
                limit_page_length=limit,
                order_by="name asc",
            )
            return {
                "options": [{"label": r.name, "value": r.name} for r in rows],
                "truncated": len(rows) >= limit,
            }

    # For Data/Text-like fields, return distinct existing values as hints.
    try:
        filters = [[field, "like", f"%{txt}%"]] if txt else None
        rows = frappe.get_list(
            source_doctype,
            fields=[field],
            filters=filters,
            distinct=True,
            order_by=f"{field} asc",
            limit_page_length=limit,
        )
    except Exception:
        rows = []
    values = [r.get(field) for r in rows if r.get(field) not in (None, "")]
    return {
        "options": [{"label": str(v), "value": v} for v in values],
        "truncated": len(values) >= limit,
    }


@frappe.whitelist()
def list_group_values(source_doctype: str, field: str, limit: int = 50) -> dict:
    """Return the distinct values of `field` on `source_doctype`.

    Used by the layer editor to populate the per-value color/icon grid
    when "Group By" is set. `limit` caps the number of distinct values
    to keep the UI bounded (50 is plenty for typical segmentation).
    """
    assert_source_read(source_doctype)
    if not field or not source_doctype:
        return {"values": [], "truncated": False}
    # Use frappe.qb.distinct so we get one row per value. For Link fields
    # this returns the link name (the value the user filters by); for
    # Select/Data it returns the raw value. ORDER BY so the UI list is
    # deterministic.
    try:
        rows = frappe.get_all(
            source_doctype,
            fields=[field],
            distinct=True,
            order_by=f"{field} asc",
            limit_page_length=min(int(limit), 200),
        )
    except Exception:
        # Bad field (deleted, virtual, etc.) — surface empty.
        return {"values": [], "truncated": False}
    values = [r.get(field) for r in rows if r.get(field) is not None]
    return {"values": values, "truncated": len(rows) >= int(limit)}


@frappe.whitelist()
def list_source_doctypes() -> list[dict]:
    """Return DocTypes the current user can read AND that have at least one
    Float field. Used by the 'add layer' picker. Strict: server enforces
    permission.
    """
    out = []
    for dt in frappe.get_all(
        "DocType",
        filters={"istable": 0, "issingle": 0, "is_virtual": 0},
        fields=["name", "module"],
        order_by="name asc",
        limit_page_length=0,
    ):
        try:
            meta = frappe.get_meta(dt.name)
        except Exception:
            continue
        has_lat = any(
            f.fieldname in ("latitude",) and f.fieldtype == "Float" for f in meta.fields
        )
        has_lng = any(
            f.fieldname in ("longitude",) and f.fieldtype == "Float"
            for f in meta.fields
        )
        if not (has_lat and has_lng):
            continue
        if not frappe.has_permission(dt.name, "read"):
            continue
        # Skip internal/system doctypes
        if dt.name.startswith("__"):
            continue
        out.append({"name": dt.name, "module": dt.module})
    return out


def _layer_style_dict(layer: dict) -> dict:
    return {
        "color": layer.get("color"),
        "icon": layer.get("icon"),
        "size": layer.get("size"),
        "cluster": layer.get("cluster"),
        "heatmap": layer.get("heatmap"),
        "stroke_color": layer.get("stroke_color"),
        "stroke_width": layer.get("stroke_width"),
        "fill_opacity": layer.get("fill_opacity"),
    }


def _layer_to_dto(doc) -> dict:
    return {
        "name": doc.name,
        "title": doc.title,
        "map": doc.map,
        "source_doctype": doc.source_doctype,
        "latitude_field": doc.latitude_field,
        "longitude_field": doc.longitude_field,
        "label_field": doc.label_field,
        "filter_json": doc.filter_json,
        "group_by_field": doc.group_by_field or "",
        "group_config": _coerce_group_config(doc.group_config_json),
        "group_config_json": doc.group_config_json or "",
        "popup_template": doc.popup_template or "",
        "popup_fields": _coerce_popup_fields(doc.popup_fields_json),
        "popup_fields_json": doc.popup_fields_json or "",
        "click_action": doc.click_action or "popup",
        "color": doc.color,
        "icon": doc.icon,
        "size": doc.size,
        "cluster": doc.cluster,
        "heatmap": doc.heatmap,
        "stroke_color": doc.stroke_color,
        "stroke_width": doc.stroke_width,
        "fill_opacity": doc.fill_opacity,
        "enabled": doc.enabled,
        "sequence": doc.sequence,
        "radius_enabled": doc.radius_enabled,
        "radius_field": doc.radius_field or "",
        "radius_meters": doc.radius_meters,
        "radius_opacity": doc.radius_opacity,
        "style": _layer_style_dict(
            {
                "color": doc.color,
                "icon": doc.icon,
                "size": doc.size,
                "cluster": doc.cluster,
                "heatmap": doc.heatmap,
                "stroke_color": doc.stroke_color,
                "stroke_width": doc.stroke_width,
                "fill_opacity": doc.fill_opacity,
            }
        ),
    }


# --- Master mappings (slice: master-mapped layer model) ---------------
#
# A "master" is an Expedition Layer row with map=NULL. Masters are reusable
# across maps — they're templates. Attaching a master to a map creates a
# per-map instance row that inherits the master's display fields (color,
# size, icon, etc.). Instances are independent copies (v1 explicit: edits
# to the master do NOT cascade to existing instances).


@frappe.whitelist()
def create_master(
    title: str,
    source_doctype: str,
    latitude_field: str = "latitude",
    longitude_field: str = "longitude",
    label_field: str | None = None,
    color: str | None = None,
    size: str = "m",
    cluster: int = 1,
    enabled: int = 1,
    icon: str | None = None,
    filter_json: str | None = None,
    group_by_field: str | None = None,
    group_config_json: str | None = None,
    popup_template: str | None = None,
    popup_fields_json: str | None = None,
    click_action: str = "popup",
    heatmap: int = 0,
    radius_enabled: int = 0,
    radius_field: str | None = None,
    radius_meters: int = 5000,
    radius_opacity: float = 0.18,
) -> dict:
    """Create an Expedition Layer doc with map=NULL — i.e. a master.

    Returns the new doc as a DTO so the client can add it to its `masters`
    ref in one round-trip.
    """
    if not frappe.has_permission("Expedition Layer", "create"):
        frappe.throw("Not permitted to create layers", frappe.PermissionError)
    assert_source_read(source_doctype)
    _assert_icons_readable(icon, group_config_json)

    doc = frappe.new_doc("Expedition Layer")
    doc.update(
        {
            "title": title,
            "map": "",
            "source_doctype": source_doctype,
            "latitude_field": latitude_field,
            "longitude_field": longitude_field,
            "label_field": label_field or "",
            "color": color or "#3B82F6",
            "size": size,
            "cluster": int(cluster),
            "enabled": int(enabled),
            "icon": icon or "",
            "filter_json": filter_json or "",
            "group_by_field": group_by_field or "",
            "group_config_json": group_config_json or "",
            "popup_template": popup_template or "",
            "popup_fields_json": popup_fields_json or "",
            "click_action": click_action or "popup",
            "use_source_permissions": 1,
            "heatmap": int(heatmap),
            "radius_enabled": int(radius_enabled),
            "radius_field": radius_field or "",
            "radius_meters": int(radius_meters),
            "radius_opacity": float(radius_opacity),
        }
    )
    doc.insert(ignore_permissions=True)
    return _layer_to_dto(doc)


@frappe.whitelist()
def list_masters() -> list[dict]:
    """Return all Expedition Layer rows where map is empty (the masters).

    Ordered by source_doctype asc, title asc so the client can group by
    source_doctype cheaply. Reads through standard Frappe permissions; only
    rows the caller can read are returned.
    """
    if not frappe.has_permission("Expedition Layer", "read"):
        frappe.throw("Not permitted", frappe.PermissionError)

    # Frappe stores empty Links as either '' or NULL depending on the
    # driver, so match both. The IN filter translates cleanly in MySQL.
    raw = frappe.get_all(
        "Expedition Layer",
        filters={"map": ["in", ["", None]]},
        fields=[
            "name",
            "title",
            "map",
            "source_doctype",
            "latitude_field",
            "longitude_field",
            "label_field",
            "filter_json",
            "group_by_field",
            "group_config_json",
            "popup_template",
            "popup_fields_json",
            "click_action",
            "color",
            "icon",
            "size",
            "cluster",
            "heatmap",
            "stroke_color",
            "stroke_width",
            "fill_opacity",
            "enabled",
            "sequence",
        ],
        order_by="source_doctype asc, title asc",
    )
    out = []
    for r in raw:
        r["group_config"] = _coerce_group_config(r.get("group_config_json"))
        r["popup_fields"] = _coerce_popup_fields(r.get("popup_fields_json"))
        out.append({**r, "style": _layer_style_dict(r)})
    return out


@frappe.whitelist()
def attach_to_map(master_name: str, map_name: str) -> dict:
    """Create a per-map instance Expedition Layer that inherits its display
    fields from a master. Idempotent: if an instance already exists on
    this map for the same (title, source_doctype), enables and returns the
    existing instance instead of creating a duplicate.
    """
    if not frappe.has_permission("Expedition Layer", "create"):
        frappe.throw("Not permitted to create layers", frappe.PermissionError)
    if not frappe.has_permission("Expedition Map", "write", doc=map_name):
        frappe.throw("Not permitted to edit this map", frappe.PermissionError)

    master = frappe.get_doc("Expedition Layer", master_name)
    if master.map:
        frappe.throw(
            f"{master_name} is not a master mapping (it already has a map)",
            frappe.ValidationError,
        )
    assert_source_read(master.source_doctype)

    # Idempotency: if an instance with the same (title, source_doctype)
    # already exists on this map, return it.
    existing = frappe.db.get_value(
        "Expedition Layer",
        {
            "map": map_name,
            "source_doctype": master.source_doctype,
            "title": master.title,
        },
        "name",
    )
    if existing:
        doc = frappe.get_doc("Expedition Layer", existing)
        if not doc.enabled:
            doc.enabled = 1
            doc.save(ignore_permissions=True)
        return _layer_to_dto(doc)

    last_seq = frappe.db.sql(
        "select ifnull(max(sequence), 0) + 1 from `tabExpedition Layer` where map = %s",
        (map_name,),
    )[0][0]

    doc = frappe.new_doc("Expedition Layer")
    doc.update(
        {
            "title": master.title,
            "map": map_name,
            "source_doctype": master.source_doctype,
            "latitude_field": master.latitude_field,
            "longitude_field": master.longitude_field,
            "label_field": master.label_field,
            "color": master.color,
            "icon": master.icon,
            "size": master.size,
            "cluster": master.cluster,
            "enabled": 1,
            "filter_json": master.filter_json,
            "stroke_color": master.stroke_color,
            "stroke_width": master.stroke_width,
            "fill_opacity": master.fill_opacity,
            "popup_template": master.popup_template,
            "popup_fields_json": master.popup_fields_json,
            "group_by_field": master.group_by_field,
            "group_config_json": master.group_config_json,
            "click_action": master.click_action,
            "use_source_permissions": master.use_source_permissions,
            "sequence": int(last_seq),
        }
    )
    doc.insert(ignore_permissions=True)
    return _layer_to_dto(doc)
