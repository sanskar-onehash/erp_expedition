# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpeditionMap(Document):
    """
    A saved map configuration. Layers, zones, viewport, basemap, filters
    are all part of this document (or linked from it).

    The user almost never opens this DocType's form view. The primary
    flow is: build a map on the /app/expedition canvas, then click Save.
    The form view is for power users who want to see the raw config.
    """

    def validate(self):
        # JSON fields must be valid JSON.
        # so we sanity-check parseability here so the API endpoints don't have to.
        for field in ("filters_json", "summary_json", "viewport"):
            value = self.get(field)
            if value and isinstance(value, str):
                try:
                    frappe.parse_json(value)
                except Exception:
                    frappe.throw(f"{field} is not valid JSON")

        if not self.owner_user and self.is_new():
            self.owner_user = frappe.session.user

    def on_update(self):
        # Touch the title to force re-index on rename. The DocType is
        # allow_rename=1, so we need search to reflect renames.
        frappe.flags.title_updated = True


@frappe.whitelist()
def touch_last_opened(name: str) -> None:
    """Called by the frontend whenever a user opens a map. Cheap update."""
    frappe.db.set_value(
        "Expedition Map",
        name,
        "last_opened_at",
        frappe.utils.now(),
        update_modified=False,
    )
