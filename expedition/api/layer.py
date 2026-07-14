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
from datetime import date, datetime, time, timedelta
from typing import Any

import frappe

from expedition.api.icon import assert_icon_readable
from expedition.api.permission import assert_map_read, assert_map_write, assert_source_read


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

GROUP_PALETTE = [
    "#3B82F6",
    "#10B981",
    "#F59E0B",
    "#EC4899",
    "#8B5CF6",
    "#06B6D4",
    "#F97316",
    "#84CC16",
    "#EF4444",
    "#6366F1",
    "#14B8A6",
    "#A855F7",
]
GROUP_PATH_SEPARATOR = "\x1f"


def _auto_group_color(value: Any) -> str:
    digest = hashlib.sha1(str(value).encode("utf-8")).digest()
    return GROUP_PALETTE[digest[0] % len(GROUP_PALETTE)]

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

NUMERIC_FIELD_TYPES = {"Int", "Float", "Currency", "Percent", "Duration", "Rating"}
HEATMAP_RAMP_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

DOCSTATUS_FILTER_OPTIONS = [
    {"label": "Draft", "value": 0},
    {"label": "Submitted", "value": 1},
    {"label": "Cancelled", "value": 2},
]
METRIC_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LINKED_METRIC_AGGREGATES = {"count", "sum", "avg", "min", "max"}
MONEY_DOCTYPE_PRIORITY = [
    "Sales Invoice",
    "Payment Entry",
    "Quotation",
    "Sales Order",
    "Purchase Invoice",
    "Journal Entry",
]
MONEY_FIELD_CANDIDATES = [
    ("outstanding_amount", "Outstanding Amount", "sum"),
    ("grand_total", "Grand Total", "sum"),
    ("rounded_total", "Rounded Total", "sum"),
    ("paid_amount", "Paid Amount", "sum"),
    ("base_grand_total", "Base Grand Total", "sum"),
    ("amount", "Amount", "sum"),
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
            "Filter must be a Frappe filter list (array of [field, op, value] or [child_doctype, field, op, value] tuples)"
        )
    normalized = []
    for f in parsed:
        if not isinstance(f, (list, tuple)) or len(f) not in (2, 3, 4):
            frappe.throw("Malformed filter entry")
        if len(f) == 2:
            field, value = f
            op = "="
            normalized.append([field, op, value])
        elif len(f) == 3:
            field, op, value = f
            op_key = _operator_key(op)
            if op_key in {"in", "not in", "between"}:
                value = _parse_multi_value(value)
            elif op_key in {"like", "not like"} and isinstance(value, str) and "%" not in value:
                value = f"%{value}%"
            normalized.append([field, op, value])
        elif len(f) == 4:
            dt, field, op, value = f
            op_key = _operator_key(op)
            if op_key in {"in", "not in", "between"}:
                value = _parse_multi_value(value)
            elif op_key in {"like", "not like"} and isinstance(value, str) and "%" not in value:
                value = f"%{value}%"
            normalized.append([dt, field, op, value])
    return normalized


def _with_doctype_filters(doctype: str, filters: list | None) -> list[list]:
    """Return explicit Frappe filters shaped as [doctype, field, op, value]."""
    out = []
    for raw in filters or []:
        if len(raw) == 4:
            out.append(list(raw))
        elif len(raw) == 3:
            field, op, value = raw
            out.append([doctype, field, op, value])
        elif len(raw) == 2:
            field, value = raw
            out.append([doctype, field, "=", value])
    return out


def _filter_child_operator(op: str | None, value: Any = None) -> str:
    op_key = _operator_key(op)
    if op_key == "=":
        return "equals"
    if op_key == "!=":
        return "not equals"
    if op_key == ">":
        return "greater than"
    if op_key == ">=":
        return "greater or equal"
    if op_key == "<":
        return "less than"
    if op_key == "<=":
        return "less or equal"
    if op_key == "is":
        return "is"
    return op_key or "equals"


def _sync_filter_child_table_from_json(doc, filter_json: str | dict | None) -> None:
    """Keep the child table aligned with API-authored filter_json.

    Expedition Layer.validate serializes the child table back into
    filter_json. API saves that only set the hidden JSON field would
    otherwise be overwritten by stale child rows.
    """
    _coerce_filter(filter_json)
    parsed = json.loads(filter_json) if isinstance(filter_json, str) and filter_json else filter_json
    rows = parsed if isinstance(parsed, list) else []
    doc.set("filters", [])
    if not rows:
        doc.filter_json = ""
        return

    meta = frappe.get_meta(doc.source_doctype) if doc.source_doctype else None
    serialized = []
    for raw in rows:
        if len(raw) == 2:
            field, value = raw
            op = "="
        else:
            field, op, value = raw
        field_meta = meta.get_field(field) if meta else None
        child = {
            "fieldname": field,
            "label": getattr(field_meta, "label", None) or field,
            "fieldtype": getattr(field_meta, "fieldtype", None) or "",
            "operator": _filter_child_operator(op, value),
            "condition": "AND",
        }
        if _operator_key(op) == "between" and isinstance(value, (list, tuple)):
            child["from_value"] = value[0] if len(value) > 0 else ""
            child["to_value"] = value[1] if len(value) > 1 else ""
            child["value"] = ""
        else:
            child["value"] = ", ".join(map(str, value)) if isinstance(value, list) else value
        doc.append("filters", child)
        serialized.append([field, op, value])
    doc.filter_json = frappe.json.dumps(serialized)


def _operator_key(op: str | None) -> str:
    key = str(op or "=").strip().lower()
    return {
        "equals": "=",
        "equal": "=",
        "not equals": "!=",
        "not equal": "!=",
        "is not": "!=",
        "greater than": ">",
        "greater or equal": ">=",
        "greater than or equal": ">=",
        "less than": "<",
        "less or equal": "<=",
        "less than or equal": "<=",
        "contains": "like",
        "not contains": "not like",
        "is set": "is",
        "is not set": "is",
    }.get(key, key)


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


def _parse_extra_feature_fields(raw: list | str | None, source_doctype: str) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = [part.strip() for part in raw.split(",") if part.strip()]
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return []

    field_map = _filter_field_map(source_doctype)
    out: list[str] = []
    seen = set()
    for item in parsed:
        field = str(item or "").strip()
        if not field or field not in field_map or field in seen:
            continue
        seen.add(field)
        out.append(field)
    return out


def _searchable_text_fields(source_doctype: str) -> list[str]:
    fields = []
    for fieldname, meta in _filter_field_map(source_doctype).items():
        fieldtype = meta.get("fieldtype")
        if fieldtype != "Password":
            fields.append(fieldname)
    return fields


