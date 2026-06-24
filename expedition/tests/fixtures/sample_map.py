# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Seed a minimal Expedition Map template so /expedition has something to
render on first load. Idempotent — safe to run repeatedly.

Usage:
    bench --site <site> execute expedition.tests.fixtures.sample_map.execute
"""

import json

import frappe


SEED_MAPS = [
    {
        "title": "India — Default",
        "template_category": "Demo",
        "basemap_style": "light",
        # Zoom 5.5 puts the country at ~70% of viewport width so
        # individual district pins are clearly visible (Apple-tier
        # default for a country-level demo). 4,179 districts cluster
        # cleanly at this zoom and break into singletons as you zoom
        # in.
        "viewport": json.dumps(
            {"center": [78.9629, 20.5937], "zoom": 5.5, "bearing": 0, "pitch": 0}
        ),
        "is_template": 1,
        "is_public": 1,
    },
    {
        "title": "World — Default",
        "template_category": "Demo",
        "basemap_style": "dark",
        "viewport": json.dumps(
            {"center": [0.0, 20.0], "zoom": 2, "bearing": 0, "pitch": 0}
        ),
        "is_template": 1,
        "is_public": 1,
    },
]


def _ensure(payload: dict) -> None:
    title = payload["title"]
    if frappe.db.exists("Expedition Map", {"title": title, "is_template": 1}):
        return
    doc = frappe.new_doc("Expedition Map")
    doc.update(payload)
    doc.insert(ignore_permissions=True)


def execute() -> None:
    for payload in SEED_MAPS:
        _ensure(payload)
    frappe.db.commit()
    print(f"Seeded {len(SEED_MAPS)} Expedition Map templates.")
