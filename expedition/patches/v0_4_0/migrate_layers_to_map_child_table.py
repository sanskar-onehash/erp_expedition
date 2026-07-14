"""Migrate Expedition Layer.map (FK) → Expedition Map.layers child table.

Before: each layer had a `map` link field pointing to one Expedition Map.
After:  layers are free (map=NULL); Expedition Map has a `layers` child
        table (Expedition Map Layer) listing which layers belong to it,
        along with per-map sequence and enabled values.

This patch is idempotent — it skips maps that already have child rows.
"""

import frappe


def execute():
    if not frappe.db.table_exists("Expedition Map Layer"):
        # DocType hasn't been synced yet — nothing to do
        return

    layers = frappe.get_all(
        "Expedition Layer",
        filters={"map": ["not in", ["", None]]},
        fields=["name", "map", "sequence", "enabled"],
        order_by="map asc, sequence asc",
    )

    if not layers:
        return

    # Group by map
    by_map: dict[str, list] = {}
    for layer in layers:
        by_map.setdefault(layer.map, []).append(layer)

    for map_name, map_layers in by_map.items():
        if not frappe.db.exists("Expedition Map", map_name):
            continue

        map_doc = frappe.get_doc("Expedition Map", map_name)

        # Skip if junction rows already exist (idempotent)
        existing = {row.layer for row in map_doc.get("layers", [])}
        changed = False
        for layer in map_layers:
            if layer.name in existing:
                continue
            map_doc.append(
                "layers",
                {
                    "layer": layer.name,
                    "sequence": layer.sequence or 0,
                    "enabled": layer.enabled if layer.enabled is not None else 1,
                },
            )
            changed = True

        if changed:
            map_doc.save(ignore_permissions=True)

    # Clear the legacy map field on all migrated layers
    frappe.db.sql(
        "UPDATE `tabExpedition Layer` SET `map` = '' WHERE `map` IS NOT NULL AND `map` != ''"
    )
    frappe.db.commit()
