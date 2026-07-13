import unittest
import frappe

GP_DOCTYPE = "Expedition DotWalk Grandparent"
P_DOCTYPE = "Expedition DotWalk Parent"
C_DOCTYPE = "Expedition DotWalk Child"

GP_FIELDS = [
    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1},
    {"fieldname": "latitude", "fieldtype": "Float", "label": "Latitude", "reqd": 1},
    {"fieldname": "longitude", "fieldtype": "Float", "label": "Longitude", "reqd": 1},
]
P_FIELDS = [
    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1},
    {
        "fieldname": "grandparent",
        "fieldtype": "Link",
        "label": "Grandparent",
        "options": GP_DOCTYPE,
        "reqd": 1,
    },
]
C_FIELDS = [
    {
        "fieldname": "parent_link",
        "fieldtype": "Link",
        "label": "Parent Link",
        "options": P_DOCTYPE,
        "reqd": 1,
    },
    {"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
]


def _ensure_test_doctypes():
    for doctype, fields in (
        (GP_DOCTYPE, GP_FIELDS),
        (P_DOCTYPE, P_FIELDS),
        (C_DOCTYPE, C_FIELDS),
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


def _drop_test_doctypes():
    for doctype in (C_DOCTYPE, P_DOCTYPE, GP_DOCTYPE):
        if frappe.db.exists("DocType", doctype):
            frappe.delete_doc("DocType", doctype, ignore_permissions=True, force=True)


class TestDotWalkLinkPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _ensure_test_doctypes()

    @classmethod
    def tearDownClass(cls):
        _drop_test_doctypes()

    def setUp(self):
        self.gp_a = frappe.get_doc({"doctype": GP_DOCTYPE, "title": "GPA", "latitude": 10.0, "longitude": 20.0}).insert(ignore_permissions=True)
        self.gp_b = frappe.get_doc({"doctype": GP_DOCTYPE, "title": "GPB", "latitude": 30.0, "longitude": 40.0}).insert(ignore_permissions=True)

        self.p_a1 = frappe.get_doc({"doctype": P_DOCTYPE, "title": "PA1", "grandparent": self.gp_a.name}).insert(ignore_permissions=True)
        self.p_b1 = frappe.get_doc({"doctype": P_DOCTYPE, "title": "PB1", "grandparent": self.gp_b.name}).insert(ignore_permissions=True)

        self.c_a1 = frappe.get_doc({"doctype": C_DOCTYPE, "parent_link": self.p_a1.name, "amount": 100.0}).insert(ignore_permissions=True)
        self.c_a2 = frappe.get_doc({"doctype": C_DOCTYPE, "parent_link": self.p_a1.name, "amount": 200.0}).insert(ignore_permissions=True)
        self.c_b1 = frappe.get_doc({"doctype": C_DOCTYPE, "parent_link": self.p_b1.name, "amount": 500.0}).insert(ignore_permissions=True)

        self.layer = frappe.get_doc(
            {
                "doctype": "Expedition Layer",
                "title": "Test Dotwalk Layer",
                "source_doctype": GP_DOCTYPE,
                "latitude_field": "latitude",
                "longitude_field": "longitude",
                "label_field": "title",
                "linked_metrics_json": frappe.as_json(
                    [
                        {
                            "key": "child_sum",
                            "label": "Child Sum",
                            "source_doctype": C_DOCTYPE,
                            "link_field": "parent_link.grandparent",
                            "aggregate": "sum",
                            "field": "amount",
                        },
                        {
                            "key": "child_count",
                            "label": "Child Count",
                            "source_doctype": C_DOCTYPE,
                            "link_field": "parent_link.grandparent",
                            "aggregate": "count",
                        },
                    ]
                ),
            }
        ).insert(ignore_permissions=True)

    def tearDown(self):
        frappe.delete_doc("Expedition Layer", self.layer.name, ignore_permissions=True, force=True)
        for doctype in (C_DOCTYPE, P_DOCTYPE, GP_DOCTYPE):
            for name in frappe.get_all(doctype, pluck="name"):
                frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)

    def test_get_features_with_dot_walk(self):
        from expedition.api.layer import get_features

        fc = get_features(layer=self.layer.name)
        self.assertEqual(fc["type"], "FeatureCollection")
        
        # We expect 2 features (Grandparent A and Grandparent B)
        self.assertEqual(len(fc["features"]), 2)
        
        features_by_name = {f["properties"]["_name"]: f for f in fc["features"]}
        
        # Grandparent A features child_sum=300, child_count=2
        feat_a = features_by_name[self.gp_a.name]
        self.assertEqual(feat_a["properties"]["_metric_child_sum"], 300.0)
        self.assertEqual(feat_a["properties"]["_metric_child_count"], 2)

        # Grandparent B features child_sum=500, child_count=1
        feat_b = features_by_name[self.gp_b.name]
        self.assertEqual(feat_b["properties"]["_metric_child_sum"], 500.0)
        self.assertEqual(feat_b["properties"]["_metric_child_count"], 1)

    def test_get_linked_records_with_dot_walk(self):
        from expedition.api.layer import get_linked_records

        res = get_linked_records(layer=self.layer.name, source_name=self.gp_a.name)
        groups = res.get("groups") or []
        self.assertEqual(len(groups), 2)
        
        # Verify first group (child_sum)
        g_sum = next(g for g in groups if g["key"] == "child_sum")
        self.assertEqual(g_sum["summary"]["row_count"], 2)
        self.assertEqual(g_sum["summary"]["totals"][0]["value"], 300.0)
        
        # Verify that rows are populated and linked field is mapped back to GPA
        self.assertEqual(len(g_sum["rows"]), 2)
        for row in g_sum["rows"]:
            self.assertEqual(row["parent_link.grandparent"], self.gp_a.name)

    def test_validation_invalid_path(self):
        from expedition.api.layer import validate_linked_metrics_json

        # Invalid field inside path
        invalid_metrics_1 = [
            {
                "key": "invalid_1",
                "label": "Invalid",
                "source_doctype": C_DOCTYPE,
                "link_field": "parent_link.invalid_field",
                "aggregate": "count",
            }
        ]
        with self.assertRaises(Exception):
            validate_linked_metrics_json(GP_DOCTYPE, invalid_metrics_1)

        # Path resolves to wrong target doctype
        invalid_metrics_2 = [
            {
                "key": "invalid_2",
                "label": "Invalid",
                "source_doctype": C_DOCTYPE,
                "link_field": "parent_link", # resolves to Parent, not Grandparent
                "aggregate": "count",
            }
        ]
        with self.assertRaises(frappe.ValidationError):
            validate_linked_metrics_json(GP_DOCTYPE, invalid_metrics_2)
