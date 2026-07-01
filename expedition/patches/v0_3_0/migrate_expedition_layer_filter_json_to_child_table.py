import frappe

from expedition.patches.v0_3_0._child_table_migration import (
    has_child_rows,
    iter_filter_rows,
    source_field_meta,
)


def execute():
    if not frappe.db.table_exists("Expedition Layer Filter"):
        return

    layers = frappe.get_all(
        "Expedition Layer",
        fields=["name", "source_doctype", "filter_json"],
        filters={"filter_json": ["is", "set"]},
    )
    for layer in layers:
        if has_child_rows("Expedition Layer Filter", layer.name, "filters"):
            continue
        rows = iter_filter_rows(layer.filter_json)
        if not rows:
            continue
        doc = frappe.get_doc("Expedition Layer", layer.name)
        for row in rows:
            meta = source_field_meta(layer.source_doctype, row["fieldname"])
            doc.append(
                "filters",
                {
                    **row,
                    "label": meta.get("label") or row["fieldname"],
                    "fieldtype": meta.get("fieldtype") or "",
                    "condition": "AND",
                },
            )
        doc.save(ignore_permissions=True)
