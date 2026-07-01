import frappe

from expedition.patches.v0_3_0._child_table_migration import has_child_rows, parse_json


def execute():
    if not frappe.db.table_exists("Expedition Layer Group"):
        return

    layers = frappe.get_all(
        "Expedition Layer",
        fields=["name", "group_config_json"],
        filters={"group_config_json": ["is", "set"]},
    )
    for layer in layers:
        if has_child_rows("Expedition Layer Group", layer.name, "groups"):
            continue
        config = parse_json(layer.group_config_json, {})
        if not isinstance(config, dict):
            continue
        doc = frappe.get_doc("Expedition Layer", layer.name)
        for value, entry in config.items():
            if not isinstance(entry, dict):
                continue
            doc.append(
                "groups",
                {
                    "group_value": str(value),
                    "color": entry.get("color") or "#2563eb",
                    "label": entry.get("label") or "",
                    "icon": entry.get("icon") or "",
                },
            )
        if doc.get("groups"):
            doc.save(ignore_permissions=True)
