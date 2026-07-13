"""Tests for expedition.api.insight.

These run under `bench run-tests --app expedition`. They cover the
insight framework: builder registration, the recompute write path,
and the whitelisted read endpoint's permission gating.
"""

import unittest

import frappe

from expedition.api.insight import (
    recompute_for_map,
    recompute_map,
    get_active_for_map,
    _BUILDERS,
    _insight_linked_money,
)

TEST_SOURCE_DOCTYPE = "Expedition Insight Test Source"
TEST_PAYMENT_DOCTYPE = "Expedition Insight Test Payment"
TEST_SOURCE_FIELDS = [
    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1},
    {"fieldname": "latitude", "fieldtype": "Float", "label": "Latitude", "reqd": 1},
    {"fieldname": "longitude", "fieldtype": "Float", "label": "Longitude", "reqd": 1},
]
TEST_PAYMENT_FIELDS = [
    {
        "fieldname": "source",
        "fieldtype": "Link",
        "label": "Source",
        "options": TEST_SOURCE_DOCTYPE,
        "reqd": 1,
    },
    {"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
    {"fieldname": "status", "fieldtype": "Data", "label": "Status"},
]


def _ensure_money_test_doctypes() -> None:
    for doctype, fields in (
        (TEST_SOURCE_DOCTYPE, TEST_SOURCE_FIELDS),
        (TEST_PAYMENT_DOCTYPE, TEST_PAYMENT_FIELDS),
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


def _drop_money_test_doctypes() -> None:
    for doctype in (TEST_PAYMENT_DOCTYPE, TEST_SOURCE_DOCTYPE):
        if frappe.db.exists("DocType", doctype):
            frappe.delete_doc("DocType", doctype, ignore_permissions=True, force=True)


def _make_source(title: str, lat: float, lng: float):
    doc = frappe.get_doc(
        {
            "doctype": TEST_SOURCE_DOCTYPE,
            "title": title,
            "latitude": lat,
            "longitude": lng,
        }
    )
    return doc.insert(ignore_permissions=True)


def _make_payment(source_name: str, amount: float, status: str = "Open"):
    doc = frappe.get_doc(
        {
            "doctype": TEST_PAYMENT_DOCTYPE,
            "source": source_name,
            "amount": amount,
            "status": status,
        }
    )
    return doc.insert(ignore_permissions=True)


def _make_money_map_and_layer():
    map_doc = frappe.get_doc(
        {
            "doctype": "Expedition Map",
            "title": "Test map for linked money insight",
            "is_public": 1,
            "public_access": "Read Only",
        }
    ).insert(ignore_permissions=True)
    layer = frappe.get_doc(
        {
            "doctype": "Expedition Layer",
            "title": "Test money layer",
            "map": map_doc.name,
            "source_doctype": TEST_SOURCE_DOCTYPE,
            "latitude_field": "latitude",
            "longitude_field": "longitude",
            "label_field": "title",
            "linked_metrics_json": frappe.as_json(
                [
                    {
                        "key": "open_amount",
                        "label": "Open Amount",
                        "source_doctype": TEST_PAYMENT_DOCTYPE,
                        "link_field": "source",
                        "aggregate": "sum",
                        "field": "amount",
                        "filters": [["status", "=", "Open"]],
                    },
                    {
                        "key": "payment_count",
                        "label": "Payment Count",
                        "source_doctype": TEST_PAYMENT_DOCTYPE,
                        "link_field": "source",
                        "aggregate": "count",
                    },
                ]
            ),
        }
    ).insert(ignore_permissions=True)
    return map_doc, layer


def _pick_public_map_with_layer() -> str | None:
    """Find a public Expedition Map that has at least one enabled layer."""
    maps = frappe.get_all(
        "Expedition Map",
        filters={"is_public": 1},
        fields=["name"],
    )
    for m in maps:
        layers = frappe.get_all(
            "Expedition Layer",
            filters={"map": m.name, "enabled": 1},
            fields=["name", "source_doctype"],
        )
        if layers:
            return m.name
    return None


class TestInsightBuilders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_money_test_doctypes()

    @classmethod
    def tearDownClass(cls):
        _drop_money_test_doctypes()

    def tearDown(self):
        for insight_name in frappe.get_all(
            "Expedition Insight",
            filters={"title": ["like", "Field Metrics: Test money layer%"]},
            pluck="name",
        ):
            frappe.delete_doc(
                "Expedition Insight", insight_name, ignore_permissions=True, force=True
            )
        for layer_name in frappe.get_all(
            "Expedition Layer",
            filters={"title": "Test money layer"},
            pluck="name",
        ):
            frappe.delete_doc(
                "Expedition Layer", layer_name, ignore_permissions=True, force=True
            )
        for map_name in frappe.get_all(
            "Expedition Map",
            filters={"title": "Test map for linked money insight"},
            pluck="name",
        ):
            frappe.delete_doc(
                "Expedition Map", map_name, ignore_permissions=True, force=True
            )
        for doctype in (TEST_PAYMENT_DOCTYPE, TEST_SOURCE_DOCTYPE):
            for name in frappe.get_all(doctype, pluck="name"):
                frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)

    def test_builders_registered(self):
        # Every entry in _BUILDERS must be a callable.
        self.assertTrue(_BUILDERS)
        for name, fn in _BUILDERS.items():
            self.assertTrue(callable(fn), f"{name} is not callable")
            self.assertIn(name, ("category_gap", "linked_money"))

    def test_recompute_is_idempotent(self):
        map_name = _pick_public_map_with_layer()
        if not map_name:
            self.skipTest("No public map with an enabled layer")
        n1 = recompute_for_map(map_name)
        n2 = recompute_for_map(map_name)
        # Same map, same data → same number of insights. The recompute
        # path clears active rows first, so a stale row never lingers.
        self.assertEqual(n1, n2)

    def test_category_gap_handles_missing_field(self):
        # The builder must gracefully no-op when the source DocType has
        # no `category` Select field.
        from expedition.api.insight import _insight_category_gap

        result = _insight_category_gap("definitely-not-a-real-map")
        self.assertEqual(result, [])

    def test_linked_money_builder_summarizes_configured_metrics(self):
        source_a = _make_source("Insight Alpha", 10.0, 20.0)
        source_b = _make_source("Insight Bravo", 11.0, 21.0)
        payments = [
            _make_payment(source_a.name, 100, "Open"),
            _make_payment(source_a.name, 50, "Open"),
            _make_payment(source_a.name, 25, "Closed"),
            _make_payment(source_b.name, 10, "Open"),
        ]
        map_doc, layer = _make_money_map_and_layer()
        try:
            rows = _insight_linked_money(map_doc.name)
            self.assertEqual(len(rows), 1)
            insight = rows[0]
            self.assertEqual(insight["title"], "Field Metrics: Test money layer")
            self.assertEqual(insight["severity"], "medium")
            self.assertEqual(insight["linked_doctype"], TEST_SOURCE_DOCTYPE)
            detail = frappe.parse_json(insight["detail_json"])
            self.assertEqual(detail["layer"], layer.name)
            self.assertEqual(detail["source_count"], 2)
            metrics = {row["key"]: row for row in detail["metrics"]}
            self.assertEqual(metrics["open_amount"]["value"], 160.0)
            self.assertEqual(metrics["open_amount"]["row_count"], 3)
            open_statuses = {
                item["label"]: item["count"]
                for item in metrics["open_amount"]["statuses"]
            }
            self.assertEqual(open_statuses["Open"], 3)
            self.assertEqual(metrics["payment_count"]["value"], 4)
            self.assertEqual(metrics["payment_count"]["row_count"], 4)
            payment_statuses = {
                item["label"]: item["count"]
                for item in metrics["payment_count"]["statuses"]
            }
            self.assertEqual(payment_statuses["Open"], 3)
            self.assertEqual(payment_statuses["Closed"], 1)
            open_top = metrics["open_amount"]["top_sources"]
            self.assertEqual(open_top[0]["name"], source_a.name)
            self.assertEqual(open_top[0]["label"], "Insight Alpha")
            self.assertEqual(open_top[0]["value"], 150.0)
            self.assertEqual(open_top[1]["name"], source_b.name)
            self.assertEqual(open_top[1]["value"], 10.0)
            self.assertAlmostEqual(metrics["open_amount"]["top_share"], 0.9375)
            self.assertEqual(metrics["open_amount"]["top_3_share"], 1.0)
            self.assertEqual(metrics["open_amount"]["active_source_count"], 2)
            self.assertEqual(metrics["open_amount"]["zero_source_count"], 0)
            count_top = metrics["payment_count"]["top_sources"]
            self.assertEqual(count_top[0]["name"], source_a.name)
            self.assertEqual(count_top[0]["value"], 3)
            self.assertEqual(metrics["payment_count"]["top_share"], 0.75)
        finally:
            for payment in payments:
                if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                    frappe.delete_doc(
                        TEST_PAYMENT_DOCTYPE,
                        payment.name,
                        ignore_permissions=True,
                        force=True,
                    )

    def test_recompute_writes_linked_money_insight_rows(self):
        source = _make_source("Insight Alpha", 10.0, 20.0)
        payment = _make_payment(source.name, 100, "Open")
        map_doc, _layer = _make_money_map_and_layer()
        try:
            written = recompute_for_map(map_doc.name)
            self.assertGreaterEqual(written, 1)
            rows = frappe.get_all(
                "Expedition Insight",
                filters={
                    "map": map_doc.name,
                    "is_active": 1,
                    "insight_type": "linked_money",
                },
                fields=["title", "detail_json"],
            )
            self.assertEqual(len(rows), 1)
            detail = frappe.parse_json(rows[0]["detail_json"])
            self.assertEqual(detail["source_count"], 1)
            metrics = {row["key"]: row for row in detail["metrics"]}
            self.assertEqual(metrics["open_amount"]["value"], 100.0)
            self.assertEqual(metrics["open_amount"]["top_sources"][0]["name"], source.name)
        finally:
            if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                frappe.delete_doc(
                    TEST_PAYMENT_DOCTYPE,
                    payment.name,
                    ignore_permissions=True,
                    force=True,
                )

    def test_recompute_map_endpoint_returns_active_rows(self):
        source = _make_source("Insight Alpha", 10.0, 20.0)
        payment = _make_payment(source.name, 100, "Open")
        map_doc, _layer = _make_money_map_and_layer()
        try:
            result = recompute_map(map_doc.name)
            self.assertEqual(result["map"], map_doc.name)
            self.assertGreaterEqual(result["written"], 1)
            self.assertTrue(
                any(row["insight_type"] == "linked_money" for row in result["insights"])
            )
        finally:
            if frappe.db.exists(TEST_PAYMENT_DOCTYPE, payment.name):
                frappe.delete_doc(
                    TEST_PAYMENT_DOCTYPE,
                    payment.name,
                    ignore_permissions=True,
                    force=True,
                )


class TestInsightPermission(unittest.TestCase):
    def test_unknown_map_raises(self):
        with self.assertRaises(frappe.DoesNotExistError):
            get_active_for_map("definitely-not-a-real-map")
