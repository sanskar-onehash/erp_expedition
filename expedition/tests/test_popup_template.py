"""Tests for server-side popup_template rendering on Expedition Layer.

The popup_template is a Jinja2 string stored on the Layer doc. It is
rendered server-side per row and attached as `_popup_html` on each
GeoJSON feature's properties. These tests cover the four behaviours:

  1. Empty template → no `_popup_html` key on features.
  2. Non-empty template referencing source row fields → rendered HTML
     lands in `_popup_html` with the right substitutions.
  3. Template that uses a forbidden attribute access (`.__class__`) is
     rejected by Frappe's `safe_render=True` default in `render_template`.
  4. The full row context (any field on the source doc) is available
     to the template, even fields not in the narrow get_all() `fields`
     list.

The test creates a tiny in-memory DocType `Expedition Test Pin` in
setUp, points a Layer at it, runs `get_features()`, and tears both
down. No demo data is required.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.exceptions import ValidationError

from expedition.api import layer as layer_api


TEST_DOCTYPE = "Expedition Test Pin"
TEST_LINKED_SOURCE_DOCTYPE = "Expedition Test Linked Source"
TEST_REVERSE_LOCATION_DOCTYPE = "Expedition Test Reverse Location"
TEST_DYNAMIC_LOCATION_DOCTYPE = "Expedition Test Dynamic Location"
TEST_PAYMENT_DOCTYPE = "Expedition Test Payment"
TEST_DYNAMIC_PAYMENT_DOCTYPE = "Expedition Test Dynamic Payment"
TEST_DOCTYPE_FIELDS = [
    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1},
    {
        "fieldname": "category",
        "fieldtype": "Select",
        "label": "Category",
        "options": "Customer\nLead\nArchitect",
    },
    {"fieldname": "city", "fieldtype": "Data", "label": "City"},
    {"fieldname": "latitude", "fieldtype": "Float", "label": "Latitude", "reqd": 1},
    {"fieldname": "longitude", "fieldtype": "Float", "label": "Longitude", "reqd": 1},
]
TEST_LINKED_SOURCE_FIELDS = [
    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1},
    {
        "fieldname": "pin",
        "fieldtype": "Link",
        "label": "Pin",
        "options": TEST_DOCTYPE,
        "reqd": 1,
    },
]
TEST_REVERSE_LOCATION_FIELDS = [
    {
        "fieldname": "pin",
        "fieldtype": "Link",
        "label": "Pin",
        "options": TEST_DOCTYPE,
        "reqd": 1,
    },
    {"fieldname": "latitude", "fieldtype": "Float", "label": "Latitude", "reqd": 1},
    {"fieldname": "longitude", "fieldtype": "Float", "label": "Longitude", "reqd": 1},
]
TEST_DYNAMIC_LOCATION_FIELDS = [
    {"fieldname": "latitude", "fieldtype": "Float", "label": "Latitude", "reqd": 1},
    {"fieldname": "longitude", "fieldtype": "Float", "label": "Longitude", "reqd": 1},
    {"fieldname": "city", "fieldtype": "Data", "label": "City"},
    {
        "fieldname": "links",
        "fieldtype": "Table",
        "label": "Links",
        "options": "Dynamic Link",
    },
]
TEST_PAYMENT_FIELDS = [
    {
        "fieldname": "pin",
        "fieldtype": "Link",
        "label": "Pin",
        "options": TEST_DOCTYPE,
        "reqd": 1,
    },
    {"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
    {"fieldname": "status", "fieldtype": "Data", "label": "Status"},
]
TEST_DYNAMIC_PAYMENT_FIELDS = [
    {
        "fieldname": "party_type",
        "fieldtype": "Select",
        "label": "Party Type",
        "options": f"{TEST_DOCTYPE}\nOther",
        "reqd": 1,
    },
    {
        "fieldname": "party",
        "fieldtype": "Dynamic Link",
        "label": "Party",
        "options": "party_type",
        "reqd": 1,
    },
    {"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
    {"fieldname": "status", "fieldtype": "Data", "label": "Status"},
]


def _ensure_test_doctype() -> None:
    for doctype, fields in (
        (TEST_DOCTYPE, TEST_DOCTYPE_FIELDS),
        (TEST_LINKED_SOURCE_DOCTYPE, TEST_LINKED_SOURCE_FIELDS),
        (TEST_REVERSE_LOCATION_DOCTYPE, TEST_REVERSE_LOCATION_FIELDS),
        (TEST_DYNAMIC_LOCATION_DOCTYPE, TEST_DYNAMIC_LOCATION_FIELDS),
        (TEST_PAYMENT_DOCTYPE, TEST_PAYMENT_FIELDS),
        (TEST_DYNAMIC_PAYMENT_DOCTYPE, TEST_DYNAMIC_PAYMENT_FIELDS),
    ):
        if frappe.db.exists("DocType", doctype):
            continue
        doc = frappe.get_doc(
            {
                "doctype": "DocType",
                "name": doctype,
                "module": "Expedition",
                "custom": 1,
                "is_virtual": 0,
                "autoname": "hash",
                "fields": fields,
                "permissions": [
                    {
                        "role": "System Manager",
                        "read": 1,
                        "write": 1,
                        "create": 1,
                        "delete": 1,
                    }
                ],
            }
        )
        doc.insert(ignore_permissions=True)


def _drop_test_doctype() -> None:
    for doctype in (
        TEST_REVERSE_LOCATION_DOCTYPE,
        TEST_DYNAMIC_LOCATION_DOCTYPE,
        TEST_DYNAMIC_PAYMENT_DOCTYPE,
        TEST_PAYMENT_DOCTYPE,
        TEST_LINKED_SOURCE_DOCTYPE,
        TEST_DOCTYPE,
    ):
        if frappe.db.exists("DocType", doctype):
            frappe.delete_doc("DocType", doctype, ignore_permissions=True, force=True)


def _make_pin(title, lat, lng, category="Customer", city=None):
    doc = frappe.get_doc(
        {
            "doctype": TEST_DOCTYPE,
            "title": title,
            "latitude": lat,
            "longitude": lng,
            "category": category,
            "city": city or "",
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _make_layer(popup_template=""):
    return frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test layer for popup_template",
            "source_doctype": TEST_DOCTYPE,
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
            "popup_template": popup_template,
        }
    ).insert(ignore_permissions=True)


def _make_layer_with_popup_fields(fields):
    doc = _make_layer(popup_template="")
    doc.popup_fields_json = frappe.as_json(fields)
    doc.save(ignore_permissions=True)
    return doc


def _make_linked_source(title, pin_name):
    doc = frappe.get_doc(
        {
            "doctype": TEST_LINKED_SOURCE_DOCTYPE,
            "title": title,
            "pin": pin_name,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _make_linked_location_layer():
    return frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test layer for popup_template",
            "source_doctype": TEST_LINKED_SOURCE_DOCTYPE,
            "location_source": "Linked DocType",
            "location_link_field": "pin",
            "location_doctype": TEST_DOCTYPE,
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
        }
    ).insert(ignore_permissions=True)


def _make_reverse_location(pin_name, lat, lng):
    doc = frappe.get_doc(
        {
            "doctype": TEST_REVERSE_LOCATION_DOCTYPE,
            "pin": pin_name,
            "latitude": lat,
            "longitude": lng,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _make_reverse_location_layer():
    return frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test layer for popup_template",
            "source_doctype": TEST_DOCTYPE,
            "location_source": "Reverse Linked DocType",
            "location_doctype": TEST_REVERSE_LOCATION_DOCTYPE,
            "location_reverse_link_field": "pin",
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
        }
    ).insert(ignore_permissions=True)


def _make_dynamic_location(pin_name, lat, lng, city=""):
    doc = frappe.get_doc(
        {
            "doctype": TEST_DYNAMIC_LOCATION_DOCTYPE,
            "latitude": lat,
            "longitude": lng,
            "city": city,
            "links": [
                {
                    "link_doctype": TEST_DOCTYPE,
                    "link_name": pin_name,
                }
            ],
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _make_dynamic_location_layer(popup_template=""):
    return frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test layer for popup_template",
            "source_doctype": TEST_DOCTYPE,
            "location_source": "Dynamic Link DocType",
            "location_doctype": TEST_DYNAMIC_LOCATION_DOCTYPE,
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
            "location_fields_json": frappe.as_json(["city"]),
            "popup_template": popup_template,
        }
    ).insert(ignore_permissions=True)


def _make_payment(pin_name, amount, status="Open"):
    doc = frappe.get_doc(
        {
            "doctype": TEST_PAYMENT_DOCTYPE,
            "pin": pin_name,
            "amount": amount,
            "status": status,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _make_dynamic_payment(pin_name, amount, status="Open", party_type=TEST_DOCTYPE):
    doc = frappe.get_doc(
        {
            "doctype": TEST_DYNAMIC_PAYMENT_DOCTYPE,
            "party_type": party_type,
            "party": pin_name,
            "amount": amount,
            "status": status,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def _make_metric_layer(popup_template=""):
    return frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test layer for popup_template",
            "source_doctype": TEST_DOCTYPE,
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
            "popup_template": popup_template,
            "linked_metrics_json": frappe.as_json(
                [
                    {
                        "key": "open_amount",
                        "source_doctype": TEST_PAYMENT_DOCTYPE,
                        "link_field": "pin",
                        "aggregate": "sum",
                        "field": "amount",
                        "filters": [["status", "=", "Open"]],
                    },
                    {
                        "key": "payment_count",
                        "source_doctype": TEST_PAYMENT_DOCTYPE,
                        "link_field": "pin",
                        "aggregate": "count",
                    },
                ]
            ),
        }
    ).insert(ignore_permissions=True)


def _make_dynamic_metric_layer():
    return frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test layer for popup_template",
            "source_doctype": TEST_DOCTYPE,
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
            "linked_metrics_json": frappe.as_json(
                [
                    {
                        "key": "dynamic_amount",
                        "source_doctype": TEST_DYNAMIC_PAYMENT_DOCTYPE,
                        "link_field": "party",
                        "dynamic_link_doctype_field": "party_type",
                        "aggregate": "sum",
                        "field": "amount",
                        "filters": [["status", "=", "Open"]],
                    },
                ]
            ),
        }
    ).insert(ignore_permissions=True)


def _make_metric_filter_layer():
    doc = _make_metric_layer()
    doc.linked_metric_filters_json = frappe.as_json(
        [{"metric": "open_amount", "operator": ">", "value": 100}]
    )
    doc.save(ignore_permissions=True)
    return doc


class TestPopupTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_test_doctype()

    @classmethod
    def tearDownClass(cls):
        _drop_test_doctype()

    def setUp(self):
        self.pin_a = _make_pin("AlphaTestPin", 10.0, 20.0, city="Delhi")
        self.pin_b = _make_pin("BravoTestPin", 11.0, 21.0, city="Mumbai")

    def tearDown(self):
        for pin in (self.pin_a, self.pin_b):
            if frappe.db.exists(TEST_DOCTYPE, pin.name):
                frappe.delete_doc(
                    TEST_DOCTYPE, pin.name, ignore_permissions=True, force=True
                )
        for layer_name in frappe.get_all(
            "Expedition Layer",
            filters={"title": "Test layer for popup_template"},
            pluck="name",
        ):
            frappe.delete_doc(
                "Expedition Layer", layer_name, ignore_permissions=True, force=True
            )

    def _find(self, features, title):
        for f in features:
            if f["properties"].get("_name") and title in str(
                f["properties"].get("_label", "")
            ):
                return f
        return None

    def test_empty_template_no_popup_key(self):
        layer = _make_layer(popup_template="")
        result = layer_api.get_features(layer=layer.name, limit=10000)
        self.assertEqual(result["type"], "FeatureCollection")
        self.assertTrue(len(result["features"]) >= 2)
        for f in result["features"]:
            self.assertNotIn("_popup_html", f["properties"])

    def test_direct_layer_bounds_use_permission_aware_get_all(self):
        layer = _make_layer(popup_template="")
        with patch.object(layer_api.frappe, "get_all", wraps=frappe.get_all) as get_all_spy:
            bounds = layer_api.get_layer_bounds(layer.name)

        self.assertEqual(bounds["south"], 10.0)
        self.assertEqual(bounds["west"], 20.0)
        self.assertEqual(bounds["north"], 11.0)
        self.assertEqual(bounds["east"], 21.0)
        self.assertEqual(bounds["count"], 2)
        source_calls = [
            call
            for call in get_all_spy.call_args_list
            if call.args and call.args[0] == TEST_DOCTYPE
        ]
        self.assertTrue(source_calls)
        source_kwargs = source_calls[-1].kwargs
        self.assertEqual(source_kwargs["fields"], ["latitude", "longitude"])
        self.assertIn(["latitude", "is", "set"], source_kwargs["filters"])
        self.assertIn(["longitude", "is", "set"], source_kwargs["filters"])

    def test_group_values_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            layer_api.list_group_values(TEST_DOCTYPE, "title desc")

    def test_template_renders_per_feature(self):
        tpl = "<div class='pin'>{{ title }} — {{ doc.name }}</div>"
        layer = _make_layer(popup_template=tpl)
        result = layer_api.get_features(layer=layer.name, limit=10000)
        # Look up by `_label` (the Layer's label_field is `title`).
        titles = {f["properties"]["_label"]: f for f in result["features"]}
        self.assertIn("AlphaTestPin", titles)
        self.assertIn("BravoTestPin", titles)
        self.assertIn(
            self.pin_a.name, titles["AlphaTestPin"]["properties"]["_popup_html"]
        )
        self.assertIn(
            self.pin_b.name, titles["BravoTestPin"]["properties"]["_popup_html"]
        )

    def test_preview_popup_template_renders_draft_against_feature(self):
        layer = _make_layer(popup_template="")
        result = layer_api.preview_popup_template(
            layer=layer.name,
            popup_template="<strong>{{ title }}</strong> {{ doc.name }}",
            source_name=self.pin_a.name,
        )
        self.assertEqual(result["source_name"], self.pin_a.name)
        self.assertIn("<strong>AlphaTestPin</strong>", result["html"])
        self.assertIn(self.pin_a.name, result["html"])

    def test_preview_popup_template_blocks_dunder_access(self):
        layer = _make_layer(popup_template="")
        with self.assertRaises(ValidationError):
            layer_api.preview_popup_template(
                layer=layer.name,
                popup_template="{{ doc.__class__ }}",
                source_name=self.pin_a.name,
            )

    def test_preview_popup_template_sanitizes_unsafe_html(self):
        layer = _make_layer(popup_template="")
        result = layer_api.preview_popup_template(
            layer=layer.name,
            popup_template="<img src=x onerror=\"alert(1)\"><script>alert(2)</script>",
            source_name=self.pin_a.name,
        )
        self.assertNotIn("onerror", result["html"])
        self.assertNotIn("<script", result["html"].lower())

    def test_template_safe_render_blocks_dunder_access(self):
        # Frappe's render_template with safe_render=True (default) blocks
        # `.__` attribute traversal. A template that tries to reach
        # `doc.__class__` must throw, not silently succeed.
        tpl = "{{ doc.__class__ }}"
        layer = _make_layer(popup_template=tpl)
        with self.assertRaises(ValidationError):
            layer_api.get_features(layer=layer.name, limit=10000)

    def test_template_render_sanitizes_unsafe_html(self):
        tpl = "<div onclick=\"alert(1)\">{{ title }}</div><script>alert(2)</script>"
        layer = _make_layer(popup_template=tpl)
        result = layer_api.get_features(layer=layer.name, limit=10000)
        html = result["features"][0]["properties"]["_popup_html"]
        self.assertNotIn("onclick", html)
        self.assertNotIn("<script", html.lower())

    def test_full_row_context_exposes_all_source_fields(self):
        # If the template references a field that was NOT in the narrow
        # `fields` list passed to `get_all`, it should still resolve —
        # the row context loader pulls the full source doc.
        tpl = "title={{ title }} category={{ category }} city={{ city }}"
        layer = _make_layer(popup_template=tpl)
        result = layer_api.get_features(layer=layer.name, limit=10000)
        titles = {f["properties"]["_label"]: f for f in result["features"]}
        for title, city in (("AlphaTestPin", "Delhi"), ("BravoTestPin", "Mumbai")):
            self.assertIn(title, titles)
            html = titles[title]["properties"]["_popup_html"]
            self.assertIn(f"title={title}", html)
            self.assertIn("category=Customer", html)
            self.assertIn(f"city={city}", html)

    def test_single_popup_field_is_fetched_for_default_popup(self):
        layer = _make_layer_with_popup_fields(["city"])
        result = layer_api.get_features(layer=layer.name, limit=10000)
        titles = {f["properties"]["_label"]: f for f in result["features"]}
        self.assertEqual(titles["AlphaTestPin"]["properties"]["city"], "Delhi")
        self.assertEqual(titles["AlphaTestPin"]["properties"]["_popup_fields"], ["city"])

    def test_linked_location_layer_uses_coordinates_from_linked_doc(self):
        source = _make_linked_source("Linked Alpha", self.pin_a.name)
        try:
            layer = _make_linked_location_layer()
            result = layer_api.get_features(layer=layer.name, limit=10000)
            titles = {f["properties"]["_label"]: f for f in result["features"]}
            self.assertIn("Linked Alpha", titles)
            feature = titles["Linked Alpha"]
            self.assertEqual(feature["geometry"]["coordinates"], [20.0, 10.0])
            self.assertEqual(feature["properties"]["_name"], source.name)
            self.assertEqual(feature["properties"]["_location_doctype"], TEST_DOCTYPE)
            self.assertEqual(feature["properties"]["_location_name"], self.pin_a.name)
        finally:
            if frappe.db.exists(TEST_LINKED_SOURCE_DOCTYPE, source.name):
                frappe.delete_doc(
                    TEST_LINKED_SOURCE_DOCTYPE,
                    source.name,
                    ignore_permissions=True,
                    force=True,
                )

    def test_reverse_linked_location_layer_uses_coordinates_from_backlink_doc(self):
        location = _make_reverse_location(self.pin_a.name, 30.0, 40.0)
        try:
            layer = _make_reverse_location_layer()
            result = layer_api.get_features(layer=layer.name, limit=10000)
            titles = {f["properties"]["_label"]: f for f in result["features"]}
            self.assertIn("AlphaTestPin", titles)
            feature = titles["AlphaTestPin"]
            self.assertEqual(feature["geometry"]["coordinates"], [40.0, 30.0])
            self.assertEqual(feature["properties"]["_name"], self.pin_a.name)
            self.assertEqual(
                feature["properties"]["_location_doctype"],
                TEST_REVERSE_LOCATION_DOCTYPE,
            )
            self.assertEqual(feature["properties"]["_location_name"], location.name)
        finally:
            if frappe.db.exists(TEST_REVERSE_LOCATION_DOCTYPE, location.name):
                frappe.delete_doc(
                    TEST_REVERSE_LOCATION_DOCTYPE,
                    location.name,
                    ignore_permissions=True,
                    force=True,
                )

    def test_dynamic_link_location_layer_uses_coordinates_from_dynamic_link_parent(self):
        location = _make_dynamic_location(self.pin_a.name, 50.0, 60.0, city="Pune")
        try:
            layer = _make_dynamic_location_layer(
                popup_template="city={{ location.city }}"
            )
            result = layer_api.get_features(layer=layer.name, limit=10000)
            titles = {f["properties"]["_label"]: f for f in result["features"]}
            self.assertIn("AlphaTestPin", titles)
            feature = titles["AlphaTestPin"]
            self.assertEqual(feature["geometry"]["coordinates"], [60.0, 50.0])
            self.assertEqual(feature["properties"]["_name"], self.pin_a.name)
            self.assertEqual(
                feature["properties"]["_location_doctype"],
                TEST_DYNAMIC_LOCATION_DOCTYPE,
            )
            self.assertEqual(feature["properties"]["_location_name"], location.name)
            self.assertEqual(feature["properties"]["_location_city"], "Pune")
            self.assertEqual(feature["properties"]["_location"]["city"], "Pune")
            self.assertIn("city=Pune", feature["properties"]["_popup_html"])
        finally:
            if frappe.db.exists(TEST_DYNAMIC_LOCATION_DOCTYPE, location.name):
                frappe.delete_doc(
                    TEST_DYNAMIC_LOCATION_DOCTYPE,
                    location.name,
                    ignore_permissions=True,
                    force=True,
                )

    def test_linked_metrics_are_attached_to_features_and_popup_context(self):
        payments = [
            _make_payment(self.pin_a.name, 100, "Open"),
            _make_payment(self.pin_a.name, 50, "Open"),
            _make_payment(self.pin_a.name, 25, "Closed"),
            _make_payment(self.pin_b.name, 10, "Open"),
        ]
        try:
            layer = _make_metric_layer(
                popup_template="amount={{ metrics.open_amount }} count={{ metrics.payment_count }}"
            )
            result = layer_api.get_features(layer=layer.name, limit=10000)
            titles = {f["properties"]["_label"]: f for f in result["features"]}
            alpha = titles["AlphaTestPin"]["properties"]
            bravo = titles["BravoTestPin"]["properties"]
            self.assertEqual(alpha["_metrics"]["open_amount"], 150.0)
            self.assertEqual(alpha["_metric_payment_count"], 3)
            self.assertEqual(bravo["_metrics"]["open_amount"], 10.0)
            self.assertIn("amount=150.0", alpha["_popup_html"])
            self.assertIn("count=3", alpha["_popup_html"])
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                )

    def test_linked_metric_filters_remove_non_matching_features(self):
        payments = [
            _make_payment(self.pin_a.name, 150, "Open"),
            _make_payment(self.pin_b.name, 10, "Open"),
        ]
        try:
            layer = _make_metric_filter_layer()
            result = layer_api.get_features(layer=layer.name, limit=10000)
            titles = {f["properties"]["_label"] for f in result["features"]}
            self.assertIn("AlphaTestPin", titles)
            self.assertNotIn("BravoTestPin", titles)
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_linked_metric_group_bands_filter_features(self):
        payments = [
            _make_payment(self.pin_a.name, 150, "Open"),
            _make_payment(self.pin_b.name, 50, "Open"),
        ]
        try:
            layer = _make_metric_layer()
            layer.group_by_field = "_metric_open_amount"
            layer.group_config_json = frappe.as_json(
                {
                    "__grouping": {
                        "mode": "bands",
                        "kind": "number",
                        "bands": [
                            {
                                "key": "high_value",
                                "label": "High value",
                                "min": 100,
                                "max": "",
                                "color": "#16a34a",
                            },
                            {
                                "key": "low_value",
                                "label": "Low value",
                                "min": "",
                                "max": 100,
                                "color": "#f97316",
                            },
                        ],
                    }
                }
            )
            layer.save(ignore_permissions=True)

            groups = layer_api.get_features(layer=layer.name, limit=10000)
            self.assertEqual(
                {group["key"] for group in groups["virtual_groups"]},
                {"high_value", "low_value"},
            )

            result = layer_api.get_features(
                layer=layer.name,
                group_key="high_value",
                limit=10000,
            )
            titles = {f["properties"]["_label"] for f in result["features"]}
            self.assertIn("AlphaTestPin", titles)
            self.assertNotIn("BravoTestPin", titles)
            alpha = next(
                f["properties"]
                for f in result["features"]
                if f["properties"]["_label"] == "AlphaTestPin"
            )
            self.assertEqual(alpha["_group_value"], "high_value")
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_group_tree_supports_linked_metric_bands(self):
        payments = [
            _make_payment(self.pin_a.name, 150, "Open"),
            _make_payment(self.pin_b.name, 50, "Open"),
        ]
        try:
            layer = _make_metric_layer()
            result = layer_api.list_group_tree(
                TEST_DOCTYPE,
                [
                    {
                        "field": "_metric_open_amount",
                        "mode": "bands",
                        "kind": "number",
                        "bands": [
                            {"key": "high", "label": "High", "min": 100, "max": ""},
                            {"key": "low", "label": "Low", "min": "", "max": 100},
                        ],
                    }
                ],
                linked_metrics_json=layer.linked_metrics_json,
            )
            labels = {path["labels"][0] for path in result["paths"]}
            self.assertEqual(labels, {"High", "Low"})
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_advanced_grouping_supports_linked_metric_bands(self):
        payments = [
            _make_payment(self.pin_a.name, 150, "Open"),
            _make_payment(self.pin_b.name, 50, "Open"),
        ]
        try:
            layer = _make_metric_layer()
            layer.group_by_field = "_metric_open_amount"
            layer.group_config_json = frappe.as_json(
                {
                    "__grouping": {
                        "version": 2,
                        "levels": [
                            {
                                "field": "_metric_open_amount",
                                "mode": "bands",
                                "kind": "number",
                                "bands": [
                                    {"key": "high", "label": "High", "min": 100, "max": ""},
                                    {"key": "low", "label": "Low", "min": "", "max": 100},
                                ],
                            }
                        ],
                    },
                    "groups": {},
                }
            )
            layer.save(ignore_permissions=True)

            groups = layer_api.get_features(layer=layer.name, limit=10000)
            self.assertEqual(
                {group["key"] for group in groups["virtual_groups"]},
                {"High", "Low"},
            )

            result = layer_api.get_features(
                layer=layer.name,
                group_key="High",
                limit=10000,
            )
            titles = {f["properties"]["_label"] for f in result["features"]}
            self.assertEqual(titles, {"AlphaTestPin"})
            props = result["features"][0]["properties"]
            self.assertEqual(props["_group_value"], "High")
            self.assertEqual(props["_group_values"]["_metric_open_amount"], "High")
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_get_linked_records_returns_configured_payment_rows(self):
        payments = [
            _make_payment(self.pin_a.name, 100, "Open"),
            _make_payment(self.pin_a.name, 25, "Closed"),
            _make_payment(self.pin_b.name, 10, "Open"),
        ]
        try:
            layer = _make_metric_layer()
            result = layer_api.get_linked_records(
                layer=layer.name,
                source_name=self.pin_a.name,
                limit=10,
            )
            groups = {group["key"]: group for group in result["groups"]}
            self.assertIn("open_amount", groups)
            self.assertIn("payment_count", groups)
            self.assertEqual(result["total"], 3)

            open_rows = groups["open_amount"]["rows"]
            count_rows = groups["payment_count"]["rows"]
            self.assertEqual(len(open_rows), 1)
            self.assertEqual(open_rows[0]["pin"], self.pin_a.name)
            self.assertEqual(open_rows[0]["amount"], 100)
            self.assertEqual(open_rows[0]["status"], "Open")
            summary_totals = {
                item["field"]: item
                for item in groups["open_amount"]["summary"]["totals"]
            }
            self.assertEqual(summary_totals["amount"]["value"], 100)
            summary_statuses = {
                item["label"]: item["count"]
                for item in groups["payment_count"]["summary"]["statuses"]
            }
            self.assertEqual(summary_statuses["Open"], 1)
            self.assertEqual(summary_statuses["Closed"], 1)
            self.assertEqual(len(count_rows), 2)
            self.assertTrue(
                any(field["fieldname"] == "status" for field in groups["payment_count"]["fields"])
            )
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_dynamic_linked_metrics_are_attached_to_features(self):
        payments = [
            _make_dynamic_payment(self.pin_a.name, 150, "Open"),
            _make_dynamic_payment(self.pin_a.name, 25, "Closed"),
            _make_dynamic_payment(self.pin_b.name, 10, "Open"),
        ]
        try:
            layer = _make_dynamic_metric_layer()
            result = layer_api.get_features(layer=layer.name, limit=10000)
            by_label = {
                feature["properties"]["_label"]: feature["properties"]
                for feature in result["features"]
            }
            self.assertEqual(by_label["AlphaTestPin"]["_metrics"]["dynamic_amount"], 150.0)
            self.assertEqual(by_label["BravoTestPin"]["_metrics"]["dynamic_amount"], 10.0)
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_DYNAMIC_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_DYNAMIC_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_get_linked_records_returns_dynamic_link_payment_rows(self):
        payments = [
            _make_dynamic_payment(self.pin_a.name, 150, "Open"),
            _make_dynamic_payment(self.pin_a.name, 25, "Closed"),
            _make_dynamic_payment(self.pin_b.name, 10, "Open"),
        ]
        try:
            layer = _make_dynamic_metric_layer()
            result = layer_api.get_linked_records(
                layer=layer.name,
                source_name=self.pin_a.name,
                limit=10,
            )
            groups = {group["key"]: group for group in result["groups"]}
            self.assertIn("dynamic_amount", groups)
            self.assertEqual(groups["dynamic_amount"]["dynamic_link_doctype_field"], "party_type")
            rows = groups["dynamic_amount"]["rows"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["party_type"], TEST_DOCTYPE)
            self.assertEqual(rows[0]["party"], self.pin_a.name)
            self.assertEqual(rows[0]["amount"], 150)
            summary_totals = {
                item["field"]: item
                for item in groups["dynamic_amount"]["summary"]["totals"]
            }
            self.assertEqual(summary_totals["amount"]["value"], 150)
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_DYNAMIC_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_DYNAMIC_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_get_linked_records_respects_layer_base_filters(self):
        payment = _make_payment(self.pin_b.name, 10, "Open")
        try:
            layer = _make_metric_layer()
            layer.filter_json = frappe.as_json([["name", "=", self.pin_a.name]])
            layer.save(ignore_permissions=True)
            with self.assertRaises(frappe.PermissionError):
                layer_api.get_linked_records(
                    layer=layer.name,
                    source_name=self.pin_b.name,
                    limit=10,
                )
        finally:
            if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                frappe.delete_doc(
                    TEST_PAYMENT_DOCTYPE,
                    payment.name,
                    ignore_permissions=True,
                    force=True,
                )

    def test_get_linked_records_suggests_payment_rows_without_configured_metrics(self):
        payment = _make_payment(self.pin_a.name, 100, "Open")
        try:
            layer = _make_layer()
            result = layer_api.get_linked_records(
                layer=layer.name,
                source_name=self.pin_a.name,
                limit=10,
            )
            self.assertTrue(result["suggested"])
            groups = [group for group in result["groups"] if group["source_doctype"] == TEST_PAYMENT_DOCTYPE]
            self.assertTrue(groups)
            self.assertTrue(groups[0]["suggested"])
            self.assertEqual(groups[0]["rows"][0]["name"], payment.name)
        finally:
            if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                frappe.delete_doc(
                    TEST_PAYMENT_DOCTYPE,
                    payment.name,
                    ignore_permissions=True,
                    force=True,
                )

    def test_money_metric_suggestions_find_linked_payment_doctype(self):
        result = layer_api.suggest_money_metrics(TEST_DOCTYPE, limit=50)
        relevant = [
            suggestion
            for suggestion in result["suggestions"]
            if suggestion["source_doctype"] == TEST_PAYMENT_DOCTYPE
        ]
        self.assertTrue(
            any(
                suggestion["aggregate"] == "count"
                and suggestion["link_field"] == "pin"
                for suggestion in relevant
            )
        )

    def test_money_metric_suggestions_find_dynamic_link_payment_doctype(self):
        result = layer_api.suggest_money_metrics(TEST_DOCTYPE, limit=50)
        relevant = [
            suggestion
            for suggestion in result["suggestions"]
            if suggestion["source_doctype"] == TEST_DYNAMIC_PAYMENT_DOCTYPE
        ]
        self.assertTrue(
            any(
                suggestion["aggregate"] == "count"
                and suggestion["link_field"] == "party"
                and suggestion["dynamic_link_doctype_field"] == "party_type"
                for suggestion in relevant
            )
        )
        self.assertTrue(
            any(
                suggestion["aggregate"] == "sum"
                and suggestion["field"] == "amount"
                and suggestion["link_field"] == "party"
                and suggestion["dynamic_link_doctype_field"] == "party_type"
                for suggestion in relevant
            )
        )
        self.assertTrue(
            any(
                suggestion["aggregate"] == "sum"
                and suggestion["field"] == "amount"
                and suggestion["link_field"] == "party"
                and suggestion["dynamic_link_doctype_field"] == "party_type"
                for suggestion in relevant
            )
        )