def _value_contains_text(value: Any, needle: str) -> bool:
    if value is None:
        return False
    return needle in str(value).lower()


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
    parent_meta = frappe.get_meta(source_doctype)
    valid_child_doctypes = {
        f.options for f in parent_meta.fields if f.fieldtype == "Table" and f.options
    }
    field_map_cache = {source_doctype: _filter_field_map(source_doctype)}

    for raw in filters:
        child_doctype = None
        if len(raw) == 4:
            child_doctype, field, op, value = raw
            if child_doctype not in valid_child_doctypes:
                frappe.throw(
                    f"Child DocType '{child_doctype}' is not a valid child table for {source_doctype}",
                    frappe.ValidationError,
                )
            if child_doctype not in field_map_cache:
                field_map_cache[child_doctype] = _filter_field_map(child_doctype)
            field_map = field_map_cache[child_doctype]
        elif len(raw) == 3:
            field, op, value = raw
            field_map = field_map_cache[source_doctype]
        elif len(raw) == 2:
            field, value = raw
            op = "="
            field_map = field_map_cache[source_doctype]
        else:
            frappe.throw("Malformed filter entry", frappe.ValidationError)

        if field not in field_map:
            doctype_name = child_doctype if child_doctype else source_doctype
            frappe.throw(
                f"Field '{field}' is not filterable on {doctype_name}",
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


def _coerce_linked_metrics(raw: str | list | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        frappe.throw("Linked Metrics must be valid JSON", frappe.ValidationError)
    if not isinstance(parsed, list):
        frappe.throw("Linked Metrics must be a JSON array", frappe.ValidationError)

    metrics = []
    seen = set()
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            frappe.throw(f"Linked metric #{idx + 1} must be an object", frappe.ValidationError)
        key = str(item.get("key") or "").strip()
        if not key or not METRIC_KEY_RE.match(key):
            frappe.throw(
                f"Linked metric #{idx + 1} needs a key using letters, numbers, and underscores",
                frappe.ValidationError,
            )
        if key in seen:
            frappe.throw(f"Duplicate linked metric key '{key}'", frappe.ValidationError)
        seen.add(key)
        source_doctype = str(
            item.get("source_doctype") or item.get("doctype") or ""
        ).strip()
        link_field = str(item.get("link_field") or "").strip()
        dynamic_link_doctype_field = str(
            item.get("dynamic_link_doctype_field")
            or item.get("doctype_field")
            or item.get("link_doctype_field")
            or ""
        ).strip()
        aggregate = str(item.get("aggregate") or "count").strip().lower()
        field = str(item.get("field") or "").strip()
        label = str(item.get("label") or key).strip()
        filters = item.get("filters") or []
        metrics.append(
            {
                "key": key,
                "label": label,
                "source_doctype": source_doctype,
                "link_field": link_field,
                "dynamic_link_doctype_field": dynamic_link_doctype_field,
                "aggregate": aggregate,
                "field": field,
                "filters": filters,
            }
        )
    return metrics


def _validate_link_field_path(metric_doctype: str, link_field_path: str, source_doctype: str) -> None:
    parts = [p.strip() for p in link_field_path.split(".") if p.strip()]
    if not parts:
        frappe.throw("Invalid empty link field path")

    current_doctype = metric_doctype
    for part in parts:
        meta = frappe.get_meta(current_doctype)
        field = meta.get_field(part)
        if not field:
            frappe.throw(f"Field '{part}' not found on DocType '{current_doctype}' in path '{link_field_path}'")
        if field.fieldtype != "Link" or not field.options:
            frappe.throw(f"Field '{part}' on DocType '{current_doctype}' is not a Link field in path '{link_field_path}'")
        current_doctype = field.options

    if current_doctype != source_doctype:
        frappe.throw(
            f"Link path '{link_field_path}' on {metric_doctype} must resolve to target DocType '{source_doctype}', but resolves to '{current_doctype}'",
            frappe.ValidationError
        )


def _resolve_link_path_mapping(
    source_doctype: str,
    link_field_path: str,
    target_names: list[str]
) -> tuple[str, dict[str, str]]:
    """
    Resolves a dot-separated link path from source_doctype to target_names.
    Returns:
        (first_field, mapping)
        where first_field is the field on source_doctype (e.g. 'customer'),
        and mapping is a dict mapping values of first_field to the corresponding
        name in target_names.
    """
    parts = [p.strip() for p in link_field_path.split(".") if p.strip()]
    if len(parts) <= 1:
        field = link_field_path
        return field, {name: name for name in target_names}

    # Build the chain of (doctype, field, target_doctype)
    chain = []
    current_doctype = source_doctype
    for part in parts:
        meta = frappe.get_meta(current_doctype)
        field = meta.get_field(part)
        if not field:
            frappe.throw(f"Field '{part}' not found on DocType '{current_doctype}' in path '{link_field_path}'")
        if field.fieldtype != "Link" or not field.options:
            frappe.throw(f"Field '{part}' on DocType '{current_doctype}' is not a Link field in path '{link_field_path}'")
        target_dt = field.options
        chain.append((current_doctype, part, target_dt))
        current_doctype = target_dt

    # Traverse back from the end of the chain to build the mapping
    current_mapping = {name: name for name in target_names}

    for i in range(len(chain) - 1, 0, -1):
        step_doctype, step_fieldname, step_target_doctype = chain[i]
        if not current_mapping:
            break
        rows = frappe.get_all(
            step_doctype,
            filters={step_fieldname: ["in", list(current_mapping.keys())]},
            fields=["name", step_fieldname],
            limit_page_length=0
        )
        next_mapping = {}
        for r in rows:
            val = r.get(step_fieldname)
            if val in current_mapping:
                next_mapping[r.name] = current_mapping[val]
        current_mapping = next_mapping

    return chain[0][1], current_mapping


def validate_linked_metrics_json(
    source_doctype: str,
    linked_metrics_json: str | list | None,
) -> None:
    metrics = _coerce_linked_metrics(linked_metrics_json)
    if not metrics:
        return
    assert_source_read(source_doctype)
    for metric in metrics:
        metric_doctype = metric["source_doctype"]
        if not metric_doctype:
            frappe.throw(
                f"Linked metric '{metric['key']}' needs source_doctype",
                frappe.ValidationError,
            )
        assert_source_read(metric_doctype)
        meta = frappe.get_meta(metric_doctype)
        link_field_path = metric["link_field"]
        if "." in link_field_path:
            _validate_link_field_path(metric_doctype, link_field_path, source_doctype)
        else:
            link_field = meta.get_field(link_field_path)
            if not link_field or link_field.fieldtype not in {"Link", "Dynamic Link"}:
                frappe.throw(
                    f"Linked metric '{metric['key']}' needs a Link or Dynamic Link field on {metric_doctype}",
                    frappe.ValidationError,
                )
            if link_field.fieldtype == "Link":
                if link_field.options != source_doctype:
                    frappe.throw(
                        f"Linked metric '{metric['key']}' needs a Link field on {metric_doctype} pointing to {source_doctype}",
                        frappe.ValidationError,
                    )
            else:
                doctype_fieldname = metric.get("dynamic_link_doctype_field") or link_field.options
                doctype_field = meta.get_field(doctype_fieldname)
                if not doctype_field or not doctype_field.fieldname:
                    frappe.throw(
                        f"Linked metric '{metric['key']}' needs a Dynamic Link selector field on {metric_doctype}",
                        frappe.ValidationError,
                    )
                metric["dynamic_link_doctype_field"] = doctype_field.fieldname
                selector_options = {
                    row.strip()
                    for row in str(getattr(doctype_field, "options", "") or "").splitlines()
                    if row.strip()
                }
                if (
                    getattr(doctype_field, "fieldtype", "") == "Select"
                    and selector_options
                    and source_doctype not in selector_options
                ):
                    frappe.throw(
                        f"Linked metric '{metric['key']}' Dynamic Link selector cannot point to {source_doctype}",
                        frappe.ValidationError,
                    )
        aggregate = metric["aggregate"]
        if aggregate not in LINKED_METRIC_AGGREGATES:
            frappe.throw(
                f"Linked metric '{metric['key']}' has unsupported aggregate '{aggregate}'",
                frappe.ValidationError,
            )
        if aggregate != "count":
            field = meta.get_field(metric["field"])
            if not field or field.fieldtype not in NUMERIC_FIELD_TYPES:
                frappe.throw(
                    f"Linked metric '{metric['key']}' needs a numeric field for {aggregate}",
                    frappe.ValidationError,
                )
        _validate_filter_rows(metric_doctype, _coerce_filter(metric["filters"]) or [])


def linked_metric_property_names(linked_metrics_json: str | list | None) -> set[str]:
    return {
        f"_metric_{metric['key']}"
        for metric in _coerce_linked_metrics(linked_metrics_json)
    }


def _coerce_linked_metric_filters(raw: str | list | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        frappe.throw("Linked Metric Filters must be valid JSON", frappe.ValidationError)
    if not isinstance(parsed, list):
        frappe.throw("Linked Metric Filters must be a JSON array", frappe.ValidationError)

    filters = []
    for idx, item in enumerate(parsed):
        if not isinstance(item, dict):
            frappe.throw(
                f"Linked metric filter #{idx + 1} must be an object",
                frappe.ValidationError,
            )
        metric = str(item.get("metric") or item.get("field") or "").strip()
        operator = _operator_key(item.get("operator") or item.get("op") or "=")
        if operator == "==":
            operator = "="
        value = item.get("value")
        if not metric or not METRIC_KEY_RE.match(metric):
            frappe.throw(
                f"Linked metric filter #{idx + 1} needs a valid metric key",
                frappe.ValidationError,
            )
        if operator not in {"=", "!=", ">", ">=", "<", "<=", "between", "in", "not in", "is"}:
            frappe.throw(
                f"Linked metric filter '{metric}' has unsupported operator '{operator}'",
                frappe.ValidationError,
            )
        filters.append({"metric": metric, "operator": operator, "value": value})
    return filters


def validate_linked_metric_filters_json(
    linked_metrics_json: str | list | None,
    linked_metric_filters_json: str | list | None,
) -> None:
    metric_keys = {
        metric["key"] for metric in _coerce_linked_metrics(linked_metrics_json)
    }
    for flt in _coerce_linked_metric_filters(linked_metric_filters_json):
        if flt["metric"] not in metric_keys:
            frappe.throw(
                f"Linked metric filter references unknown metric '{flt['metric']}'",
                frappe.ValidationError,
            )
        op = flt["operator"]
        if op in {"in", "not in"} and not _parse_multi_value(flt["value"]):
            frappe.throw(
                f"Linked metric filter '{flt['metric']} {op}' needs at least one value",
                frappe.ValidationError,
            )
        if op == "between" and len(_parse_multi_value(flt["value"])) != 2:
            frappe.throw(
                f"Linked metric filter '{flt['metric']} between' needs exactly two values",
                frappe.ValidationError,
            )
        if op == "is" and str(flt["value"] or "").strip().lower() not in {"set", "not set"}:
            frappe.throw(
                f"Linked metric filter '{flt['metric']} is' must use set or not set",
                frappe.ValidationError,
            )


def _coerce_metric_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_filter_matches(metric_value: Any, operator: str, expected: Any) -> bool:
    op = _operator_key(operator)
    if op == "==":
        op = "="
    if op == "is":
        is_value = str(expected or "").strip().lower()
        is_set = metric_value not in (None, "")
        return is_set if is_value == "set" else not is_set
    if op in {"in", "not in"}:
        values = _parse_multi_value(expected)
        matched = metric_value in values or str(metric_value) in {str(v) for v in values}
        return matched if op == "in" else not matched
    if op == "between":
        values = _parse_multi_value(expected)
        actual = _coerce_metric_number(metric_value)
        low = _coerce_metric_number(values[0]) if len(values) > 0 else None
        high = _coerce_metric_number(values[1]) if len(values) > 1 else None
        if actual is None or low is None or high is None:
            return False
        return low <= actual <= high
    if op in {">", ">=", "<", "<="}:
        actual = _coerce_metric_number(metric_value)
        target = _coerce_metric_number(expected)
        if actual is None or target is None:
            return False
        if op == ">":
            return actual > target
        if op == ">=":
            return actual >= target
        if op == "<":
            return actual < target
        return actual <= target
    if op == "!=":
        return metric_value != expected and str(metric_value) != str(expected)
    return metric_value == expected or str(metric_value) == str(expected)


def _passes_linked_metric_filters(layer_doc, metrics: dict[str, Any]) -> bool:
    filters = _coerce_linked_metric_filters(
        getattr(layer_doc, "linked_metric_filters_json", "") or ""
    )
    if not filters:
        return True
    for flt in filters:
        if not _metric_filter_matches(
            metrics.get(flt["metric"]),
            flt["operator"],
            flt["value"],
        ):
            return False
    return True


def _aggregate_metric_values(
    aggregate: str,
    values: list[Any],
) -> int | float | None:
    if aggregate == "count":
        return len(values)
    numbers = []
    for value in values:
        if value in (None, ""):
            continue
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numbers:
        return 0 if aggregate in {"sum", "avg"} else None
    if aggregate == "sum":
        return sum(numbers)
    if aggregate == "avg":
        return sum(numbers) / len(numbers)
    if aggregate == "min":
        return min(numbers)
    if aggregate == "max":
        return max(numbers)
    return None


def _linked_metric_dynamic_doctype_field(metric: dict[str, Any]) -> str:
    configured = str(metric.get("dynamic_link_doctype_field") or "").strip()
    if configured:
        return configured
    try:
        meta = frappe.get_meta(metric["source_doctype"])
        first_field = metric["link_field"].split(".")[0]
        link_field = meta.get_field(first_field)
    except Exception:
        return ""
    if link_field and link_field.fieldtype == "Dynamic Link":
        return str(link_field.options or "").strip()
    return ""


def _linked_metrics_for_rows(layer_doc, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metrics = _coerce_linked_metrics(getattr(layer_doc, "linked_metrics_json", "") or "")
    if not metrics or not rows:
        return {}

    source_names = [row.get("name") for row in rows if row.get("name")]
    if not source_names:
        return {}

    out: dict[str, dict[str, Any]] = {name: {} for name in source_names}
    for metric in metrics:
        validate_linked_metrics_json(layer_doc.source_doctype, [metric])
        aggregate = metric["aggregate"]

        link_field_path = metric["link_field"]
        first_field, mapping = _resolve_link_path_mapping(
            metric["source_doctype"],
            link_field_path,
            source_names
        )
        if not mapping:
            for source_name in source_names:
                out[source_name][metric["key"]] = 0 if aggregate == "count" else None
            continue

        metric_fields = [first_field]
        dynamic_doctype_field = _linked_metric_dynamic_doctype_field(metric)
        if dynamic_doctype_field and dynamic_doctype_field not in metric_fields:
            metric_fields.append(dynamic_doctype_field)
        if aggregate != "count":
            metric_fields.append(metric["field"])
        filters = _coerce_filter(metric["filters"]) or []
        if dynamic_doctype_field:
            filters.append([dynamic_doctype_field, "=", layer_doc.source_doctype])
        filters.append([first_field, "in", list(mapping.keys())])
        metric_rows = frappe.get_all(
            metric["source_doctype"],
            fields=metric_fields,
            filters=filters,
            limit_page_length=0,
        )
        bucket: dict[str, list[Any]] = {name: [] for name in source_names}
        for metric_row in metric_rows:
            source_name = mapping.get(metric_row.get(first_field))
            if not source_name or source_name not in bucket:
                continue
            bucket[source_name].append(
                1 if aggregate == "count" else metric_row.get(metric["field"])
            )
        for source_name in source_names:
            out[source_name][metric["key"]] = _aggregate_metric_values(
                aggregate,
                bucket.get(source_name, []),
            )
    return out


LINKED_RECORD_DISPLAY_FIELDS = [
    "status",
    "workflow_state",
    "grand_total",
    "rounded_total",
    "base_grand_total",
    "outstanding_amount",
    "paid_amount",
    "advance_paid",
    "posting_date",
    "transaction_date",
    "due_date",
    "party",
    "customer",
    "lead",
    "quotation_to",
    "currency",
]


def _linked_record_field_meta(meta, fieldname: str) -> dict[str, Any] | None:
    if fieldname in {"name", "modified", "docstatus"}:
        standard = {
            "name": ("ID", "Data"),
            "modified": ("Modified", "Datetime"),
            "docstatus": ("Document Status", "Select"),
        }
        label, fieldtype = standard[fieldname]
        return {"fieldname": fieldname, "label": label, "fieldtype": fieldtype}
    field = meta.get_field(fieldname)
    if not field or not field.fieldname or field.fieldtype in LAYOUT_FIELD_TYPES:
        return None
    return {
        "fieldname": field.fieldname,
        "label": field.label or field.fieldname,
        "fieldtype": field.fieldtype,
        "options": field.options or "",
    }


def _linked_record_fields(metric: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    meta = frappe.get_meta(metric["source_doctype"])
    link_field = metric["link_field"].split(".")[0]
    fields = ["name", "modified", "docstatus", link_field]
    dynamic_doctype_field = _linked_metric_dynamic_doctype_field(metric)
    if dynamic_doctype_field and dynamic_doctype_field not in fields:
        fields.append(dynamic_doctype_field)
    if metric["aggregate"] != "count" and metric["field"] not in fields:
        fields.append(metric["field"])
    for fieldname in LINKED_RECORD_DISPLAY_FIELDS:
        if fieldname not in fields and _linked_record_field_meta(meta, fieldname):
            fields.append(fieldname)

    display_meta = []
    for fieldname in fields:
        meta_row = _linked_record_field_meta(meta, fieldname)
        if meta_row:
            display_meta.append(meta_row)
    return fields, display_meta


def _suggested_linked_record_metrics(source_doctype: str, limit: int = 8) -> list[dict[str, Any]]:
    suggestions = suggest_money_metrics(source_doctype, limit=limit).get("suggestions") or []
    metrics: list[dict[str, Any]] = []
    seen_doctypes: set[str] = set()
    for suggestion in suggestions:
        doctype = suggestion.get("source_doctype")
        link_field = suggestion.get("link_field")
        if not doctype or not link_field or doctype in seen_doctypes:
            continue
        seen_doctypes.add(doctype)
        metrics.append(
            {
                "key": f"suggested_{_metric_key_from_parts(doctype)}",
                "label": doctype,
                "source_doctype": doctype,
                "link_field": link_field,
                "dynamic_link_doctype_field": suggestion.get("dynamic_link_doctype_field") or "",
                "aggregate": "count",
                "field": "",
                "filters": suggestion.get("filters") or [],
                "suggested": True,
            }
        )
    return metrics


LINKED_RECORD_SUMMARY_FIELDS = [
    ("outstanding_amount", "Outstanding"),
    ("paid_amount", "Paid"),
    ("grand_total", "Total"),
    ("rounded_total", "Total"),
    ("base_grand_total", "Base Total"),
    ("advance_paid", "Advance"),
    ("amount", "Amount"),
]


def _linked_record_status(row: dict[str, Any]) -> str:
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


def _linked_record_group_summary(
    rows: list[dict[str, Any]],
    metric: dict[str, Any],
) -> dict[str, Any]:
    totals = []
    seen_fields: set[str] = set()
    field_order = []
    if metric.get("field"):
        field_order.append((metric["field"], metric["field"].replace("_", " ").title()))
    field_order.extend(LINKED_RECORD_SUMMARY_FIELDS)
    for fieldname, label in field_order:
        if not fieldname or fieldname in seen_fields:
            continue
        seen_fields.add(fieldname)
        total = 0.0
        count = 0
        for row in rows:
            value = _coerce_metric_number(row.get(fieldname))
            if value is None:
                continue
            total += value
            count += 1
        if not count:
            continue
        totals.append(
            {
                "field": fieldname,
                "label": label,
                "value": total,
                "count": count,
            }
        )

    statuses: dict[str, int] = {}
    for row in rows:
        status = _linked_record_status(row)
        statuses[status] = statuses.get(status, 0) + 1

    return {
        "row_count": len(rows),
        "totals": totals,
        "statuses": [
            {"label": label, "count": count}
            for label, count in sorted(statuses.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


@frappe.whitelist()
def get_linked_records(
    layer: str,
    source_name: str,
    limit: int = 12,
) -> dict[str, Any]:
    """Return compact linked payment/business rows for a popup source row."""
    layer_doc = frappe.get_doc("Expedition Layer", layer)
    if not layer_doc.enabled:
        return {"groups": [], "total": 0}

    assert_source_read(layer_doc.source_doctype)
    source_name = str(source_name or "").strip()
    if not source_name:
        return {"groups": [], "total": 0}

    source_filters = _coerce_filter(layer_doc.filter_json) or []
    source_filters = _with_doctype_filters(layer_doc.source_doctype, source_filters)
    source_filters.append([layer_doc.source_doctype, "name", "=", source_name])
    source_rows = frappe.get_all(
        layer_doc.source_doctype,
        fields=["name"],
        filters=source_filters,
        limit=1,
    )
    if not source_rows:
        frappe.throw("Source record not found or not readable", frappe.PermissionError)

    metrics = _coerce_linked_metrics(getattr(layer_doc, "linked_metrics_json", "") or "")
    configured = bool(metrics)
    if not metrics:
        metrics = _suggested_linked_record_metrics(layer_doc.source_doctype)
    if not metrics:
        return {"groups": [], "total": 0, "suggested": not configured}

    limit = min(max(int(limit or 12), 1), 50)
    groups = []
    total = 0
    for metric in metrics:
        validate_linked_metrics_json(layer_doc.source_doctype, [metric])

        # Resolve the dot-walked path mapping
        link_field_path = metric["link_field"]
        first_field, mapping = _resolve_link_path_mapping(
            metric["source_doctype"],
            link_field_path,
            [source_name]
        )

        fields, field_meta = _linked_record_fields(metric)

        if not mapping:
            groups.append(
                {
                    "key": metric["key"],
                    "label": metric["label"],
                    "source_doctype": metric["source_doctype"],
                    "aggregate": metric["aggregate"],
                    "field": metric["field"],
                    "link_field": metric["link_field"],
                    "dynamic_link_doctype_field": _linked_metric_dynamic_doctype_field(metric),
                    "fields": field_meta,
                    "rows": [],
                    "summary": _linked_record_group_summary([], metric),
                    "truncated": False,
                    "suggested": bool(metric.get("suggested")),
                }
            )
            continue

        filters = _coerce_filter(metric["filters"]) or []
        dynamic_doctype_field = _linked_metric_dynamic_doctype_field(metric)
        if dynamic_doctype_field:
            filters.append([dynamic_doctype_field, "=", layer_doc.source_doctype])
        filters.append([first_field, "in", list(mapping.keys())])
        rows = frappe.get_all(
            metric["source_doctype"],
            fields=fields,
            filters=filters,
            limit_page_length=limit,
            order_by="modified desc",
        )

        # Map the first_field value back to the original source_name
        for row in rows:
            row[metric["link_field"]] = mapping.get(row.get(first_field))

        total += len(rows)
        groups.append(
            {
                "key": metric["key"],
                "label": metric["label"],
                "source_doctype": metric["source_doctype"],
                "aggregate": metric["aggregate"],
                "field": metric["field"],
                "link_field": metric["link_field"],
                "dynamic_link_doctype_field": dynamic_doctype_field,
                "fields": field_meta,
                "rows": rows,
                "summary": _linked_record_group_summary(rows, metric),
                "truncated": len(rows) >= limit,
                "suggested": bool(metric.get("suggested")),
            }
        )
    return {"groups": groups, "total": total, "suggested": not configured}


def _attach_metrics_to_props(
    props: dict[str, Any],
    source_name: str | None,
    metrics_by_name: dict[str, dict[str, Any]],
) -> None:
    metrics = metrics_by_name.get(source_name or "") or {}
    if not metrics:
        return
    props["_metrics"] = metrics
    for key, value in metrics.items():
        props[f"_metric_{key}"] = value


def _parse_group_config_raw(raw: str | dict | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_group_config(raw: str | dict | None) -> dict:
    """Parse legacy group_config_json. Shape: { "<value>": { color, icon, label } }."""
    parsed = _parse_group_config_raw(raw)
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for value, cfg in parsed.items():
        if str(value).startswith("__"):
            continue
        if not isinstance(cfg, dict):
            continue
        entry = {}
        if isinstance(cfg.get("color"), str) and cfg["color"].startswith("#"):
            entry["color"] = cfg["color"]
        if isinstance(cfg.get("territory_color"), str) and cfg["territory_color"].startswith("#"):
            entry["territory_color"] = cfg["territory_color"]
        if isinstance(cfg.get("icon"), str) and cfg["icon"]:
            entry["icon"] = cfg["icon"]
        if isinstance(cfg.get("label"), str):
            entry["label"] = cfg["label"]
        if entry:
            out[str(value)] = entry
    return out


def _coerce_group_config_for_client(raw: str | dict | None) -> dict:
    parsed = _parse_group_config_raw(raw)
    if not parsed:
        return {}
    grouping = parsed.get("__grouping")
    try:
        version = int(grouping.get("version") or 1) if isinstance(grouping, dict) else 1
    except (TypeError, ValueError):
        version = 1
    if isinstance(grouping, dict) and version >= 2:
        return parsed
    return _coerce_group_config(parsed)


def _coerce_grouping_rules(raw: str | dict | None) -> dict:
    parsed = _parse_group_config_raw(raw)
    rules = parsed.get("__grouping")
    if not isinstance(rules, dict) or rules.get("mode") != "bands":
        return {}
    kind = str(rules.get("kind") or "number").lower()
    if kind not in {"number", "date", "datetime", "time"}:
        kind = "number"
    bands = []
    for idx, band in enumerate(rules.get("bands") or []):
        if not isinstance(band, dict):
            continue
        key = str(band.get("key") or f"band_{idx + 1}")
        item = {"key": key}
        for edge in ("min", "max"):
            value = band.get(edge)
            if value in (None, ""):
                item[edge] = None
                continue
            item[edge] = value
        if item.get("min") is None and item.get("max") is None:
            continue
        if isinstance(band.get("label"), str):
            item["label"] = band["label"]
        if isinstance(band.get("color"), str) and band["color"].startswith("#"):
            item["color"] = band["color"]
        if isinstance(band.get("territory_color"), str) and band["territory_color"].startswith("#"):
            item["territory_color"] = band["territory_color"]
        if isinstance(band.get("icon"), str) and band["icon"]:
            item["icon"] = band["icon"]
        bands.append(item)
    return {"mode": "bands", "kind": kind, "bands": bands} if bands else {}


def _coerce_multi_grouping(raw: str | dict | None) -> dict:
    parsed = _parse_group_config_raw(raw)
    rules = parsed.get("__grouping")
    try:
        version = int(rules.get("version") or 1) if isinstance(rules, dict) else 1
    except (TypeError, ValueError):
        version = 1
    if not isinstance(rules, dict) or version < 2:
        return {}
    levels = []
    seen = set()
    for level in rules.get("levels") or []:
        if not isinstance(level, dict):
            continue
        field = str(level.get("field") or "").strip()
        if not field or field in seen:
            continue
        mode = str(level.get("mode") or "value").strip().lower()
        if mode not in {"value", "bands"}:
            continue
        seen.add(field)
        item = {"field": field, "mode": mode}
        if mode == "bands":
            kind = str(level.get("kind") or "number").lower()
            if kind not in {"number", "date", "datetime", "time"}:
                kind = "number"
            bands = []
            for idx, band in enumerate(level.get("bands") or []):
                if not isinstance(band, dict):
                    continue
                key = str(band.get("key") or f"band_{idx + 1}")
                if not key:
                    continue
                item_band = {"key": key}
                for edge in ("min", "max"):
                    value = band.get(edge)
                    item_band[edge] = None if value in (None, "") else value
                if item_band.get("min") is None and item_band.get("max") is None:
                    continue
                if isinstance(band.get("label"), str):
                    item_band["label"] = band["label"]
                bands.append(item_band)
            if not bands:
                continue
            item["kind"] = kind
            item["bands"] = bands
        levels.append(item)
    if not levels:
        return {}
    groups = parsed.get("groups") if isinstance(parsed.get("groups"), dict) else {}
    return {"version": 2, "levels": levels, "groups": groups}


def _resolve_group_level_value(row: dict[str, Any], level: dict) -> str:
    value = row.get(level["field"])
    if level.get("mode") == "bands":
        group_value, band_override = _resolve_group_band(value, {
            "mode": "bands",
            "kind": level.get("kind") or "number",
            "bands": level.get("bands") or [],
        })
        if band_override and band_override.get("label"):
            return str(band_override["label"])
        return _group_display_value(group_value)
    return _group_display_value(value)


def _group_display_value(value: Any) -> str:
    if value in (None, ""):
        return "(blank)"
    return str(value)


def _group_path_key(values: list[Any]) -> str:
    return GROUP_PATH_SEPARATOR.join(_group_display_value(v) for v in values)


def _resolve_multi_group_style(
    row: dict[str, Any],
    rules: dict,
    layer_color: str | None,
    layer_icon: str | None,
) -> dict:
    if not rules:
        return {}
    levels = rules.get("levels") or []
    groups = rules.get("groups") or {}
    path_values = [_resolve_group_level_value(row, level) for level in levels]
    if not path_values:
        return {}

    effective = {
        "color": layer_color or "",
        "icon": layer_icon or "",
        "label": path_values[-1],
    }
    group_values = {
        level["field"]: path_values[idx]
        for idx, level in enumerate(levels)
    }
    for idx in range(len(path_values)):
        key = _group_path_key(path_values[: idx + 1])
        override = groups.get(key)
        if not isinstance(override, dict):
            continue
        if isinstance(override.get("color"), str) and override.get("color"):
            effective["color"] = override["color"]
        if isinstance(override.get("territory_color"), str) and override.get("territory_color"):
            effective["territory_color"] = override["territory_color"]
        if isinstance(override.get("icon"), str):
            effective["icon"] = override["icon"]
        if isinstance(override.get("label"), str) and override.get("label"):
            effective["label"] = override["label"]

    leaf_key = _group_path_key(path_values)
    return {
        "key": leaf_key,
        "path": path_values,
        "label": " / ".join(path_values),
        "values": group_values,
        "color": effective.get("color") or "",
        "territory_color": effective.get("territory_color") or "",
        "icon": effective.get("icon") or "",
    }


def _band_sort_value(value: Any, kind: str) -> float | str | None:
    if value in (None, ""):
        return None
    try:
        if kind == "number":
            return float(value)
        if kind == "date":
            if isinstance(value, datetime):
                return value.date().isoformat()
            if isinstance(value, date):
                return value.isoformat()
            return date.fromisoformat(str(value)[:10]).isoformat()
        if kind == "datetime":
            if isinstance(value, datetime):
                return value.isoformat(sep=" ")
            return datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            ).isoformat(sep=" ")
        if kind == "time":
            if isinstance(value, timedelta):
                return value.total_seconds()
            if isinstance(value, time):
                return value.isoformat()
            return time.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return None
    return None


def _resolve_group_band(value: Any, rules: dict) -> tuple[str | None, dict | None]:
    if not rules or rules.get("mode") != "bands":
        return None, None
    kind = rules.get("kind") or "number"
    comparable = _band_sort_value(value, kind)
    if comparable is None:
        return None, None
    for band in rules.get("bands") or []:
        min_value = _band_sort_value(band.get("min"), kind)
        max_value = _band_sort_value(band.get("max"), kind)
        if min_value is not None and comparable < min_value:
            continue
        if max_value is not None and comparable >= max_value:
            continue
        return str(band["key"]), {
            k: band[k] for k in ("color", "territory_color", "icon", "label") if band.get(k)
        }
    return "__unmatched", {"label": "Other"}


def _group_filter_for_band(field: str, band: dict, kind: str) -> list[list]:
    filters = []
    if band.get("min") not in (None, ""):
        filters.append([field, ">=", band.get("min")])
    if band.get("max") not in (None, ""):
        filters.append([field, "<", band.get("max")])
    if not filters:
        filters.append([field, "is", "set"])
    return filters


def _is_linked_metric_field(fieldname: str | None) -> bool:
    return str(fieldname or "").startswith("_metric_")


def _linked_metric_key_from_field(fieldname: str | None) -> str:
    text = str(fieldname or "")
    return text[len("_metric_") :] if text.startswith("_metric_") else text


def _band_display_key(band: dict) -> str:
    return str(band.get("label") or _group_display_value(band.get("key")))


def _linked_metric_group_value(
    metrics: dict[str, Any],
    group_by_field: str | None,
) -> Any:
    if not _is_linked_metric_field(group_by_field):
        return None
    return metrics.get(_linked_metric_key_from_field(group_by_field))


def _linked_metric_group_matches(
    metrics: dict[str, Any],
    group_by_field: str | None,
    group_key: str | None,
    group_rules: dict,
) -> bool:
    if not group_key or not _is_linked_metric_field(group_by_field):
        return True
    metric_value = _linked_metric_group_value(metrics, group_by_field)
    if group_rules.get("mode") == "bands":
        resolved_key, _override = _resolve_group_band(metric_value, group_rules)
        return str(resolved_key) == str(group_key)
    if str(group_key) == "(blank)":
        return metric_value in (None, "")
    return str(_group_display_value(metric_value)) == str(group_key)


def _attach_metric_group_values_to_row(
    row: dict[str, Any],
    metrics: dict[str, Any],
    fields: list[str],
) -> None:
    for fieldname in fields:
        if _is_linked_metric_field(fieldname):
            row[fieldname] = _linked_metric_group_value(metrics, fieldname)


def _multi_group_matches(
    row: dict[str, Any],
    metrics: dict[str, Any],
    rules: dict,
    group_key: str | None,
) -> bool:
    if not group_key or not rules:
        return True
    levels = rules.get("levels") or []
    fields = [level["field"] for level in levels]
    _attach_metric_group_values_to_row(row, metrics, fields)
    values = [_resolve_group_level_value(row, level) for level in levels]
    return _group_path_key(values) == str(group_key)


def _group_filters_for_key(
    group_key: str | None,
    group_by_field: str | None,
    group_rules: dict,
    multi_grouping: dict,
) -> list[list]:
    if not group_key:
        return []
    if multi_grouping:
        parts = str(group_key).split(GROUP_PATH_SEPARATOR)
        filters = []
        levels = multi_grouping.get("levels") or []
        for idx, level in enumerate(levels):
            if idx >= len(parts):
                break
            field = level["field"]
            if _is_linked_metric_field(field):
                continue
            value = parts[idx]
            if level.get("mode") == "bands":
                matched = None
                for band in level.get("bands") or []:
                    if _band_display_key(band) == value:
                        matched = band
                        break
                if matched:
                    filters.extend(_group_filter_for_band(field, matched, level.get("kind") or "number"))
            elif value == "(blank)":
                filters.append([field, "in", ["", None]])
            else:
                filters.append([field, "=", value])
        return filters

    if not group_by_field:
        return []
    if _is_linked_metric_field(group_by_field):
        return []
    if group_rules.get("mode") == "bands":
        for band in group_rules.get("bands") or []:
            if str(band.get("key")) == str(group_key):
                return _group_filter_for_band(
                    group_by_field,
                    band,
                    group_rules.get("kind") or "number",
                )
        if str(group_key) == "__unmatched":
            return [["name", "=", "__expedition_no_match__"]]
        return []
    if str(group_key) == "(blank)":
        return [[group_by_field, "in", ["", None]]]
    return [[group_by_field, "=", group_key]]


def _virtual_group_style(
    key: str,
    label: str,
    layer_color: str | None,
    layer_icon: str | None,
    group_config: dict,
) -> dict:
    override = group_config.get(str(key)) if isinstance(group_config, dict) else None
    return {
        "color": (override or {}).get("color") or _auto_group_color(key),
        "territory_color": (override or {}).get("territory_color") or "",
        "icon": (override or {}).get("icon") if override and "icon" in override else (layer_icon or ""),
        "label": (override or {}).get("label") or label,
    }


def _virtual_groups_for_layer(
    layer_doc,
    base_filters: list,
    group_config: dict,
    group_rules: dict,
    multi_grouping: dict,
    group_by_field: str | None,
    limit: int = 1000,
) -> list[dict]:
    if multi_grouping:
        levels = multi_grouping.get("levels") or []
        fields = [level["field"] for level in levels]
        if not fields:
            return []
        db_fields = [field for field in fields if not _is_linked_metric_field(field)]
        uses_metric_fields = any(_is_linked_metric_field(field) for field in fields)
        fetch_fields = ["name", *db_fields]
        order_by = ", ".join(f"{field} asc" for field in db_fields) if db_fields else "name asc"
        rows = frappe.get_all(
            layer_doc.source_doctype,
            fields=fetch_fields,
            filters=base_filters,
            distinct=not uses_metric_fields,
            order_by=order_by,
            limit_page_length=0 if uses_metric_fields else min(max(int(limit or 1000), 1), 5000),
        )
        metrics_by_name = _linked_metrics_for_rows(layer_doc, rows)
        groups = multi_grouping.get("groups") or {}
        seen = set()
        out = []
        for row in rows:
            _attach_metric_group_values_to_row(
                row,
                metrics_by_name.get(row.get("name"), {}),
                fields,
            )
            values = [_resolve_group_level_value(row, level) for level in levels]
            key = _group_path_key(values)
            if key in seen:
                continue
            seen.add(key)
            style = {"color": layer_doc.color or "", "icon": layer_doc.icon or ""}
            label = " / ".join(values)
            for idx in range(len(values)):
                override = groups.get(_group_path_key(values[: idx + 1]))
                if not isinstance(override, dict):
                    continue
                if override.get("color"):
                    style["color"] = override["color"]
                if override.get("territory_color"):
                    style["territory_color"] = override["territory_color"]
                if "icon" in override:
                    style["icon"] = override.get("icon") or ""
                if override.get("label"):
                    label = override["label"]
            out.append({"key": key, "label": label, "style": style})
        return out

    if not group_by_field:
        return []
    if group_rules.get("mode") == "bands":
        out = []
        for band in group_rules.get("bands") or []:
            key = str(band["key"])
            label = str(band.get("label") or key)
            out.append({
                "key": key,
                "label": label,
                "style": {
                    "color": band.get("color") or _auto_group_color(key),
                    "territory_color": band.get("territory_color") or "",
                    "icon": band.get("icon") if "icon" in band else (layer_doc.icon or ""),
                },
            })
        return out
    if _is_linked_metric_field(group_by_field):
        return []

    rows = frappe.get_all(
        layer_doc.source_doctype,
        fields=[group_by_field],
        filters=base_filters,
        distinct=True,
        order_by=f"{group_by_field} asc",
        limit_page_length=min(max(int(limit or 1000), 1), 5000),
    )
    out = []
    seen = set()
    for row in rows:
        raw = row.get(group_by_field)
        key = _group_display_value(raw)
        if key in seen:
            continue
        seen.add(key)
        style = _virtual_group_style(key, key, layer_doc.color, layer_doc.icon, group_config)
        out.append({"key": key, "label": style["label"], "style": {"color": style["color"], "territory_color": style.get("territory_color") or "", "icon": style["icon"]}})
    return out


def _style_for_virtual_group_key(
    group_key: str | None,
    layer_doc,
    group_config: dict,
    group_rules: dict,
    multi_grouping: dict,
) -> dict:
    if not group_key:
        return {"color": layer_doc.color, "territory_color": getattr(layer_doc, "territory_color", "") or "", "icon": layer_doc.icon}
    if multi_grouping:
        parts = str(group_key).split(GROUP_PATH_SEPARATOR)
        groups = multi_grouping.get("groups") or {}
        style = {"color": layer_doc.color or "", "icon": layer_doc.icon or ""}
        for idx in range(len(parts)):
            override = groups.get(_group_path_key(parts[: idx + 1]))
            if not isinstance(override, dict):
                continue
            if override.get("color"):
                style["color"] = override["color"]
            if override.get("territory_color"):
                style["territory_color"] = override["territory_color"]
            if "icon" in override:
                style["icon"] = override.get("icon") or ""
        return style
    if group_rules.get("mode") == "bands":
        for band in group_rules.get("bands") or []:
            if str(band.get("key")) != str(group_key):
                continue
            return {
                "color": band.get("color") or _auto_group_color(group_key),
                "territory_color": band.get("territory_color") or "",
                "icon": band.get("icon") if "icon" in band else (layer_doc.icon or ""),
            }
        return {"color": _auto_group_color(group_key), "territory_color": "", "icon": layer_doc.icon or ""}
    style = _virtual_group_style(
        str(group_key),
        str(group_key),
        layer_doc.color,
        layer_doc.icon,
        group_config,
    )
    return {"color": style["color"], "territory_color": style.get("territory_color") or "", "icon": style["icon"]}


def _assert_icons_readable(icon: str | None = None, group_config_json: str | dict | None = None) -> None:
    assert_icon_readable(icon)
    raw = _parse_group_config_raw(group_config_json)
    groups = raw.get("groups") if isinstance(raw.get("groups"), dict) else {}
    for item in groups.values():
        if isinstance(item, dict) and item.get("icon") != "__none":
            assert_icon_readable(item.get("icon"))
    cfg = _coerce_group_config(group_config_json)
    for item in cfg.values():
        if item.get("icon") != "__none":
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


def _source_field_labels(source_doctype: str) -> dict[str, str]:
    return {
        fieldname: meta.get("label") or fieldname
        for fieldname, meta in _filter_field_map(source_doctype).items()
    }


def _source_assignment_fields(source_doctype: str) -> list[dict[str, str]]:
    fields = []
    standard_user_fields = {"owner", "modified_by"}
    for fieldname, meta in _filter_field_map(source_doctype).items():
        if fieldname in standard_user_fields:
            continue
        if meta.get("fieldtype") == "Link" and meta.get("options") == "User":
            fields.append(
                {
                    "fieldname": fieldname,
                    "label": meta.get("label") or fieldname,
                    "fieldtype": "Link",
                    "options": "User",
                }
            )
    return fields


def _location_source_mode(layer_doc) -> str:
    mode = str(getattr(layer_doc, "location_source", "") or "Direct Fields").strip()
    if mode == "Linked DocType":
        return "linked"
    if mode == "Reverse Linked DocType":
        return "reverse"
    if mode == "Dynamic Link DocType":
        return "dynamic"
    return "direct"


def _linked_location_config(layer_doc) -> dict[str, str]:
    """Return validated single-hop linked location config for a layer.

    MVP scope: direct `Link` fields only. Reverse links, Dynamic Link child
    tables, and one-to-many aggregations can build on this shape later.
    """
    if _location_source_mode(layer_doc) != "linked":
        return {}

    source_meta = frappe.get_meta(layer_doc.source_doctype)
    link_fieldname = (getattr(layer_doc, "location_link_field", "") or "").strip()
    link_field = source_meta.get_field(link_fieldname)
    if not link_field or link_field.fieldtype != "Link" or not link_field.options:
        frappe.throw(
            f"Source DocType {layer_doc.source_doctype} has no Link field '{link_fieldname}'",
            frappe.ValidationError,
        )

    location_doctype = (getattr(layer_doc, "location_doctype", "") or link_field.options).strip()
    if location_doctype != link_field.options:
        frappe.throw(
            f"Location DocType must match {link_fieldname}'s target DocType ({link_field.options})",
            frappe.ValidationError,
        )

    return {
        "mode": "linked",
        "link_field": link_fieldname,
        "location_doctype": location_doctype,
        "latitude_field": layer_doc.latitude_field,
        "longitude_field": layer_doc.longitude_field,
    }


def _reverse_location_config(layer_doc) -> dict[str, str]:
    if _location_source_mode(layer_doc) != "reverse":
        return {}

    location_doctype = (getattr(layer_doc, "location_doctype", "") or "").strip()
    reverse_fieldname = (
        getattr(layer_doc, "location_reverse_link_field", "") or ""
    ).strip()
    if not location_doctype:
        frappe.throw(
            "Location DocType is required for reverse linked locations",
            frappe.ValidationError,
        )
    location_meta = frappe.get_meta(location_doctype)
    reverse_field = location_meta.get_field(reverse_fieldname)
    if (
        not reverse_field
        or reverse_field.fieldtype != "Link"
        or reverse_field.options != layer_doc.source_doctype
    ):
        frappe.throw(
            f"Location DocType {location_doctype} has no Link field '{reverse_fieldname}' pointing to {layer_doc.source_doctype}",
            frappe.ValidationError,
        )
    return {
        "mode": "reverse",
        "reverse_link_field": reverse_fieldname,
        "location_doctype": location_doctype,
        "latitude_field": layer_doc.latitude_field,
        "longitude_field": layer_doc.longitude_field,
    }


def _dynamic_location_config(layer_doc) -> dict[str, str]:
    if _location_source_mode(layer_doc) != "dynamic":
        return {}

    location_doctype = (getattr(layer_doc, "location_doctype", "") or "").strip()
    if not location_doctype:
        frappe.throw(
            "Location DocType is required for dynamic linked locations",
            frappe.ValidationError,
        )
    if not frappe.db.exists("DocType", "Dynamic Link"):
        frappe.throw(
            "Dynamic Link DocType is not available on this site",
            frappe.ValidationError,
        )
    return {
        "mode": "dynamic",
        "location_doctype": location_doctype,
        "latitude_field": layer_doc.latitude_field,
        "longitude_field": layer_doc.longitude_field,
    }


def _location_config(layer_doc) -> dict[str, str]:
    return (
        _linked_location_config(layer_doc)
        or _reverse_location_config(layer_doc)
        or _dynamic_location_config(layer_doc)
    )


def _assert_location_read(layer_doc) -> None:
    cfg = _location_config(layer_doc)
    if cfg:
        assert_source_read(cfg["location_doctype"])


def _validate_location_coordinate_fields(layer_doc) -> None:
    target_doctype = layer_doc.source_doctype
    cfg = _location_config(layer_doc)
    if cfg:
        target_doctype = cfg["location_doctype"]
    meta = frappe.get_meta(target_doctype)
    for fieldname, label in (
        (layer_doc.latitude_field, "Latitude Field"),
        (layer_doc.longitude_field, "Longitude Field"),
    ):
        field = meta.get_field(fieldname)
        if not field or field.fieldtype != "Float":
            frappe.throw(
                f"{label} must be a Float field on {target_doctype}",
                frappe.ValidationError,
            )


def _coerce_location_fields(raw: str | list | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        frappe.throw("Location Fields must be valid JSON", frappe.ValidationError)
    if not isinstance(parsed, list):
        frappe.throw("Location Fields must be a JSON array", frappe.ValidationError)
    out = []
    seen = set()
    for item in parsed:
        fieldname = str(item or "").strip()
        if not fieldname or fieldname in seen:
            continue
        seen.add(fieldname)
        out.append(fieldname)
    return out


def _location_doctype_for_layer(layer_doc) -> str:
    cfg = _location_config(layer_doc)
    return cfg.get("location_doctype") if cfg else layer_doc.source_doctype


def validate_location_fields_json(layer_doc, location_fields_json: str | list | None) -> None:
    fields = _coerce_location_fields(location_fields_json)
    if not fields:
        return
    location_doctype = _location_doctype_for_layer(layer_doc)
    meta = frappe.get_meta(location_doctype)
    for fieldname in fields:
        field = meta.get_field(fieldname)
        if not field or field.fieldtype in LAYOUT_FIELD_TYPES:
            frappe.throw(
                f"Location field '{fieldname}' is not readable on {location_doctype}",
                frappe.ValidationError,
            )


def _valid_location_fields(layer_doc) -> list[str]:
    fields = _coerce_location_fields(getattr(layer_doc, "location_fields_json", "") or "")
    if not fields:
        return []
    validate_location_fields_json(layer_doc, fields)
    return fields


def _attach_location_to_props(
    props: dict[str, Any],
    location_row: dict[str, Any] | None,
    lat_field: str,
    lng_field: str,
) -> None:
    if not location_row:
        return
    location = {
        k: v
        for k, v in location_row.items()
        if k not in {lat_field, lng_field}
    }
    if not location:
        return
    props["_location"] = location
    for key, value in location.items():
        if key == "name":
            continue
        props[f"_location_{key}"] = value


def _append_valid_source_fields(
    fields: list[str],
    source_doctype: str,
    requested: list[str],
) -> None:
    field_map = _filter_field_map(source_doctype)
    for raw in requested:
        fieldname = str(raw or "").strip()
        if not fieldname or fieldname in fields:
            continue
        if fieldname in field_map:
            fields.append(fieldname)


def _default_popup_fields(
    source_doctype: str,
    label_field: str | None = None,
) -> list[str]:
    field_map = _filter_field_map(source_doctype)
    preferred = [
        "status",
        "workflow_state",
        "owner",
        "modified",
        "creation",
        "modified_by",
        "docstatus",
    ]
    out = []
    for fieldname in preferred:
        if fieldname in field_map and fieldname != label_field:
            out.append(fieldname)
    if not out and label_field and label_field in field_map:
        out.append(label_field)
    return out[:6]


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


def _render_popup_template(template: str, ctx: dict[str, Any]) -> str:
    html = frappe.render_template(template, ctx)
    return frappe.utils.sanitize_html(html, always_sanitize=True)


@frappe.whitelist(allow_guest=True)
def get_text_search_matches(
    layer: str,
    text: str,
    bounds: dict | None = None,
    limit: int = 10000,
) -> dict:
    """Return source row names whose searchable fields contain text."""
    query = str(text or "").strip().lower()
    if not query:
        return {"names": [], "truncated": False}

    layer_doc = frappe.get_doc("Expedition Layer", layer)
    if not layer_doc.enabled:
        return {"names": [], "truncated": False}

    assert_source_read(layer_doc.source_doctype)
    _assert_location_read(layer_doc)
    filters = _coerce_filter(layer_doc.filter_json) or []
    if bounds and _location_source_mode(layer_doc) == "direct":
        south = float(bounds.get("south"))
        west = float(bounds.get("west"))
        north = float(bounds.get("north"))
        east = float(bounds.get("east"))
        filters += [
            [layer_doc.latitude_field, ">=", south],
            [layer_doc.latitude_field, "<=", north],
            [layer_doc.longitude_field, ">=", west],
            [layer_doc.longitude_field, "<=", east],
            [layer_doc.latitude_field, "is", "set"],
            [layer_doc.longitude_field, "is", "set"],
        ]

    or_filters = [
        [field, "like", f"%{query}%"]
        for field in _searchable_text_fields(layer_doc.source_doctype)
    ]
    limit = min(max(int(limit or 10000), 1), 10000)
    rows = frappe.get_all(
        layer_doc.source_doctype,
        fields=["name"],
        filters=filters,
        or_filters=or_filters,
        limit=limit + 1,
        order_by=(
            f"{layer_doc.latitude_field} asc"
            if _location_source_mode(layer_doc) == "direct"
            else "name asc"
        ),
    )
    names = [row.get("name") for row in rows[:limit]]
    return {"names": [name for name in names if name], "truncated": len(rows) > limit}


def _get_features_from_python_script(layer_doc, bounds, limit, offset, render_popup):
    """Execute a python script to return layer data, and format it as GeoJSON."""
    context = {
        "bounds": bounds,
        "limit": limit,
        "offset": offset,
        "frappe": frappe,
    }
    
    script_output = frappe.safe_eval(
        layer_doc.python_script or "",
        eval_globals={"frappe": frappe, "context": context},
        eval_locals=context,
    )
    
    if not isinstance(script_output, list):
        script_output = []
        
    features = []
    total = len(script_output)
    
    limit = min(int(limit or 5000), 10000)
    offset = max(int(offset or 0), 0)
    sliced_output = script_output[offset : offset + limit]
    
    for item in sliced_output:
        if not isinstance(item, dict):
            continue
        
        geom = item.get("geometry")
        if not geom:
            lat = item.get("latitude")
            lng = item.get("longitude")
            if lat is not None and lng is not None:
                geom = {"type": "Point", "coordinates": [float(lng), float(lat)]}
                
        if not geom:
            continue
            
        props = item.get("properties") or {}
        if not isinstance(props, dict):
            props = {"value": props}
            
        props["_name"] = item.get("id") or item.get("name") or "custom_pin"
        props["_doctype"] = layer_doc.name
        
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": props
        })
        
    response_style = {
        "color": layer_doc.color,
        "icon": layer_doc.icon,
        "size": layer_doc.size,
        "pin_min_zoom": getattr(layer_doc, "pin_min_zoom", 0) or 0,
        "cluster": layer_doc.cluster,
        "heatmap": layer_doc.heatmap,
        "territory_enabled": getattr(layer_doc, "territory_enabled", 0),
        "territory_color": getattr(layer_doc, "territory_color", "") or "",
        "territory_opacity": getattr(layer_doc, "territory_opacity", None),
        "territory_padding_meters": getattr(layer_doc, "territory_padding_meters", None),
        "stroke_color": layer_doc.stroke_color,
        "stroke_width": layer_doc.stroke_width,
        "fill_opacity": layer_doc.fill_opacity,
    }
    
    return {
        "type": "FeatureCollection",
        "features": features,
        "total": total,
        "truncated": total > offset + len(features),
        "layer": {
            "name": layer_doc.name,
            "title": layer_doc.title,
            "click_action": layer_doc.click_action or "popup",
            "style": response_style,
        }
    }


@frappe.whitelist(allow_guest=True)
def get_features(
    layer: str,
    bounds: dict | None = None,
    group_key: str | None = None,
    extra_fields: list | str | None = None,
    limit: int = 5000,
    offset: int = 0,
    render_popup: bool | int | str = True,
) -> dict:
    """Return a GeoJSON FeatureCollection for the given Expedition Layer."""
    layer_doc = frappe.get_doc("Expedition Layer", layer)
    render_popup = str(render_popup).lower() not in {"0", "false", "no"}
    if not layer_doc.enabled:
        return {
            "type": "FeatureCollection",
            "features": [],
            "total": 0,
            "truncated": False,
        }

    data_source_type = getattr(layer_doc, "data_source_type", "DocType")
    if data_source_type == "Python Script":
        if layer_doc.map:
            from expedition.api.permission import assert_map_read
            assert_map_read(layer_doc.map)
        return _get_features_from_python_script(layer_doc, bounds, limit, offset, render_popup)
    elif data_source_type == "Client Script (JS)":
        return {
            "type": "FeatureCollection",
            "features": [],
            "total": 0,
            "truncated": False,
            "layer": {
                "name": layer_doc.name,
                "title": layer_doc.title,
                "click_action": layer_doc.click_action or "popup",
                "style": {
                    "color": layer_doc.color,
                    "icon": layer_doc.icon,
                    "size": layer_doc.size,
                    "pin_min_zoom": getattr(layer_doc, "pin_min_zoom", 0) or 0,
                    "cluster": layer_doc.cluster,
                    "heatmap": layer_doc.heatmap,
                    "stroke_color": layer_doc.stroke_color,
                    "stroke_width": layer_doc.stroke_width,
                    "fill_opacity": layer_doc.fill_opacity,
                }
            }
        }

    assert_source_read(layer_doc.source_doctype)
    _assert_location_read(layer_doc)

    filters = _coerce_filter(layer_doc.filter_json) or []
    base_filters = list(filters)
    group_config = _coerce_group_config(layer_doc.group_config_json)
    group_rules = _coerce_grouping_rules(layer_doc.group_config_json)
    multi_grouping = _coerce_multi_grouping(layer_doc.group_config_json)
    group_by_field = (layer_doc.group_by_field or "").strip() or None
    grouping_fields = []
    if multi_grouping:
        grouping_fields = [level["field"] for level in multi_grouping.get("levels") or []]
    elif group_by_field:
        grouping_fields = [group_by_field]
    metric_grouping_active = any(_is_linked_metric_field(field) for field in grouping_fields)
    metric_post_filtering = bool(
        _coerce_linked_metric_filters(getattr(layer_doc, "linked_metric_filters_json", "") or "")
    ) or bool(group_key and metric_grouping_active)

    if (multi_grouping or group_by_field) and not group_key:
        popup_fields = _coerce_popup_fields(layer_doc.popup_fields_json)
        default_popup_fields = (
            []
            if popup_fields or (layer_doc.popup_template or "").strip()
            else _default_popup_fields(layer_doc.source_doctype, layer_doc.label_field)
        )
        return {
            "type": "FeatureCollection",
            "features": [],
            "total": 0,
            "truncated": False,
            "virtual_groups": _virtual_groups_for_layer(
                layer_doc,
                base_filters,
                group_config,
                group_rules,
                multi_grouping,
                group_by_field,
            ),
            "layer": {
                "name": layer_doc.name,
                "title": layer_doc.title,
                "source_doctype": layer_doc.source_doctype,
                "location_source": getattr(layer_doc, "location_source", None) or "Direct Fields",
                "location_link_field": getattr(layer_doc, "location_link_field", None) or "",
                "location_doctype": getattr(layer_doc, "location_doctype", None) or "",
                "location_reverse_link_field": getattr(layer_doc, "location_reverse_link_field", None) or "",
                "location_fields": _coerce_location_fields(getattr(layer_doc, "location_fields_json", "") or ""),
                "click_action": layer_doc.click_action or "popup",
                "group_by_field": group_by_field,
                "group_config": _coerce_group_config_for_client(layer_doc.group_config_json),
                "popup_fields": popup_fields,
                "default_popup_fields": default_popup_fields,
                "linked_metrics": _coerce_linked_metrics(getattr(layer_doc, "linked_metrics_json", "") or ""),
                "linked_metric_filters": _coerce_linked_metric_filters(getattr(layer_doc, "linked_metric_filters_json", "") or ""),
                "field_labels": _source_field_labels(layer_doc.source_doctype),
                "assignment_fields": _source_assignment_fields(layer_doc.source_doctype),
                "style": {
                    "color": layer_doc.color,
                    "icon": layer_doc.icon,
                    "size": layer_doc.size,
                    "pin_min_zoom": getattr(layer_doc, "pin_min_zoom", 0) or 0,
                    "cluster": layer_doc.cluster,
                    "heatmap": layer_doc.heatmap,
                    "territory_enabled": getattr(layer_doc, "territory_enabled", 0),
                    "territory_color": getattr(layer_doc, "territory_color", "") or "",
                    "territory_opacity": getattr(layer_doc, "territory_opacity", None),
                    "territory_padding_meters": getattr(layer_doc, "territory_padding_meters", None),
                    "stroke_color": layer_doc.stroke_color,
                    "stroke_width": layer_doc.stroke_width,
                    "fill_opacity": layer_doc.fill_opacity,
                    "heatmap_config": _heatmap_config_dict(layer_doc),
                },
            },
        }

    filters += _group_filters_for_key(
        group_key,
        group_by_field,
        group_rules,
        multi_grouping,
    )

    # Apply viewport bounds as additional lat/lng WHERE clauses. This is
    # safe because the source lat/lng field types are validated at layer
    # save time (Float), so we cannot SQL-inject via these field names.
    if bounds and _location_source_mode(layer_doc) == "direct":
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

    popup_template = (layer_doc.popup_template or "").strip()
    popup_fields = _coerce_popup_fields(layer_doc.popup_fields_json)
    default_popup_fields = (
        []
        if popup_fields or popup_template
        else _default_popup_fields(layer_doc.source_doctype, layer_doc.label_field)
    )
    field_labels = _source_field_labels(layer_doc.source_doctype)
    assignment_fields = _source_assignment_fields(layer_doc.source_doctype)

    fields = (
        ["name"]
        if _location_source_mode(layer_doc) != "direct"
        else ["name", layer_doc.latitude_field, layer_doc.longitude_field]
    )
    if layer_doc.label_field and layer_doc.label_field not in fields:
        fields.append(layer_doc.label_field)
    _append_valid_source_fields(
        fields,
        layer_doc.source_doctype,
        popup_fields or default_popup_fields,
    )
    requested_extra_fields = _parse_extra_feature_fields(
        extra_fields,
        layer_doc.source_doctype,
    )
    _append_valid_source_fields(fields, layer_doc.source_doctype, requested_extra_fields)

    meta = frappe.get_meta(layer_doc.source_doctype)
    for df in meta.fields:
        if df.in_list_view and not df.hidden and df.fieldname not in fields:
            fields.append(df.fieldname)
    for f in ["modified", "modified_by", "owner"]:
        if f not in fields:
            fields.append(f)

    heatmap_config = _heatmap_config_dict(layer_doc)
    heatmap_weight_field = heatmap_config.get("weight_field") if heatmap_config.get("mode") == "sum" else ""
    if (
        heatmap_weight_field
        and not heatmap_weight_field.startswith("_metric_")
        and heatmap_weight_field not in fields
    ):
        fields.append(heatmap_weight_field)

    location_cfg = _location_config(layer_doc)
    if location_cfg:
        location_doctype = location_cfg["location_doctype"]
        lat_field = location_cfg["latitude_field"]
        lng_field = location_cfg["longitude_field"]
        link_field = location_cfg.get("link_field")
        reverse_link_field = location_cfg.get("reverse_link_field")
        location_extra_fields = _valid_location_fields(layer_doc)
        location_fetch_fields = ["name", lat_field, lng_field]
        for field in location_extra_fields:
            if field not in location_fetch_fields:
                location_fetch_fields.append(field)
        if link_field and link_field not in fields:
            fields.append(link_field)
        for field in grouping_fields:
            if _is_linked_metric_field(field):
                continue
            if field and field not in fields:
                fields.append(field)

        rows = frappe.get_all(
            layer_doc.source_doctype,
            fields=fields,
            filters=filters,
            limit_page_length=0 if metric_post_filtering else limit,
            limit_start=0 if metric_post_filtering else offset,
            order_by="name asc",
        )
        metrics_by_name = _linked_metrics_for_rows(layer_doc, rows)
        linked_by_name: dict[str, dict[str, Any]] = {}
        reverse_by_source: dict[str, dict[str, Any]] = {}
        dynamic_by_source: dict[str, dict[str, Any]] = {}

        if location_cfg["mode"] == "linked":
            linked_names = []
            seen_linked_names = set()
            for row in rows:
                linked_name = row.get(link_field)
                if linked_name and linked_name not in seen_linked_names:
                    seen_linked_names.add(linked_name)
                    linked_names.append(linked_name)
            if linked_names:
                location_filters = [["name", "in", linked_names]]
                if bounds:
                    south = float(bounds.get("south"))
                    west = float(bounds.get("west"))
                    north = float(bounds.get("north"))
                    east = float(bounds.get("east"))
                    location_filters += [
                        [lat_field, ">=", south],
                        [lat_field, "<=", north],
                        [lng_field, ">=", west],
                        [lng_field, "<=", east],
                        [lat_field, "is", "set"],
                        [lng_field, "is", "set"],
                    ]
                location_rows = frappe.get_all(
                    location_doctype,
                    fields=location_fetch_fields,
                    filters=location_filters,
                    limit_page_length=len(linked_names),
                )
                linked_by_name = {row.get("name"): row for row in location_rows}
        elif location_cfg["mode"] == "reverse":
            source_names = [row.get("name") for row in rows if row.get("name")]
            if source_names:
                location_filters = [[reverse_link_field, "in", source_names]]
                if bounds:
                    south = float(bounds.get("south"))
                    west = float(bounds.get("west"))
                    north = float(bounds.get("north"))
                    east = float(bounds.get("east"))
                    location_filters += [
                        [lat_field, ">=", south],
                        [lat_field, "<=", north],
                        [lng_field, ">=", west],
                        [lng_field, "<=", east],
                        [lat_field, "is", "set"],
                        [lng_field, "is", "set"],
                    ]
                location_rows = frappe.get_all(
                    location_doctype,
                    fields=[
                        *location_fetch_fields,
                        reverse_link_field,
                    ],
                    filters=location_filters,
                    limit_page_length=max(len(source_names) * 5, 1),
                    order_by="modified desc",
                )
                for row in location_rows:
                    source_name = row.get(reverse_link_field)
                    if source_name and source_name not in reverse_by_source:
                        reverse_by_source[source_name] = row
        elif location_cfg["mode"] == "dynamic":
            source_names = [row.get("name") for row in rows if row.get("name")]
            if source_names:
                dynamic_rows = frappe.get_all(
                    "Dynamic Link",
                    fields=["parent", "link_name"],
                    filters=[
                        ["parenttype", "=", location_doctype],
                        ["link_doctype", "=", layer_doc.source_doctype],
                        ["link_name", "in", source_names],
                    ],
                    limit_page_length=max(len(source_names) * 10, 1),
                    order_by="modified desc",
                )
                parents = []
                seen_parents = set()
                for row in dynamic_rows:
                    parent = row.get("parent")
                    if parent and parent not in seen_parents:
                        seen_parents.add(parent)
                        parents.append(parent)
                if parents:
                    location_filters = [["name", "in", parents]]
                    if bounds:
                        south = float(bounds.get("south"))
                        west = float(bounds.get("west"))
                        north = float(bounds.get("north"))
                        east = float(bounds.get("east"))
                        location_filters += [
                            [lat_field, ">=", south],
                            [lat_field, "<=", north],
                            [lng_field, ">=", west],
                            [lng_field, "<=", east],
                            [lat_field, "is", "set"],
                            [lng_field, "is", "set"],
                        ]
                    location_rows = frappe.get_all(
                        location_doctype,
                        fields=location_fetch_fields,
                        filters=location_filters,
                        limit_page_length=len(parents),
                    )
                    locations = {row.get("name"): row for row in location_rows}
                    for row in dynamic_rows:
                        source_name = row.get("link_name")
                        parent = row.get("parent")
                        if (
                            source_name
                            and source_name not in dynamic_by_source
                            and parent in locations
                        ):
                            dynamic_by_source[source_name] = locations[parent]

        total = 0
        features = []
        for r in rows:
            if location_cfg["mode"] == "linked":
                linked_name = r.get(link_field)
                location_row = linked_by_name.get(linked_name)
            elif location_cfg["mode"] == "reverse":
                location_row = reverse_by_source.get(r.get("name"))
                linked_name = location_row.get("name") if location_row else None
            else:
                location_row = dynamic_by_source.get(r.get("name"))
                linked_name = location_row.get("name") if location_row else None
            if not location_row:
                continue
            lat = location_row.get(lat_field)
            lng = location_row.get(lng_field)
            if lat is None or lng is None:
                continue
            props = {
                k: v
                for k, v in r.items()
                if not link_field or k != link_field
            }
            props["_doctype"] = layer_doc.source_doctype
            props["_name"] = r.get("name")
            props["_label"] = (
                r.get(layer_doc.label_field) if layer_doc.label_field else r.get("name")
            )
            props["_location_doctype"] = location_doctype
            props["_location_name"] = linked_name
            _attach_location_to_props(props, location_row, lat_field, lng_field)
            _attach_metrics_to_props(props, r.get("name"), metrics_by_name)
            if not _passes_linked_metric_filters(
                layer_doc,
                metrics_by_name.get(r.get("name"), {}),
            ):
                continue
            source_metrics = metrics_by_name.get(r.get("name"), {})
            if not multi_grouping and not _linked_metric_group_matches(
                source_metrics,
                group_by_field,
                group_key,
                group_rules,
            ):
                continue
            if multi_grouping:
                if not _multi_group_matches(r, source_metrics, multi_grouping, group_key):
                    continue

            total += 1
            if metric_post_filtering and total <= offset:
                continue
            if len(features) >= limit:
                continue

            group_value = None
            if multi_grouping:
                _attach_metric_group_values_to_row(
                    r,
                    source_metrics,
                    [level["field"] for level in multi_grouping.get("levels") or []],
                )
                resolved_group = _resolve_multi_group_style(
                    r,
                    multi_grouping,
                    layer_doc.color,
                    layer_doc.icon,
                )
                if resolved_group:
                    props["_group_value"] = resolved_group["key"]
                    props["_group_path"] = resolved_group["path"]
                    props["_group_label"] = resolved_group["label"]
                    props["_group_values"] = resolved_group["values"]
                    if resolved_group.get("color"):
                        props["_color"] = resolved_group["color"]
                    if resolved_group.get("icon") == "__none":
                        props["_icon_disabled"] = 1
                    elif resolved_group.get("icon"):
                        props["_icon"] = resolved_group["icon"]
            elif group_by_field:
                if _is_linked_metric_field(group_by_field):
                    source_group_value = _linked_metric_group_value(
                        source_metrics,
                        group_by_field,
                    )
                else:
                    source_group_value = r.get(group_by_field)
                band_override = None
                if group_rules.get("mode") == "bands":
                    group_value, band_override = _resolve_group_band(
                        source_group_value, group_rules
                    )
                else:
                    group_value = source_group_value
                props["_group_value"] = group_value
                override = (
                    group_config.get(str(group_value)) if group_value is not None else None
                )
                if not override and band_override:
                    override = band_override
                if group_value is not None:
                    props["_color"] = (
                        override.get("color")
                        if override and override.get("color")
                        else _auto_group_color(group_value)
                    )
                if override and override.get("icon") == "__none":
                    props["_icon_disabled"] = 1
                elif override and override.get("icon"):
                    props["_icon"] = override["icon"]

            if popup_template and render_popup:
                try:
                    ctx = _full_row_context(layer_doc.source_doctype, r.get("name"), r)
                    ctx["layer"] = {
                        "title": layer_doc.title,
                        "name": layer_doc.name,
                    }
                    ctx["metrics"] = metrics_by_name.get(r.get("name"), {})
                    ctx["location"] = frappe._dict(
                        props.get("_location") or {"name": linked_name}
                    )
                    props["_popup_html"] = _render_popup_template(popup_template, ctx)
                except (
                    frappe.exceptions.ValidationError,
                    frappe.exceptions.SecurityException,
                ):
                    raise
                except Exception:
                    props["_popup_html"] = ""
            else:
                display_fields = popup_fields or default_popup_fields
                if display_fields:
                    props["_popup_fields"] = display_fields

            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lng, lat]},
                    "properties": props,
                }
            )

        response_style = {
            "color": layer_doc.color,
            "icon": layer_doc.icon,
            "size": layer_doc.size,
            "pin_min_zoom": getattr(layer_doc, "pin_min_zoom", 0) or 0,
            "cluster": layer_doc.cluster,
            "heatmap": layer_doc.heatmap,
            "territory_enabled": getattr(layer_doc, "territory_enabled", 0),
            "territory_color": getattr(layer_doc, "territory_color", "") or "",
            "territory_opacity": getattr(layer_doc, "territory_opacity", None),
            "territory_padding_meters": getattr(layer_doc, "territory_padding_meters", None),
            "stroke_color": layer_doc.stroke_color,
            "stroke_width": layer_doc.stroke_width,
            "fill_opacity": layer_doc.fill_opacity,
            "heatmap_config": heatmap_config,
        }
        if group_key:
            response_style.update(
                _style_for_virtual_group_key(
                    group_key,
                    layer_doc,
                    group_config,
                    group_rules,
                    multi_grouping,
                )
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
                "location_source": getattr(layer_doc, "location_source", None) or "Direct Fields",
                "location_link_field": link_field or "",
                "location_reverse_link_field": reverse_link_field or "",
                "location_doctype": location_doctype,
                "location_fields": _coerce_location_fields(getattr(layer_doc, "location_fields_json", "") or ""),
                "click_action": layer_doc.click_action or "popup",
                "group_by_field": group_by_field,
                "group_config": _coerce_group_config_for_client(layer_doc.group_config_json),
                "popup_fields": popup_fields,
                "default_popup_fields": default_popup_fields,
                "linked_metrics": _coerce_linked_metrics(getattr(layer_doc, "linked_metrics_json", "") or ""),
                "linked_metric_filters": _coerce_linked_metric_filters(getattr(layer_doc, "linked_metric_filters_json", "") or ""),
                "field_labels": field_labels,
                "assignment_fields": assignment_fields,
                "style": response_style,
            },
        }

    # If the layer has a popup_template, we need the full row context (every
    # column on the source doc) to render it. Without this, only the
    # `fields` list above is fetched and templates can only reference
    # those columns. We use doc.as_dict() semantics: pull the whole doc,
    # restricted by the same permission boundary, and let Jinja reference
    # any field the user can see.
    rows: list[dict[str, Any]] = frappe.get_all(
        layer_doc.source_doctype,
        fields=fields,
        filters=filters,
        limit_page_length=0 if metric_post_filtering else limit,
        limit_start=0 if metric_post_filtering else offset,
        order_by=f"{layer_doc.latitude_field} asc",
    )
    metrics_by_name = _linked_metrics_for_rows(layer_doc, rows)

    total = 0 if metric_post_filtering else frappe.db.count(layer_doc.source_doctype, filters=filters)

    features = []

    # Add grouping fields to fetched fields if not already present.
    if (
        group_by_field
        and not _is_linked_metric_field(group_by_field)
        and group_by_field not in fields
    ):
        fields.append(group_by_field)
    for field in grouping_fields:
        if _is_linked_metric_field(field):
            continue
        if field and field not in fields:
            fields.append(field)

    # Re-fetch if we added grouping fields after the first query.
    if rows and any(field not in rows[0] for field in grouping_fields):
        rows = frappe.get_all(
            layer_doc.source_doctype,
            fields=fields,
            filters=filters,
            limit_page_length=0 if metric_post_filtering else limit,
            limit_start=0 if metric_post_filtering else offset,
            order_by=f"{layer_doc.latitude_field} asc",
        )
        metrics_by_name = _linked_metrics_for_rows(layer_doc, rows)

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
        _attach_metrics_to_props(props, r.get("name"), metrics_by_name)
        if not _passes_linked_metric_filters(
            layer_doc,
            metrics_by_name.get(r.get("name"), {}),
        ):
            continue
        source_metrics = metrics_by_name.get(r.get("name"), {})
        if not multi_grouping and not _linked_metric_group_matches(
            source_metrics,
            group_by_field,
            group_key,
            group_rules,
        ):
            continue
        if multi_grouping:
            if not _multi_group_matches(r, source_metrics, multi_grouping, group_key):
                continue

        if metric_post_filtering:
            total += 1
            if total <= offset:
                continue
            if len(features) >= limit:
                continue

        # Resolve per-feature group style (segmentation).
        group_value = None
        if multi_grouping:
            _attach_metric_group_values_to_row(
                r,
                source_metrics,
                [level["field"] for level in multi_grouping.get("levels") or []],
            )
            resolved_group = _resolve_multi_group_style(
                r,
                multi_grouping,
                layer_doc.color,
                layer_doc.icon,
            )
            if resolved_group:
                props["_group_value"] = resolved_group["key"]
                props["_group_path"] = resolved_group["path"]
                props["_group_label"] = resolved_group["label"]
                props["_group_values"] = resolved_group["values"]
                if resolved_group.get("color"):
                    props["_color"] = resolved_group["color"]
                if resolved_group.get("icon") == "__none":
                    props["_icon_disabled"] = 1
                elif resolved_group.get("icon"):
                    props["_icon"] = resolved_group["icon"]
        elif group_by_field:
            if _is_linked_metric_field(group_by_field):
                source_group_value = _linked_metric_group_value(
                    source_metrics,
                    group_by_field,
                )
            else:
                source_group_value = r.get(group_by_field)
            band_override = None
            if group_rules.get("mode") == "bands":
                group_value, band_override = _resolve_group_band(
                    source_group_value, group_rules
                )
            else:
                group_value = source_group_value
            props["_group_value"] = group_value
            override = (
                group_config.get(str(group_value)) if group_value is not None else None
            )
            if not override and band_override:
                override = band_override
            if group_value is not None:
                props["_color"] = (
                    override.get("color")
                    if override and override.get("color")
                    else _auto_group_color(group_value)
                )
            if override and override.get("icon") == "__none":
                props["_icon_disabled"] = 1
            elif override and override.get("icon"):
                props["_icon"] = override["icon"]

        # Render the Jinja popup_template against the source row. The
        # rendered HTML is attached as `_popup_html`. `safe_render=True`
        # blocks `.__` attribute traversal (Frappe default), so users
        # cannot reach into Python objects via crafted templates.
        if popup_template and render_popup:
            try:
                ctx = _full_row_context(layer_doc.source_doctype, r.get("name"), r)
                ctx["layer"] = {
                    "title": layer_doc.title,
                    "name": layer_doc.name,
                }
                ctx["metrics"] = metrics_by_name.get(r.get("name"), {})
                props["_popup_html"] = _render_popup_template(popup_template, ctx)
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
        if not popup_template:
            display_fields = popup_fields or default_popup_fields
            if display_fields:
                props["_popup_fields"] = display_fields

        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": props,
            }
        )

    response_style = {
        "color": layer_doc.color,
        "icon": layer_doc.icon,
        "size": layer_doc.size,
        "pin_min_zoom": getattr(layer_doc, "pin_min_zoom", 0) or 0,
        "cluster": layer_doc.cluster,
        "heatmap": layer_doc.heatmap,
        "territory_enabled": getattr(layer_doc, "territory_enabled", 0),
        "territory_color": getattr(layer_doc, "territory_color", "") or "",
        "territory_opacity": getattr(layer_doc, "territory_opacity", None),
        "territory_padding_meters": getattr(layer_doc, "territory_padding_meters", None),
        "stroke_color": layer_doc.stroke_color,
        "stroke_width": layer_doc.stroke_width,
        "fill_opacity": layer_doc.fill_opacity,
        "heatmap_config": heatmap_config,
    }
    if group_key:
        response_style.update(
            _style_for_virtual_group_key(
                group_key,
                layer_doc,
                group_config,
                group_rules,
                multi_grouping,
            )
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
            "location_source": getattr(layer_doc, "location_source", None) or "Direct Fields",
            "location_link_field": getattr(layer_doc, "location_link_field", None) or "",
            "location_doctype": getattr(layer_doc, "location_doctype", None) or "",
            "location_reverse_link_field": getattr(layer_doc, "location_reverse_link_field", None) or "",
            "location_fields": _coerce_location_fields(getattr(layer_doc, "location_fields_json", "") or ""),
            "click_action": layer_doc.click_action or "popup",
            "group_by_field": group_by_field,
            "group_config": _coerce_group_config_for_client(layer_doc.group_config_json),
            "popup_fields": popup_fields,
            "default_popup_fields": default_popup_fields,
            "linked_metrics": _coerce_linked_metrics(getattr(layer_doc, "linked_metrics_json", "") or ""),
            "linked_metric_filters": _coerce_linked_metric_filters(getattr(layer_doc, "linked_metric_filters_json", "") or ""),
            "field_labels": field_labels,
            "assignment_fields": assignment_fields,
            "style": response_style,
        },
    }


