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

import frappe
from frappe.exceptions import ValidationError

from expedition.api import layer as layer_api


TEST_DOCTYPE = "Expedition Test Pin"
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


def _ensure_test_doctype() -> None:
    if frappe.db.exists("DocType", TEST_DOCTYPE):
        return
    doc = frappe.get_doc(
        {
            "doctype": "DocType",
            "name": TEST_DOCTYPE,
            "module": "Expedition",
            "custom": 1,
            "is_virtual": 0,
            "autoname": "hash",
            "fields": TEST_DOCTYPE_FIELDS,
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
    if frappe.db.exists("DocType", TEST_DOCTYPE):
        frappe.delete_doc("DocType", TEST_DOCTYPE, ignore_permissions=True, force=True)


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

    def test_template_safe_render_blocks_dunder_access(self):
        # Frappe's render_template with safe_render=True (default) blocks
        # `.__` attribute traversal. A template that tries to reach
        # `doc.__class__` must throw, not silently succeed.
        tpl = "{{ doc.__class__ }}"
        layer = _make_layer(popup_template=tpl)
        with self.assertRaises(ValidationError):
            layer_api.get_features(layer=layer.name, limit=10000)

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
