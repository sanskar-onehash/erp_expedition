# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpeditionLayer(Document):
    """
    A visible data layer on a saved Expedition Map.

    A layer binds:
      - a source DocType (e.g. Customer, Lead, Item)
      - the latitude/longitude field names on that DocType
      - style (color, icon, size, clustering, heatmap)
      - an optional filter (JSON, applied on the source)
      - an optional popup template (Jinja over the source row)

    Hard rules enforced server-side:
      - The source DocType must have latitude and longitude Float fields
        (validated on save to fail fast, not at render time).
      - Every read goes through frappe.has_permission on the source DocType.
    """

    def validate(self):
        self._validate_source_doctype()
        self._validate_filter_json()

    def _validate_source_doctype(self):
        if not self.source_doctype:
            return
        meta = frappe.get_meta(self.source_doctype)
        lat = meta.get_field(self.latitude_field)
        lng = meta.get_field(self.longitude_field)
        if not lat or lat.fieldtype != "Float":
            frappe.throw(
                f"Source DocType {self.source_doctype} has no Float field '{self.latitude_field}'"
            )
        if not lng or lng.fieldtype != "Float":
            frappe.throw(
                f"Source DocType {self.source_doctype} has no Float field '{self.longitude_field}'"
            )

    def _validate_filter_json(self):
        if not self.filter_json:
            return
        try:
            frappe.parse_json(self.filter_json)
        except Exception:
            frappe.throw("Filter is not valid JSON")
