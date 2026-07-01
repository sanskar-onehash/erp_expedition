import json
from typing import Any

import frappe


OPERATOR_TO_LABEL = {
    "=": "equals",
    "==": "equals",
    "!=": "not equals",
    "like": "like",
    "not like": "not like",
    "in": "in",
    "not in": "not in",
    ">": "greater than",
    ">=": "greater or equal",
    "<": "less than",
    "<=": "less or equal",
    "between": "between",
    "is": "is",
}


def parse_json(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def operator_label(operator: Any) -> str:
    key = str(operator or "=").strip().lower()
    return OPERATOR_TO_LABEL.get(key, key or "equals")


def source_field_meta(source_doctype: str | None, fieldname: str | None) -> dict:
    if not source_doctype or not fieldname:
        return {}
    try:
        meta = frappe.get_meta(source_doctype)
    except Exception:
        return {}
    field = meta.get_field(fieldname)
    if not field:
        return {}
    return {
        "label": field.label or field.fieldname,
        "fieldtype": field.fieldtype,
    }


def has_child_rows(child_doctype: str, parent: str, parentfield: str) -> bool:
    return bool(
        frappe.db.exists(
            child_doctype,
            {
                "parent": parent,
                "parentfield": parentfield,
            },
        )
    )


def iter_filter_rows(filter_json: Any) -> list[dict]:
    parsed = parse_json(filter_json, [])
    if not isinstance(parsed, list):
        return []
    rows = []
    for raw in parsed:
        if not isinstance(raw, (list, tuple)) or len(raw) not in (2, 3):
            continue
        if len(raw) == 2:
            fieldname, value = raw
            operator = "="
        else:
            fieldname, operator, value = raw
        if not fieldname:
            continue
        row = {
            "fieldname": str(fieldname),
            "operator": operator_label(operator),
            "value": "",
            "from_value": "",
            "to_value": "",
        }
        if str(operator or "").strip().lower() == "between" and isinstance(value, list):
            row["from_value"] = "" if len(value) < 1 or value[0] is None else str(value[0])
            row["to_value"] = "" if len(value) < 2 or value[1] is None else str(value[1])
        elif isinstance(value, (list, tuple)):
            row["value"] = ", ".join(str(item) for item in value)
        elif value is not None:
            row["value"] = str(value)
        rows.append(row)
    return rows