@frappe.whitelist()
def preview_popup_template(
    layer: str,
    popup_template: str | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Render a draft popup template against a readable feature row."""
    layer_doc = frappe.get_doc("Expedition Layer", layer)
    assert_source_read(layer_doc.source_doctype)
    _assert_location_read(layer_doc)

    template = str(
        popup_template
        if popup_template is not None
        else (layer_doc.popup_template or "")
    ).strip()
    if not template:
        return {
            "html": "",
            "source_name": "",
            "source_label": "",
            "empty_template": True,
        }

    limit = 500 if source_name else 25
    feature_collection = get_features(layer=layer, limit=limit, render_popup=False)
    features = feature_collection.get("features") or []
    selected = None
    if source_name:
        for feature in features:
            props = feature.get("properties") or {}
            if props.get("_name") == source_name:
                selected = feature
                break
    else:
        selected = features[0] if features else None
    if not selected:
        frappe.throw(
            "No readable feature is available for popup preview",
            frappe.DoesNotExistError,
        )

    props = selected.get("properties") or {}
    row_name = props.get("_name")
    ctx = _full_row_context(layer_doc.source_doctype, row_name, props)
    ctx["metrics"] = frappe._dict(props.get("_metrics") or {})
    ctx["location"] = frappe._dict(
        props.get("_location") or {"name": props.get("_location_name")}
    )
    ctx["layer"] = frappe._dict(
        {
            "name": layer_doc.name,
            "title": layer_doc.title,
            "source_doctype": layer_doc.source_doctype,
        }
    )
    return {
        "html": _render_popup_template(template, ctx),
        "source_name": row_name,
        "source_label": props.get("_label") or row_name,
        "empty_template": False,
    }


@frappe.whitelist(allow_guest=True)
def get_virtual_group_features(
    layer: str,
    group_keys: list | str,
    bounds: dict | None = None,
    limit: int = 5000,
) -> dict:
    """Fetch multiple virtual group layers in one HTTP request.

    Each returned entry is still resolved through `get_features` with a
    single `group_key`, so semantics match independent filtered layers:
    parent filters + group filter + viewport bounds.
    """
    if isinstance(group_keys, str):
        try:
            group_keys = json.loads(group_keys)
        except (TypeError, ValueError):
            group_keys = [g for g in group_keys.split(",") if g]
    if not isinstance(group_keys, list):
        group_keys = []

    clean_keys = []
    seen = set()
    for key in group_keys:
        text = str(key)
        if text in seen:
            continue
        seen.add(text)
        clean_keys.append(text)
        if len(clean_keys) >= 100:
            break

    groups = {}
    for key in clean_keys:
        groups[key] = get_features(
            layer=layer,
            bounds=bounds,
            group_key=key,
            limit=limit,
            offset=0,
        )
    return {"groups": groups}


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
    h.update((getattr(layer_doc, "location_source", "") or "").encode("utf-8"))
    h.update(b"|")
    h.update((getattr(layer_doc, "location_link_field", "") or "").encode("utf-8"))
    h.update(b"|")
    h.update((getattr(layer_doc, "location_doctype", "") or "").encode("utf-8"))
    h.update(b"|")
    h.update((getattr(layer_doc, "location_reverse_link_field", "") or "").encode("utf-8"))
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
    `assert_source_read(source_doctype)` gate as `get_features`, with
    row-level permission query conditions preserved through
    `frappe.get_all`.
    """
    layer_doc = frappe.get_doc("Expedition Layer", layer)
    if not layer_doc.enabled:
        return _empty_bounds()

    # Python Script and Client Script layers have no source_doctype —
    # bounds cannot be computed server-side without running the script.
    # Return an empty-bounds sentinel so the client skips auto-fit.
    if layer_doc.data_source_type in ("Python Script", "Client Script (JS)"):
        return _empty_bounds()

    assert_source_read(layer_doc.source_doctype)
    _assert_location_read(layer_doc)

    cache_key = f"expedition:layer_bounds:{layer_doc.name}:{_filter_hash(layer_doc)}"
    cached = frappe.cache.get_value(cache_key)
    if cached:
        return cached

    filters = _coerce_filter(layer_doc.filter_json) or []
    location_cfg = _location_config(layer_doc)
    if location_cfg:
        lat_f = location_cfg["latitude_field"]
        lng_f = location_cfg["longitude_field"]
        if location_cfg["mode"] == "reverse":
            source_rows = frappe.get_all(
                layer_doc.source_doctype,
                fields=["name"],
                filters=filters,
                limit_page_length=10000,
                order_by="name asc",
            )
            source_names = [row.get("name") for row in source_rows if row.get("name")]
            if not source_names:
                bounds = _empty_bounds()
                frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
                return bounds
            location_rows = frappe.get_all(
                location_cfg["location_doctype"],
                fields=[
                    "name",
                    location_cfg["reverse_link_field"],
                    lat_f,
                    lng_f,
                ],
                filters=[
                    [location_cfg["reverse_link_field"], "in", source_names],
                    [lat_f, "is", "set"],
                    [lng_f, "is", "set"],
                ],
                limit_page_length=max(len(source_names) * 5, 1),
                order_by="modified desc",
            )
            seen_sources = set()
            points = []
            for row in location_rows:
                source_name = row.get(location_cfg["reverse_link_field"])
                if source_name in seen_sources:
                    continue
                seen_sources.add(source_name)
                points.append(row)
            if not points:
                bounds = _empty_bounds()
            else:
                lats = [float(point[lat_f]) for point in points]
                lngs = [float(point[lng_f]) for point in points]
                bounds = {
                    "south": min(lats),
                    "west": min(lngs),
                    "north": max(lats),
                    "east": max(lngs),
                    "count": len(points),
                }
            frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
            return bounds
        if location_cfg["mode"] == "dynamic":
            source_rows = frappe.get_all(
                layer_doc.source_doctype,
                fields=["name"],
                filters=filters,
                limit_page_length=10000,
                order_by="name asc",
            )
            source_names = [row.get("name") for row in source_rows if row.get("name")]
            if not source_names:
                bounds = _empty_bounds()
                frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
                return bounds
            dynamic_rows = frappe.get_all(
                "Dynamic Link",
                fields=["parent", "link_name"],
                filters=[
                    ["parenttype", "=", location_cfg["location_doctype"]],
                    ["link_doctype", "=", layer_doc.source_doctype],
                    ["link_name", "in", source_names],
                ],
                limit_page_length=max(len(source_names) * 10, 1),
                order_by="modified desc",
            )
            parents = []
            seen_parents = set()
            for row in dynamic_rows:
                parent = row.get("parent")
                if parent and parent not in seen_parents:
                    seen_parents.add(parent)
                    parents.append(parent)
            if not parents:
                bounds = _empty_bounds()
                frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
                return bounds
            location_rows = frappe.get_all(
                location_cfg["location_doctype"],
                fields=["name", lat_f, lng_f],
                filters=[
                    ["name", "in", parents],
                    [lat_f, "is", "set"],
                    [lng_f, "is", "set"],
                ],
                limit_page_length=len(parents),
            )
            locations = {row.get("name"): row for row in location_rows}
            seen_sources = set()
            points = []
            for row in dynamic_rows:
                source_name = row.get("link_name")
                parent = row.get("parent")
                if source_name in seen_sources or parent not in locations:
                    continue
                seen_sources.add(source_name)
                points.append(locations[parent])
            if not points:
                bounds = _empty_bounds()
            else:
                lats = [float(point[lat_f]) for point in points]
                lngs = [float(point[lng_f]) for point in points]
                bounds = {
                    "south": min(lats),
                    "west": min(lngs),
                    "north": max(lats),
                    "east": max(lngs),
                    "count": len(points),
                }
            frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
            return bounds

        rows = frappe.get_all(
            layer_doc.source_doctype,
            fields=["name", location_cfg["link_field"]],
            filters=filters,
            limit_page_length=10000,
            order_by="name asc",
        )
        linked_names = []
        seen = set()
        for row in rows:
            linked_name = row.get(location_cfg["link_field"])
            if linked_name and linked_name not in seen:
                seen.add(linked_name)
                linked_names.append(linked_name)
        if not linked_names:
            bounds = _empty_bounds()
            frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
            return bounds
        location_rows = frappe.get_all(
            location_cfg["location_doctype"],
            fields=["name", lat_f, lng_f],
            filters=[
                ["name", "in", linked_names],
                [lat_f, "is", "set"],
                [lng_f, "is", "set"],
            ],
            limit_page_length=len(linked_names),
        )
        locations = {row.get("name"): row for row in location_rows}
        points = [
            locations[row.get(location_cfg["link_field"])]
            for row in rows
            if row.get(location_cfg["link_field"]) in locations
        ]
        if not points:
            bounds = _empty_bounds()
        else:
            lats = [float(point[lat_f]) for point in points]
            lngs = [float(point[lng_f]) for point in points]
            bounds = {
                "south": min(lats),
                "west": min(lngs),
                "north": max(lats),
                "east": max(lngs),
                "count": len(points),
            }
        frappe.cache.set_value(cache_key, bounds, expires_in_sec=300)
        return bounds

    lat_f, lng_f = layer_doc.latitude_field, layer_doc.longitude_field

    direct_filters = list(filters)
    direct_filters += [[lat_f, "is", "set"], [lng_f, "is", "set"]]
    rows = frappe.get_all(
        layer_doc.source_doctype,
        fields=[lat_f, lng_f],
        filters=direct_filters,
        limit_page_length=0,
        order_by="name asc",
    )
    points = []
    for row in rows:
        try:
            points.append((float(row[lat_f]), float(row[lng_f])))
        except (TypeError, ValueError):
            continue

    if not points:
        bounds = _empty_bounds()
    else:
        lats = [point[0] for point in points]
        lngs = [point[1] for point in points]
        bounds = {
            "south": min(lats),
            "west": min(lngs),
            "north": max(lats),
            "east": max(lngs),
            "count": len(points),
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
    assert_map_read(map_name)

    fields = [
        "name",
        "title",
        "map",
        "source_doctype",
        "location_source",
        "location_link_field",
        "location_doctype",
        "location_reverse_link_field",
        "location_fields_json",
        "latitude_field",
        "longitude_field",
        "label_field",
        "filter_json",
        "group_by_field",
        "group_config_json",
        "popup_template",
        "popup_fields_json",
        "linked_metrics_json",
        "linked_metric_filters_json",
        "click_action",
        "color",
        "icon",
        "size",
        "cluster",
        "heatmap",
        "heatmap_mode",
        "heatmap_weight_field",
        "heatmap_weight_min",
        "heatmap_weight_max",
        "heatmap_weight_scale",
        "heatmap_weight_stops_json",
        "heatmap_radius_min",
        "heatmap_radius_max",
        "heatmap_intensity_min",
        "heatmap_intensity_max",
        "heatmap_opacity",
        "heatmap_ramp_json",
        "territory_enabled",
        "territory_color",
        "territory_opacity",
        "territory_padding_meters",
        "stroke_color",
        "stroke_width",
        "fill_opacity",
        "enabled",
        "sequence",
        "radius_enabled",
        "radius_field",
        "radius_meters",
        "radius_opacity",
    ]
    if frappe.db.has_column("Expedition Layer", "pin_min_zoom"):
        fields.insert(fields.index("cluster"), "pin_min_zoom")
    if frappe.db.has_column("Expedition Layer", "heatmap_weight_stops_json"):
        fields.insert(fields.index("heatmap_radius_min"), "heatmap_weight_stops_json")
    if frappe.db.has_column("Expedition Layer", "heatmap_intensity_min"):
        fields.insert(fields.index("heatmap_intensity_max"), "heatmap_intensity_min")

    rows = frappe.get_all(
        "Expedition Layer",
        filters={"map": map_name},
        fields=fields,
        order_by="sequence asc, modified desc",
    )
    out = []
    for r in rows:
        # Parse the group_config_json into a dict for the client
        r["group_config"] = _coerce_group_config_for_client(r.get("group_config_json"))
        r["popup_fields"] = _coerce_popup_fields(r.get("popup_fields_json"))
        out.append({**r, "style": _layer_style_dict(r)})
    return out


def _coerce_heatmap_ramp(raw: str | list | None) -> list[dict[str, Any]]:
    """Parse heatmap_ramp_json into sorted [{stop, color, alpha}] stops."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    out = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            stop = float(item.get("stop"))
        except (TypeError, ValueError):
            continue
        color = str(item.get("color") or "").strip()
        if stop < 0 or stop > 1 or not HEATMAP_RAMP_RE.match(color):
            continue
        try:
            alpha = float(item.get("alpha", 1))
        except (TypeError, ValueError):
            alpha = 1.0
        out.append(
            {
                "stop": stop,
                "color": color,
                "alpha": max(0.0, min(1.0, alpha)),
            }
        )
    out.sort(key=lambda s: s["stop"])
    return out


def _coerce_weight_stops(raw: str | list | None) -> list[list[float]]:
    """Parse heatmap_weight_stops_json into sorted [[value, weight]] pairs."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list) or len(parsed) < 2:
        return []
    out = []
    for pair in parsed:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        try:
            value = float(pair[0])
            weight = float(pair[1])
        except (TypeError, ValueError):
            continue
        out.append([value, weight])
    out.sort(key=lambda pair: pair[0])
    return out


def _validate_heatmap_weight_range(field: str | None, min_value: Any, max_value: Any) -> None:
    """Weighted heatmaps need a usable input range for client expressions."""
    if not field:
        return
    if min_value in (None, "") or max_value in (None, ""):
        frappe.throw("Heatmap metric range requires both minimum and maximum.", frappe.ValidationError)
    try:
        min_number = float(min_value)
        max_number = float(max_value)
    except (TypeError, ValueError):
        frappe.throw("Heatmap metric range must be numeric.", frappe.ValidationError)
    if min_number == max_number:
        frappe.throw("Heatmap metric minimum and maximum must be different.", frappe.ValidationError)


def _heatmap_config_dict(layer: Any) -> dict[str, Any]:
    """Return the client-facing heatmap configuration for a layer row/doc."""
    get = layer.get if isinstance(layer, dict) else lambda key, default=None: getattr(layer, key, default)
    mode = str(get("heatmap_mode") or "count").lower()
    scale = str(get("heatmap_weight_scale") or "linear").lower()
    return {
        "mode": mode if mode in {"count", "sum"} else "count",
        "weight_field": get("heatmap_weight_field") or "",
        "weight_min": get("heatmap_weight_min"),
        "weight_max": get("heatmap_weight_max"),
        "weight_scale": "log" if scale == "log" else "linear",
        "weight_stops": _coerce_weight_stops(get("heatmap_weight_stops_json") or ""),
        "radius_min": get("heatmap_radius_min") or 10,
        "radius_max": get("heatmap_radius_max") or 30,
        "intensity_min": get("heatmap_intensity_min") or 1,
        "intensity_max": get("heatmap_intensity_max") or 2.5,
        "opacity": get("heatmap_opacity") if get("heatmap_opacity") is not None else 0.75,
        "ramp": _coerce_heatmap_ramp(get("heatmap_ramp_json") or ""),
    }


def _ensure_pin_min_zoom_column() -> None:
    if frappe.db.has_column("Expedition Layer", "pin_min_zoom"):
        return
    frappe.reload_doc("expedition", "doctype", "expedition_layer")


@frappe.whitelist()
def create(
    map_name: str,
    title: str,
    source_doctype: str,
    location_source: str = "Direct Fields",
    location_link_field: str | None = None,
    location_doctype: str | None = None,
    location_reverse_link_field: str | None = None,
    location_fields_json: str | None = None,
    latitude_field: str = "latitude",
    longitude_field: str = "longitude",
    label_field: str | None = None,
    color: str | None = None,
    size: str = "m",
    pin_min_zoom: float = 0,
    cluster: int = 1,
    enabled: int = 1,
    icon: str | None = None,
    filter_json: str | None = None,
    group_by_field: str | None = None,
    group_config_json: str | None = None,
    popup_template: str | None = None,
    popup_fields_json: str | None = None,
    linked_metrics_json: str | None = None,
    linked_metric_filters_json: str | None = None,
    click_action: str = "popup",
    heatmap: int = 0,
    heatmap_mode: str = "count",
    heatmap_weight_field: str | None = None,
    heatmap_weight_min: float | None = None,
    heatmap_weight_max: float | None = None,
    heatmap_weight_scale: str = "linear",
    heatmap_weight_stops_json: str | None = None,
    heatmap_radius_min: int = 10,
    heatmap_radius_max: int = 30,
    heatmap_intensity_min: float = 1,
    heatmap_intensity_max: float = 2.5,
    heatmap_opacity: float = 0.75,
    heatmap_ramp_json: str | None = None,
    territory_enabled: int = 0,
    territory_color: str | None = None,
    territory_opacity: float = 0.18,
    territory_padding_meters: int = 2500,
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
    assert_map_write(map_name)
    assert_source_read(source_doctype)
    _ensure_pin_min_zoom_column()
    if location_source == "Linked DocType" and location_doctype:
        assert_source_read(location_doctype)
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
            "location_source": location_source or "Direct Fields",
            "location_link_field": location_link_field or "",
            "location_doctype": location_doctype or "",
            "location_reverse_link_field": location_reverse_link_field or "",
            "location_fields_json": location_fields_json or "",
            "latitude_field": latitude_field,
            "longitude_field": longitude_field,
            "label_field": label_field or "",
            "filter_json": filter_json or "",
            "group_by_field": group_by_field or "",
            "group_config_json": group_config_json or "",
            "popup_template": popup_template or "",
            "popup_fields_json": popup_fields_json or "",
            "linked_metrics_json": linked_metrics_json or "",
            "linked_metric_filters_json": linked_metric_filters_json or "",
            "click_action": click_action or "popup",
            "color": color or "#3B82F6",
            "size": size,
            "pin_min_zoom": float(pin_min_zoom or 0),
            "cluster": int(cluster),
            "enabled": int(enabled),
            "icon": icon or "",
            "sequence": int(last_seq),
            "use_source_permissions": 1,
            "heatmap": int(heatmap),
            "heatmap_mode": heatmap_mode or "count",
            "heatmap_weight_field": heatmap_weight_field or "",
            "heatmap_weight_min": heatmap_weight_min,
            "heatmap_weight_max": heatmap_weight_max,
            "heatmap_weight_scale": heatmap_weight_scale or "linear",
            "heatmap_weight_stops_json": heatmap_weight_stops_json or "",
            "heatmap_radius_min": int(heatmap_radius_min or 10),
            "heatmap_radius_max": int(heatmap_radius_max or 30),
            "heatmap_intensity_min": float(heatmap_intensity_min or 1),
            "heatmap_intensity_max": float(heatmap_intensity_max or 2.5),
            "heatmap_opacity": float(heatmap_opacity if heatmap_opacity is not None else 0.75),
            "heatmap_ramp_json": heatmap_ramp_json or "",
            "territory_enabled": int(territory_enabled),
            "territory_color": territory_color or "",
            "territory_opacity": float(territory_opacity if territory_opacity is not None else 0.18),
            "territory_padding_meters": int(territory_padding_meters or 2500),
            "radius_enabled": int(radius_enabled),
            "radius_field": radius_field or "",
            "radius_meters": int(radius_meters),
            "radius_opacity": float(radius_opacity),
        }
    )
    _sync_filter_child_table_from_json(doc, filter_json)
    _assert_location_read(doc)
    doc.insert(ignore_permissions=True)
    return _layer_to_dto(doc)


@frappe.whitelist()
def update(layer_name: str, **fields) -> dict:
    """Update an Expedition Layer. Pass any subset of editable fields.

    Editable: title, color, size, cluster, enabled, label_field,
    latitude_field, longitude_field, filter_json, icon, heatmap,
    stroke_color, stroke_width, fill_opacity, sequence.
    """
    if "pin_min_zoom" in fields:
        _ensure_pin_min_zoom_column()
    doc = frappe.get_doc("Expedition Layer", layer_name)
    if doc.map:
        assert_map_write(doc.map)
    elif not frappe.has_permission("Expedition Layer", "write", doc=layer_name):
        frappe.throw("Not permitted to update this layer", frappe.PermissionError)
    _assert_icons_readable(fields.get("icon"), fields.get("group_config_json"))

    allowed = {
        "title",
        "color",
        "size",
        "pin_min_zoom",
        "cluster",
        "enabled",
        "label_field",
        "location_source",
        "location_link_field",
        "location_doctype",
        "location_reverse_link_field",
        "location_fields_json",
        "latitude_field",
        "longitude_field",
        "filter_json",
        "group_by_field",
        "group_config_json",
        "popup_template",
        "popup_fields_json",
        "linked_metrics_json",
        "linked_metric_filters_json",
        "click_action",
        "icon",
        "heatmap",
        "heatmap_mode",
        "heatmap_weight_field",
        "heatmap_weight_min",
        "heatmap_weight_max",
        "heatmap_weight_scale",
        "heatmap_weight_stops_json",
        "heatmap_radius_min",
        "heatmap_radius_max",
        "heatmap_intensity_min",
        "heatmap_intensity_max",
        "heatmap_opacity",
        "heatmap_ramp_json",
        "territory_enabled",
        "territory_color",
        "territory_opacity",
        "territory_padding_meters",
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
    if "filter_json" in fields:
        _sync_filter_child_table_from_json(doc, fields.get("filter_json"))
        changed = True
    if changed:
        _assert_location_read(doc)
        doc.save(ignore_permissions=True)
    return _layer_to_dto(doc)


@frappe.whitelist()
def delete(layer_name: str) -> dict:
    """Delete an Expedition Layer."""
    map_name = frappe.db.get_value("Expedition Layer", layer_name, "map")
    if map_name:
        assert_map_write(map_name)
    elif not frappe.has_permission("Expedition Layer", "delete", doc=layer_name):
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
    assert_map_write(map_name)
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
    """Return filterable field metadata for a source DocType including child tables."""
    assert_source_read(source_doctype)
    fields = list(_filter_field_map(source_doctype).values())
    
    # Load child table fields
    parent_meta = frappe.get_meta(source_doctype)
    for f in parent_meta.fields:
        if f.fieldtype == "Table" and f.options:
            child_doctype = f.options
            try:
                child_fields_map = _filter_field_map(child_doctype)
                for cf in child_fields_map.values():
                    c_fieldname = f"{child_doctype}:{cf['fieldname']}"
                    c_label = f"{cf.get('label') or cf['fieldname']} ({f.label or f.fieldname})"
                    fields.append({
                        "fieldname": c_fieldname,
                        "fieldtype": cf["fieldtype"],
                        "label": c_label,
                        "options": cf.get("options") or "",
                        "reqd": cf.get("reqd") or 0,
                        "hidden": cf.get("hidden") or 0,
                        "read_only": cf.get("read_only") or 0,
                        "standard": cf.get("standard") or 0,
                        "operators": cf.get("operators") or [],
                        "child_doctype": child_doctype,
                        "child_fieldname": cf["fieldname"],
                    })
            except Exception:
                pass

    fields.sort(
        key=lambda f: (
            str(f.get("label") or f["fieldname"]).casefold(),
            str(f["fieldname"]).casefold(),
        )
    )
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


def _metric_key_from_parts(*parts: Any) -> str:
    key = "_".join(str(part or "") for part in parts)
    key = re.sub(r"[^A-Za-z0-9_]+", "_", key).strip("_").lower()
    key = re.sub(r"_+", "_", key)
    if not key:
        return "metric"
    if not re.match(r"^[A-Za-z_]", key):
        key = f"metric_{key}"
    return key[:48]


def _unique_metric_key(base_key: str, seen: set[str]) -> str:
    key = base_key[:48] or "metric"
    if key not in seen:
        seen.add(key)
        return key
    suffix = 2
    while True:
        suffix_text = f"_{suffix}"
        candidate = f"{key[:48 - len(suffix_text)]}{suffix_text}"
        if candidate not in seen:
            seen.add(candidate)
            return candidate
        suffix += 1


def _money_metric_base_filters(meta: Any, fieldname: str | None = None) -> list[list[Any]]:
    filters: list[list[Any]] = []
    if int(getattr(meta, "is_submittable", 0) or 0):
        filters.append(["docstatus", "=", 1])
    if fieldname == "outstanding_amount":
        filters.append(["outstanding_amount", ">", 0])
    return filters


def _money_metric_label(doctype: str, field_label: str | None, aggregate: str) -> str:
    if aggregate == "count":
        return f"{doctype} Count"
    return f"{doctype} {field_label or 'Amount'}"


def _money_doctype_priority(doctype: str) -> int:
    try:
        return MONEY_DOCTYPE_PRIORITY.index(doctype)
    except ValueError:
        return len(MONEY_DOCTYPE_PRIORITY)


def _readable_doctype_candidates() -> list[str]:
    priority = [dt for dt in MONEY_DOCTYPE_PRIORITY if frappe.db.exists("DocType", dt)]
    priority_set = set(priority)
    rows = frappe.get_all(
        "DocType",
        filters={"istable": 0, "issingle": 0},
        fields=["name"],
        order_by="name asc",
        limit_page_length=0,
    )
    return priority + [row.name for row in rows if row.name not in priority_set]


def _dynamic_link_can_target(field: Any, source_doctype: str) -> bool:
    fieldtype = getattr(field, "fieldtype", "") or ""
    options = str(getattr(field, "options", "") or "")
    if fieldtype == "Select":
        return source_doctype in {row.strip() for row in options.splitlines() if row.strip()}
    if fieldtype == "Link" and options == "DocType":
        return True
    return fieldtype in {"Data", "Small Text", "Read Only"}


def _money_link_fields(meta: Any, source_doctype: str) -> list[dict[str, str]]:
    fields_by_name = {df.fieldname: df for df in meta.fields if df.fieldname}
    links: list[dict[str, str]] = []
    for df in meta.fields:
        if not df.fieldname:
            continue
        if df.fieldtype == "Link" and df.options == source_doctype:
            links.append({"link_field": df.fieldname, "dynamic_link_doctype_field": ""})
        elif df.fieldtype == "Dynamic Link":
            doctype_fieldname = str(df.options or "").strip()
            doctype_field = fields_by_name.get(doctype_fieldname)
            if doctype_field and _dynamic_link_can_target(doctype_field, source_doctype):
                links.append(
                    {
                        "link_field": df.fieldname,
                        "dynamic_link_doctype_field": doctype_fieldname,
                    }
                )
    return links


@frappe.whitelist()
def suggest_money_metrics(source_doctype: str, limit: int = 12) -> dict:
    """Suggest linked money metric rows for a layer source DocType."""
    source_doctype = str(source_doctype or "").strip()
    if not source_doctype:
        return {"source_doctype": "", "suggestions": [], "truncated": False}
    assert_source_read(source_doctype)

    limit = min(max(int(limit or 12), 1), 50)
    suggestions: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    try:
        source_module = frappe.get_meta(source_doctype).module
    except Exception:
        source_module = ""

    for doctype in _readable_doctype_candidates():
        if doctype == source_doctype or not frappe.has_permission(doctype, "read"):
            continue
        try:
            meta = frappe.get_meta(doctype)
        except Exception:
            continue

        link_fields = _money_link_fields(meta, source_doctype)
        if not link_fields:
            continue
        same_module_rank = 0 if getattr(meta, "module", "") == source_module else 1

        fields_by_name = {df.fieldname: df for df in meta.fields if df.fieldname}
        candidate_fields = [
            (fieldname, default_label, aggregate)
            for fieldname, default_label, aggregate in MONEY_FIELD_CANDIDATES
            if fieldname in fields_by_name
            and fields_by_name[fieldname].fieldtype in NUMERIC_FIELD_TYPES
        ]
        if not candidate_fields:
            candidate_fields = [
                (df.fieldname, df.label or df.fieldname, "sum")
                for df in meta.fields
                if df.fieldname and df.fieldtype == "Currency"
            ][:3]

        for link in link_fields:
            link_fieldname = link["link_field"]
            dynamic_link_doctype_field = link.get("dynamic_link_doctype_field") or ""
            link_rank = 1 if dynamic_link_doctype_field else 0
            base_rank = (
                _money_doctype_priority(doctype),
                same_module_rank,
                link_rank,
                doctype.lower(),
                link_fieldname.lower(),
            )
            base_filters = _money_metric_base_filters(meta)
            if dynamic_link_doctype_field:
                base_filters = base_filters + [[dynamic_link_doctype_field, "=", source_doctype]]
            count_key = _unique_metric_key(
                _metric_key_from_parts(doctype, link_fieldname, "count"),
                seen_keys,
            )
            suggestions.append(
                {
                    "key": count_key,
                    "label": _money_metric_label(doctype, None, "count"),
                    "source_doctype": doctype,
                    "link_field": link_fieldname,
                    "dynamic_link_doctype_field": dynamic_link_doctype_field,
                    "aggregate": "count",
                    "field": "",
                    "filters": base_filters,
                    "category": "count",
                    "_rank": (*base_rank, 1, ""),
                }
            )

            for fieldname, default_label, aggregate in candidate_fields[:4]:
                field_meta = fields_by_name[fieldname]
                label = field_meta.label or default_label
                key = _unique_metric_key(
                    _metric_key_from_parts(doctype, fieldname),
                    seen_keys,
                )
                suggestions.append(
                    {
                        "key": key,
                        "label": _money_metric_label(doctype, label, aggregate),
                        "source_doctype": doctype,
                        "link_field": link_fieldname,
                        "dynamic_link_doctype_field": dynamic_link_doctype_field,
                        "aggregate": aggregate,
                        "field": fieldname,
                        "filters": (
                            _money_metric_base_filters(meta, fieldname)
                            + (
                                [[dynamic_link_doctype_field, "=", source_doctype]]
                                if dynamic_link_doctype_field
                                else []
                            )
                        ),
                        "category": "money",
                        "_rank": (*base_rank, 0, fieldname.lower()),
                    }
                )

    suggestions.sort(key=lambda item: item.get("_rank") or ())
    for suggestion in suggestions:
        suggestion.pop("_rank", None)
    return {
        "source_doctype": source_doctype,
        "suggestions": suggestions[:limit],
        "truncated": len(suggestions) > limit,
    }


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
    field_map = _filter_field_map(source_doctype)
    if field not in field_map:
        frappe.throw(
            f"Field '{field}' is not groupable on {source_doctype}",
            frappe.ValidationError,
        )
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
def list_group_tree(
    source_doctype: str,
    fields: list | str,
    limit: int = 1000,
    linked_metrics_json: str | list | None = None,
    filter_json: str | list | None = None,
) -> dict:
    """Return distinct grouping paths for ordered `fields`.

    The response is intentionally path-based instead of a fully nested
    object so the client can preserve expansion state and lazily render
    tree rows. Permissions are enforced against the source DocType and
    fields are validated against the same filterable schema used by the
    layer editor.
    """
    assert_source_read(source_doctype)
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except (TypeError, ValueError):
            fields = [f.strip() for f in fields.split(",") if f.strip()]
    if not isinstance(fields, list):
        fields = []

    field_map = _filter_field_map(source_doctype)
    metric_fields = linked_metric_property_names(linked_metrics_json)
    levels = []
    clean_fields = []
    db_fields = []
    seen = set()
    for field in fields:
        raw_level = field if isinstance(field, dict) else {"field": field, "mode": "value"}
        fieldname = str(raw_level.get("field") or "").strip()
        if not fieldname or fieldname in seen:
            continue
        if fieldname not in field_map and fieldname not in metric_fields:
            frappe.throw(
                f"Field '{fieldname}' is not groupable on {source_doctype}",
                frappe.ValidationError,
            )
        seen.add(fieldname)
        clean_fields.append(fieldname)
        if not _is_linked_metric_field(fieldname):
            db_fields.append(fieldname)
        mode = str(raw_level.get("mode") or "value").lower()
        level = {"field": fieldname, "mode": "bands" if mode == "bands" else "value"}
        if level["mode"] == "bands":
            kind = str(raw_level.get("kind") or "number").lower()
            level["kind"] = kind if kind in {"number", "date", "datetime", "time"} else "number"
            bands = []
            for idx, band in enumerate(raw_level.get("bands") or []):
                if not isinstance(band, dict):
                    continue
                key = str(band.get("key") or f"band_{idx + 1}")
                item = {"key": key}
                for edge in ("min", "max"):
                    value = band.get(edge)
                    item[edge] = None if value in (None, "") else value
                if item.get("min") is None and item.get("max") is None:
                    continue
                if isinstance(band.get("label"), str):
                    item["label"] = band["label"]
                bands.append(item)
            level["bands"] = bands
        levels.append(level)

    if not clean_fields:
        return {"paths": [], "truncated": False}

    limit = min(max(int(limit or 1000), 1), 5000)
    base_filters = _coerce_filter(filter_json) or []
    fetch_fields = ["name", *db_fields]
    order_by = ", ".join(f"{field} asc" for field in db_fields) if db_fields else "name asc"
    try:
        rows = frappe.get_all(
            source_doctype,
            fields=fetch_fields,
            filters=base_filters,
            distinct=not metric_fields.intersection(clean_fields),
            order_by=order_by,
            limit_page_length=limit,
        )
    except Exception:
        return {"paths": [], "truncated": False}
    if metric_fields.intersection(clean_fields):
        metric_layer = frappe._dict(
            {
                "source_doctype": source_doctype,
                "linked_metrics_json": linked_metrics_json,
            }
        )
        metrics_by_name = _linked_metrics_for_rows(metric_layer, rows)
        for row in rows:
            metrics = metrics_by_name.get(row.get("name"), {})
            for fieldname in clean_fields:
                if _is_linked_metric_field(fieldname):
                    row[fieldname] = _linked_metric_group_value(metrics, fieldname)

    seen_paths = set()
    paths = []
    for row in rows:
        values = [_resolve_group_level_value(row, level) for level in levels]
        key = _group_path_key(values)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        paths.append({
            "key": key,
            "values": values,
            "labels": values,
        })
    return {"paths": paths, "truncated": len(rows) >= limit}


@frappe.whitelist()
def list_source_doctypes() -> list[dict]:
    """Return readable DocTypes for the layer source picker.

    Coordinate fields are chosen separately in the editor, so do not
    pre-filter to DocTypes with literal latitude/longitude field names.
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
        "pin_min_zoom": layer.get("pin_min_zoom") or 0,
        "cluster": layer.get("cluster"),
        "heatmap": layer.get("heatmap"),
        "heatmap_config": _heatmap_config_dict(layer),
        "territory_enabled": layer.get("territory_enabled"),
        "territory_color": layer.get("territory_color"),
        "territory_opacity": layer.get("territory_opacity"),
        "territory_padding_meters": layer.get("territory_padding_meters"),
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
        "location_source": getattr(doc, "location_source", None) or "Direct Fields",
        "location_link_field": getattr(doc, "location_link_field", None) or "",
        "location_doctype": getattr(doc, "location_doctype", None) or "",
        "location_reverse_link_field": getattr(doc, "location_reverse_link_field", None) or "",
        "location_fields_json": getattr(doc, "location_fields_json", None) or "",
        "location_fields": _coerce_location_fields(getattr(doc, "location_fields_json", "") or ""),
        "latitude_field": doc.latitude_field,
        "longitude_field": doc.longitude_field,
        "label_field": doc.label_field,
        "filter_json": doc.filter_json,
        "group_by_field": doc.group_by_field or "",
        "group_config": _coerce_group_config_for_client(doc.group_config_json),
        "group_config_json": doc.group_config_json or "",
        "popup_template": doc.popup_template or "",
        "popup_fields": _coerce_popup_fields(doc.popup_fields_json),
        "popup_fields_json": doc.popup_fields_json or "",
        "linked_metrics_json": getattr(doc, "linked_metrics_json", None) or "",
        "linked_metrics": _coerce_linked_metrics(getattr(doc, "linked_metrics_json", "") or ""),
        "linked_metric_filters_json": getattr(doc, "linked_metric_filters_json", None) or "",
        "linked_metric_filters": _coerce_linked_metric_filters(getattr(doc, "linked_metric_filters_json", "") or ""),
        "click_action": doc.click_action or "popup",
        "color": doc.color,
        "icon": doc.icon,
        "size": doc.size,
        "pin_min_zoom": getattr(doc, "pin_min_zoom", 0) or 0,
        "cluster": doc.cluster,
        "heatmap": doc.heatmap,
        "heatmap_mode": doc.heatmap_mode or "count",
        "heatmap_weight_field": doc.heatmap_weight_field or "",
        "heatmap_weight_min": doc.heatmap_weight_min,
        "heatmap_weight_max": doc.heatmap_weight_max,
        "heatmap_weight_scale": doc.heatmap_weight_scale or "linear",
        "heatmap_weight_stops_json": doc.heatmap_weight_stops_json or "",
        "heatmap_radius_min": doc.heatmap_radius_min,
        "heatmap_radius_max": doc.heatmap_radius_max,
        "heatmap_intensity_min": doc.heatmap_intensity_min,
        "heatmap_intensity_max": doc.heatmap_intensity_max,
        "heatmap_opacity": doc.heatmap_opacity,
        "heatmap_ramp_json": doc.heatmap_ramp_json or "",
        "heatmap_config": _heatmap_config_dict(doc),
        "territory_enabled": getattr(doc, "territory_enabled", 0),
        "territory_color": getattr(doc, "territory_color", None) or "",
        "territory_opacity": getattr(doc, "territory_opacity", None),
        "territory_padding_meters": getattr(doc, "territory_padding_meters", None),
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
                "pin_min_zoom": getattr(doc, "pin_min_zoom", 0) or 0,
                "cluster": doc.cluster,
                "heatmap": doc.heatmap,
                "heatmap_mode": doc.heatmap_mode,
                "heatmap_weight_field": doc.heatmap_weight_field,
                "heatmap_weight_min": doc.heatmap_weight_min,
                "heatmap_weight_max": doc.heatmap_weight_max,
                "heatmap_weight_scale": doc.heatmap_weight_scale,
                "heatmap_weight_stops_json": doc.heatmap_weight_stops_json,
                "heatmap_radius_min": doc.heatmap_radius_min,
                "heatmap_radius_max": doc.heatmap_radius_max,
                "heatmap_intensity_min": doc.heatmap_intensity_min,
                "heatmap_intensity_max": doc.heatmap_intensity_max,
                "heatmap_opacity": doc.heatmap_opacity,
                "heatmap_ramp_json": doc.heatmap_ramp_json,
                "territory_enabled": getattr(doc, "territory_enabled", 0),
                "territory_color": getattr(doc, "territory_color", None) or "",
                "territory_opacity": getattr(doc, "territory_opacity", None),
                "territory_padding_meters": getattr(doc, "territory_padding_meters", None),
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
    location_source: str = "Direct Fields",
    location_link_field: str | None = None,
    location_doctype: str | None = None,
    location_reverse_link_field: str | None = None,
    location_fields_json: str | None = None,
    latitude_field: str = "latitude",
    longitude_field: str = "longitude",
    label_field: str | None = None,
    color: str | None = None,
    size: str = "m",
    pin_min_zoom: float = 0,
    cluster: int = 1,
    enabled: int = 1,
    icon: str | None = None,
    filter_json: str | None = None,
    group_by_field: str | None = None,
    group_config_json: str | None = None,
    popup_template: str | None = None,
    popup_fields_json: str | None = None,
    linked_metrics_json: str | None = None,
    linked_metric_filters_json: str | None = None,
    click_action: str = "popup",
    heatmap: int = 0,
    heatmap_mode: str = "count",
    heatmap_weight_field: str | None = None,
    heatmap_weight_min: float | None = None,
    heatmap_weight_max: float | None = None,
    heatmap_weight_scale: str = "linear",
    heatmap_weight_stops_json: str | None = None,
    heatmap_radius_min: int = 10,
    heatmap_radius_max: int = 30,
    heatmap_intensity_min: float = 1,
    heatmap_intensity_max: float = 2.5,
    heatmap_opacity: float = 0.75,
    heatmap_ramp_json: str | None = None,
    territory_enabled: int = 0,
    territory_color: str | None = None,
    territory_opacity: float = 0.18,
    territory_padding_meters: int = 2500,
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
    _ensure_pin_min_zoom_column()

    doc = frappe.new_doc("Expedition Layer")
    doc.update(
        {
            "title": title,
            "map": "",
            "source_doctype": source_doctype,
            "location_source": location_source or "Direct Fields",
            "location_link_field": location_link_field or "",
            "location_doctype": location_doctype or "",
            "location_reverse_link_field": location_reverse_link_field or "",
            "location_fields_json": location_fields_json or "",
            "latitude_field": latitude_field,
            "longitude_field": longitude_field,
            "label_field": label_field or "",
            "color": color or "#3B82F6",
            "size": size,
            "pin_min_zoom": float(pin_min_zoom or 0),
            "cluster": int(cluster),
            "enabled": int(enabled),
            "icon": icon or "",
            "filter_json": filter_json or "",
            "group_by_field": group_by_field or "",
            "group_config_json": group_config_json or "",
            "popup_template": popup_template or "",
            "popup_fields_json": popup_fields_json or "",
            "linked_metrics_json": linked_metrics_json or "",
            "linked_metric_filters_json": linked_metric_filters_json or "",
            "click_action": click_action or "popup",
            "use_source_permissions": 1,
            "heatmap": int(heatmap),
            "heatmap_mode": heatmap_mode or "count",
            "heatmap_weight_field": heatmap_weight_field or "",
            "heatmap_weight_min": heatmap_weight_min,
            "heatmap_weight_max": heatmap_weight_max,
            "heatmap_weight_scale": heatmap_weight_scale or "linear",
            "heatmap_weight_stops_json": heatmap_weight_stops_json or "",
            "heatmap_radius_min": int(heatmap_radius_min or 10),
            "heatmap_radius_max": int(heatmap_radius_max or 30),
            "heatmap_intensity_min": float(heatmap_intensity_min or 1),
            "heatmap_intensity_max": float(heatmap_intensity_max or 2.5),
            "heatmap_opacity": float(heatmap_opacity if heatmap_opacity is not None else 0.75),
            "heatmap_ramp_json": heatmap_ramp_json or "",
            "territory_enabled": int(territory_enabled),
            "territory_color": territory_color or "",
            "territory_opacity": float(territory_opacity if territory_opacity is not None else 0.18),
            "territory_padding_meters": int(territory_padding_meters or 2500),
            "radius_enabled": int(radius_enabled),
            "radius_field": radius_field or "",
            "radius_meters": int(radius_meters),
            "radius_opacity": float(radius_opacity),
        }
    )
    _sync_filter_child_table_from_json(doc, filter_json)
    _assert_location_read(doc)
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
    _lm_fields = [
        "name",
        "title",
        "map",
        "source_doctype",
        "location_source",
        "location_link_field",
        "location_doctype",
        "location_reverse_link_field",
        "location_fields_json",
        "latitude_field",
        "longitude_field",
        "label_field",
        "filter_json",
        "group_by_field",
        "group_config_json",
        "popup_template",
        "popup_fields_json",
        "linked_metrics_json",
        "linked_metric_filters_json",
        "click_action",
        "color",
        "icon",
        "size",
        "cluster",
        "heatmap",
        "heatmap_mode",
        "heatmap_weight_field",
        "heatmap_weight_min",
        "heatmap_weight_max",
        "heatmap_weight_scale",
        "heatmap_radius_min",
        "heatmap_radius_max",
        "heatmap_intensity_max",
        "heatmap_opacity",
        "heatmap_ramp_json",
        "territory_enabled",
        "territory_color",
        "territory_opacity",
        "territory_padding_meters",
        "stroke_color",
        "stroke_width",
        "fill_opacity",
        "enabled",
        "sequence",
    ]
    if frappe.db.has_column("Expedition Layer", "pin_min_zoom"):
        _lm_fields.insert(_lm_fields.index("cluster"), "pin_min_zoom")
    if frappe.db.has_column("Expedition Layer", "heatmap_weight_stops_json"):
        _lm_fields.insert(_lm_fields.index("heatmap_radius_min"), "heatmap_weight_stops_json")
    if frappe.db.has_column("Expedition Layer", "heatmap_intensity_min"):
        _lm_fields.insert(_lm_fields.index("heatmap_intensity_max"), "heatmap_intensity_min")

    raw = frappe.get_all(
        "Expedition Layer",
        filters={"map": ["in", ["", None]]},
        fields=_lm_fields,
        order_by="source_doctype asc, title asc",
    )
    out = []
    for r in raw:
        r["group_config"] = _coerce_group_config_for_client(r.get("group_config_json"))
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
    assert_map_write(map_name)

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
            "location_source": getattr(master, "location_source", None) or "Direct Fields",
            "location_link_field": getattr(master, "location_link_field", None) or "",
            "location_doctype": getattr(master, "location_doctype", None) or "",
            "location_reverse_link_field": getattr(master, "location_reverse_link_field", None) or "",
            "location_fields_json": getattr(master, "location_fields_json", None) or "",
            "latitude_field": master.latitude_field,
            "longitude_field": master.longitude_field,
            "label_field": master.label_field,
            "color": master.color,
            "icon": master.icon,
            "size": master.size,
            "pin_min_zoom": getattr(master, "pin_min_zoom", 0) or 0,
            "cluster": master.cluster,
            "heatmap": master.heatmap,
            "heatmap_mode": master.heatmap_mode or "count",
            "heatmap_weight_field": master.heatmap_weight_field or "",
            "heatmap_weight_min": master.heatmap_weight_min,
            "heatmap_weight_max": master.heatmap_weight_max,
            "heatmap_weight_scale": master.heatmap_weight_scale or "linear",
            "heatmap_weight_stops_json": master.heatmap_weight_stops_json or "",
            "heatmap_radius_min": master.heatmap_radius_min or 10,
            "heatmap_radius_max": master.heatmap_radius_max or 30,
            "heatmap_intensity_min": master.heatmap_intensity_min or 1,
            "heatmap_intensity_max": master.heatmap_intensity_max or 2.5,
            "heatmap_opacity": master.heatmap_opacity if master.heatmap_opacity is not None else 0.75,
            "heatmap_ramp_json": master.heatmap_ramp_json or "",
            "territory_enabled": getattr(master, "territory_enabled", 0),
            "territory_color": getattr(master, "territory_color", None) or "",
            "territory_opacity": getattr(master, "territory_opacity", None) if getattr(master, "territory_opacity", None) is not None else 0.18,
            "territory_padding_meters": getattr(master, "territory_padding_meters", None) or 2500,
            "enabled": 1,
            "filter_json": master.filter_json,
            "stroke_color": master.stroke_color,
            "stroke_width": master.stroke_width,
            "fill_opacity": master.fill_opacity,
            "popup_template": master.popup_template,
            "popup_fields_json": master.popup_fields_json,
            "linked_metrics_json": getattr(master, "linked_metrics_json", None) or "",
            "linked_metric_filters_json": getattr(master, "linked_metric_filters_json", None) or "",
            "group_by_field": master.group_by_field,
            "group_config_json": master.group_config_json,
            "click_action": master.click_action,
            "use_source_permissions": master.use_source_permissions,
            "sequence": int(last_seq),
        }
    )
    _sync_filter_child_table_from_json(doc, master.filter_json)
    doc.insert(ignore_permissions=True)
    return _layer_to_dto(doc)


@frappe.whitelist()
def get_list_view_fields(source_doctype: str) -> list[dict]:
    """Return the list view fields configured for a source DocType."""
    assert_source_read(source_doctype)
    meta = frappe.get_meta(source_doctype)
    fields = []

    # Check for fields configured for list view in doctype
    for df in meta.fields:
        if df.in_list_view and not df.hidden:
            fields.append({
                "fieldname": df.fieldname,
                "label": df.label or df.fieldname,
                "fieldtype": df.fieldtype,
                "options": df.options,
            })

    # Default fallback if no specific list view fields exist
    if not fields:
        fields = [
            {"fieldname": "name", "label": "ID", "fieldtype": "Link", "options": source_doctype},
            {"fieldname": "modified", "label": "Modified", "fieldtype": "Datetime"},
        ]
    return fields
