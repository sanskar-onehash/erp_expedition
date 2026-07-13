# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


OPERATOR_ALIASES = {
    "equals": "=",
    "equal": "=",
    "not equals": "!=",
    "not equal": "!=",
    "is not": "!=",
    "greater than": ">",
    "greater or equal": ">=",
    "greater than or equal": ">=",
    "less than": "<",
    "less or equal": "<=",
    "less than or equal": "<=",
    "contains": "like",
    "not contains": "not like",
    "is set": "is",
    "is not set": "is",
}


def _canonical_operator(operator):
    op = str(operator or "=").strip()
    return OPERATOR_ALIASES.get(op.lower(), op)


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
        self._sync_filter_json_from_child_table()
        self._sync_group_config_json_from_child_table()
        self._validate_filter_json()
        self._validate_heatmap()
        self._validate_linked_metrics()
        self._validate_linked_metric_filters()
        self._validate_location_fields()

    def _sync_filter_json_from_child_table(self):
        """Serialize the Filters child table rows into filter_json."""
        rows = self.get("filters") or []
        if not rows:
            return
        result = []
        for row in rows:
            field = row.fieldname or ""
            op = _canonical_operator(row.operator)
            value = row.value or ""
            if op == "between":
                fv = row.from_value or ""
                tv = row.to_value or ""
                value = [fv, tv] if fv or tv else ""
            label_op = str(row.operator or "").strip().lower()
            if label_op == "is not set":
                op = "is"
                value = "not set"
            elif op == "is":
                value = "not set" if label_op == "is not set" else (value or "set")
            if field:
                result.append([field, op, value])
        self.filter_json = frappe.json.dumps(result) if result else ""

    def _sync_group_config_json_from_child_table(self):
        """Serialize the Groups child table rows into group_config_json."""
        rows = self.get("groups") or []
        if not rows:
            return
        cfg = {}
        for g in rows:
            value = g.group_value or ""
            if not value:
                continue
            entry = {}
            if g.color:
                entry["color"] = g.color
            if g.icon:
                entry["icon"] = g.icon
            if g.label:
                entry["label"] = g.label
            if entry:
                cfg[value] = entry
        self.group_config_json = frappe.json.dumps(cfg) if cfg else ""

    def on_update(self):
        """Ensure hidden JSON fields are kept in sync after save."""
        self._sync_filter_json_from_child_table()
        self._sync_group_config_json_from_child_table()
        if self.filter_json or self.group_config_json:
            frappe.db.set_value(
                self.doctype,
                self.name,
                {
                    "filter_json": self.filter_json or "",
                    "group_config_json": self.group_config_json or "",
                },
                update_modified=False,
            )

    def _validate_source_doctype(self):
        if not self.source_doctype:
            return
        source_meta = frappe.get_meta(self.source_doctype)
        location_source = self.location_source or "Direct Fields"
        location_doctype = self.source_doctype

        if location_source == "Linked DocType":
            link_fieldname = (self.location_link_field or "").strip()
            if not link_fieldname:
                frappe.throw("Location Link Field is required for linked locations")
            link_field = source_meta.get_field(link_fieldname)
            if not link_field or link_field.fieldtype != "Link" or not link_field.options:
                frappe.throw(
                    f"Source DocType {self.source_doctype} has no Link field '{link_fieldname}'"
                )
            self.location_doctype = self.location_doctype or link_field.options
            if self.location_doctype != link_field.options:
                frappe.throw(
                    f"Location DocType must match {link_fieldname}'s target DocType ({link_field.options})"
                )
            location_doctype = self.location_doctype
        elif location_source == "Reverse Linked DocType":
            if not self.location_doctype:
                frappe.throw("Location DocType is required for reverse linked locations")
            reverse_fieldname = (self.location_reverse_link_field or "").strip()
            if not reverse_fieldname:
                frappe.throw("Reverse Link Field is required for reverse linked locations")
            location_meta = frappe.get_meta(self.location_doctype)
            reverse_field = location_meta.get_field(reverse_fieldname)
            if (
                not reverse_field
                or reverse_field.fieldtype != "Link"
                or reverse_field.options != self.source_doctype
            ):
                frappe.throw(
                    f"Location DocType {self.location_doctype} has no Link field '{reverse_fieldname}' pointing to {self.source_doctype}"
                )
            location_doctype = self.location_doctype
        elif location_source == "Dynamic Link DocType":
            if not self.location_doctype:
                frappe.throw("Location DocType is required for dynamic linked locations")
            if not frappe.db.exists("DocType", "Dynamic Link"):
                frappe.throw("Dynamic Link DocType is not available on this site")
            location_doctype = self.location_doctype

        meta = frappe.get_meta(location_doctype)
        lat = meta.get_field(self.latitude_field)
        lng = meta.get_field(self.longitude_field)
        if not lat or lat.fieldtype != "Float":
            frappe.throw(
                f"Location DocType {location_doctype} has no Float field '{self.latitude_field}'"
            )
        if not lng or lng.fieldtype != "Float":
            frappe.throw(
                f"Location DocType {location_doctype} has no Float field '{self.longitude_field}'"
            )

    def _validate_filter_json(self):
        if not self.filter_json:
            return
        try:
            frappe.parse_json(self.filter_json)
        except Exception:
            frappe.throw("Filter is not valid JSON")
        from expedition.api.layer import validate_filter_json

        validate_filter_json(self.source_doctype, self.filter_json)

    def _validate_heatmap(self):
        if not self.source_doctype:
            return
        from expedition.api.layer import (
            NUMERIC_FIELD_TYPES,
            _validate_heatmap_weight_range,
        )

        mode = (self.heatmap_mode or "count").strip().lower()
        weight_field = (self.heatmap_weight_field or "").strip()
        if mode == "count":
            weight_field = ""
            self.heatmap_weight_field = ""
        if weight_field:
            if weight_field.startswith("_metric_"):
                from expedition.api.layer import linked_metric_property_names

                if weight_field not in linked_metric_property_names(
                    self.linked_metrics_json
                ):
                    frappe.throw(
                        f"Heatmap metric field '{weight_field}' is not defined in Linked Metrics"
                    )
            else:
                meta = frappe.get_meta(self.source_doctype)
                field = meta.get_field(weight_field)
                if not field or field.fieldtype not in NUMERIC_FIELD_TYPES:
                    frappe.throw(
                        f"Heatmap metric field '{weight_field}' must be numeric on {self.source_doctype}"
                    )
            _validate_heatmap_weight_range(
                weight_field,
                self.heatmap_weight_min,
                self.heatmap_weight_max,
            )

    def _validate_linked_metrics(self):
        if not self.linked_metrics_json:
            return
        try:
            metrics = frappe.parse_json(self.linked_metrics_json)
        except Exception:
            frappe.throw("Linked Metrics must be valid JSON")
        if not isinstance(metrics, list):
            frappe.throw("Linked Metrics must be a JSON array")

        from expedition.api.layer import validate_linked_metrics_json

        validate_linked_metrics_json(self.source_doctype, self.linked_metrics_json)

    def _validate_linked_metric_filters(self):
        if not self.linked_metric_filters_json:
            return
        try:
            filters = frappe.parse_json(self.linked_metric_filters_json)
        except Exception:
            frappe.throw("Linked Metric Filters must be valid JSON")
        if not isinstance(filters, list):
            frappe.throw("Linked Metric Filters must be a JSON array")

        from expedition.api.layer import validate_linked_metric_filters_json

        validate_linked_metric_filters_json(
            self.linked_metrics_json,
            self.linked_metric_filters_json,
        )

    def _validate_location_fields(self):
        if not self.location_fields_json:
            return
        from expedition.api.layer import validate_location_fields_json

        validate_location_fields_json(self, self.location_fields_json)
