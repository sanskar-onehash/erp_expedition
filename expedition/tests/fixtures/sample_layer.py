# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Seed a minimal Expedition Layer so /expedition has real pins on first
load. Idempotent — safe to run repeatedly.

The seed attaches to the Expedition Map by title (resolved to name at
runtime so a fresh install with different random names still works).
The source DocType is the standard Frappe `Location`, which ships with
~4,200 rows of Indian districts + sub-districts with separate latitude
and longitude Float fields — a DocType available in the bench that
satisfies the layer contract.

Usage:
    bench --site <site> execute expedition.tests.fixtures.sample_layer.execute
"""

import frappe


# Color is a warm orange — high contrast on the OpenFreeMap positron
# (light) style. The Frappe Color field stores hex without '#'.
SEED_LAYERS = [
    {
        "title": "Indian districts",
        "target_map_title": "Expedition",
        "source_doctype": "Location",
        "latitude_field": "latitude",
        "longitude_field": "longitude",
        "label_field": "location_name",
        "color": "#FF6B35",
        "size": "m",
        "cluster": 1,
        "enabled": 1,
        "sequence": 1,
        "use_source_permissions": 1,
    },
]


def _resolve_map_name(target_title: str) -> str | None:
    """Resolve an Expedition Map by its title to its (random) name."""
    name = frappe.db.get_value("Expedition Map", {"title": target_title}, "name")
    return name


def _ensure(payload: dict) -> bool:
    """Insert one Expedition Layer doc if no doc with the same title
    exists on the target map. Returns True if a new doc was inserted.
    """
    target_map = payload.pop("target_map_title", None)
    if target_map:
        map_name = _resolve_map_name(target_map)
        if not map_name:
            return False
        payload["map"] = map_name

    title = payload["title"]
    if frappe.db.exists(
        "Expedition Layer", {"title": title, "map": payload.get("map")}
    ):
        return False
    doc = frappe.new_doc("Expedition Layer")
    doc.update(payload)
    doc.insert(ignore_permissions=True)
    return True


def execute() -> None:
    inserted = 0
    for payload in SEED_LAYERS:
        if _ensure(dict(payload)):
            inserted += 1
    frappe.db.commit()
    print(
        f"Seeded {inserted} new Expedition Layer docs (of {len(SEED_LAYERS)} requested)."
    )
