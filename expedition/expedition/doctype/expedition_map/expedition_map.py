# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


OPERATOR_ALIASES = {
    "equals": "=",
    "equal": "=",
    "not equals": "!=",
    "not equal": "!=",
    "greater than": ">",
    "greater or equal": ">=",
    "greater than or equal": ">=",
    "less than": "<",
    "less or equal": "<=",
    "less than or equal": "<=",
    "contains": "like",
    "not contains": "not like",
}


def _canonical_operator(operator):
    op = str(operator or "=").strip()
    return OPERATOR_ALIASES.get(op.lower(), op)


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

        # Serialize Layer Filters child table into hidden filters_json.
        self._sync_filters_json_from_child_table()

    def _sync_filters_json_from_child_table(self):
        """Serialize the Layer Filters child table rows into filters_json."""
        rows = self.get("layer_filters") or []
        if not rows:
            self.filters_json = ""
            return
        result = []
        for row in rows:
            layer = row.layer or ""
            field = row.fieldname or ""
            op = _canonical_operator(row.operator)
            value = row.value or ""
            if layer and field:
                result.append(
                    {"layer": layer, "fieldname": field, "operator": op, "value": value}
                )
        self.filters_json = frappe.json.dumps(result) if result else ""

    def on_update(self):
        # Ensure hidden JSON field is kept in sync after save.
        self._sync_filters_json_from_child_table()
        if self.filters_json is not None:
            frappe.db.set_value(
                self.doctype,
                self.name,
                {"filters_json": self.filters_json or ""},
                update_modified=False,
            )
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
