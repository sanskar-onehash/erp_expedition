import frappe

from expedition.patches.v0_3_0._child_table_migration import (
    has_child_rows,
    operator_label,
    parse_json,
    source_field_meta,
)


def execute():
    if not frappe.db.table_exists("Expedition Map Filter"):
        return

    maps = frappe.get_all(
        "Expedition Map",
        fields=["name", "filters_json"],
        filters={"filters_json": ["is", "set"]},
    )
    for map_doc in maps:
        if has_child_rows("Expedition Map Filter", map_doc.name, "layer_filters"):
            continue
        filters = parse_json(map_doc.filters_json, [])
        if not isinstance(filters, list):
            continue
        doc = frappe.get_doc("Expedition Map", map_doc.name)
        for row in filters:
            if not isinstance(row, dict):
                continue
            layer_name = row.get("layer")
            fieldname = row.get("fieldname") or row.get("field")
            if not layer_name or not fieldname:
                continue
            source_doctype = frappe.db.get_value(
                "Expedition Layer", layer_name, "source_doctype"
            )
            meta = source_field_meta(source_doctype, fieldname)
            doc.append(
                "layer_filters",
                {
                    "layer": layer_name,
                    "fieldname": fieldname,
                    "label": meta.get("label") or fieldname,
                    "operator": operator_label(row.get("operator") or row.get("op")),
                    "value": "" if row.get("value") is None else str(row.get("value")),
                },
            )
        if doc.get("layer_filters"):
            doc.save(ignore_permissions=True)
