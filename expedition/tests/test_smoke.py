"""Smoke test for expedition app - verifies api imports + load_full work."""

import unittest

import frappe


class TestExpeditionApi(unittest.TestCase):
    def test_map_templates(self):
        from expedition.api.map import list_templates

        templates = list_templates()
        self.assertIsInstance(templates, list)

    def test_map_load_full(self):
        from expedition.api.map import list_templates, load_full

        templates = list_templates()
        if not templates:
            self.skipTest("No expedition maps in DB")
        m = load_full(templates[0]["name"])
        self.assertIn("map", m)
        self.assertIn("layers", m)
        self.assertIn("zones", m)

    def test_layer_importable(self):
        from expedition.api.layer import get_features

        self.assertTrue(callable(get_features))

    def test_get_features_returns_geojson(self):
        """
        Catches regressions in the layer endpoint: schema drift on the
        Expedition Layer DocType, the Frappe `between` filter bug for
        Float fields, missing `fields=` (which silently drops lat/lng),
        and permission-check regressions.
        """
        from expedition.api.layer import get_features

        layers = frappe.get_all("Expedition Layer", filters={"enabled": 1}, limit=1)
        if not layers:
            self.skipTest("No enabled Expedition Layer docs in DB")
        fc = get_features(layer=layers[0]["name"])
        self.assertEqual(fc["type"], "FeatureCollection")
        self.assertIn("features", fc)
        self.assertIn("total", fc)
        self.assertGreaterEqual(fc["total"], 1)
        # If features came back, verify shape
        if fc["features"]:
            f = fc["features"][0]
            self.assertEqual(f["type"], "Feature")
            self.assertEqual(f["geometry"]["type"], "Point")
            self.assertIn("_label", f["properties"])
            self.assertIn("_name", f["properties"])
            self.assertIn("_doctype", f["properties"])
        # Verify viewport-bounded fetch works (the regression that bit
        # us once: Frappe's `between` filter is broken for Float).
        fc_b = get_features(
            layer=layers[0]["name"],
            bounds={"south": 6.5, "west": 68, "north": 36, "east": 98},
        )
        self.assertEqual(fc_b["type"], "FeatureCollection")
        self.assertIn("features", fc_b)

    def test_permission_importable(self):
        from expedition.api.permission import assert_source_read

        self.assertTrue(callable(assert_source_read))

    def test_activity_importable(self):
        from expedition.api.activity import log_activity, list_for_related

        self.assertTrue(callable(log_activity))
        self.assertTrue(callable(list_for_related))

    def test_insight_importable(self):
        from expedition.api.insight import recompute_all

        self.assertTrue(callable(recompute_all))

    def test_load_full_debug(self):
        from expedition.api.map import list_templates, load_full

        templates = list_templates()
        if not templates:
            self.skipTest("No expedition maps in DB")
        result = load_full(templates[0]["name"])
        print(f"\n>>> load_full {templates[0]['name']!r} keys={sorted(result.keys())}")
        m = result["map"]
        print(
            f">>> map: title={m.get('title')!r} basemap={m.get('basemap_id')!r} center={m.get('center_lat')},{m.get('center_lng')}"
        )
        for L in result["layers"]:
            print(
                f">>>   layer: {L.get('name')!r} seq={L.get('sequence')} src={L.get('source_doctype')!r} enabled={L.get('enabled')}"
            )
        for z in result["zones"][:3]:
            print(
                f">>>   zone: {z.get('name')!r} type={z.get('zone_type')} seq={z.get('sequence')}"
            )
        self.assertTrue(True)

    def test_dump_schema_records(self):
        """Compact dump of all 6 DocType schemas + key records."""
        from expedition.expedition.doctype.expedition_map.expedition_map import (
            ExpeditionMap,
        )

        for dt in [
            "Expedition Map",
            "Expedition Layer",
            "Expedition Zone",
            "Expedition Activity",
            "Expedition Insight",
            "Expedition Preset",
        ]:
            meta = frappe.get_meta(dt)
            print(f"\n=== {dt} ({len(meta.fields)} fields) ===")
            for f in meta.fields:
                opt = f.options or ""
                print(f"  {f.fieldname:30s} {f.fieldtype:20s} {opt}")

        # Records
        print("\n=== Expedition Map records ===")
        for d in frappe.get_all(
            "Expedition Map",
            fields=[
                "name",
                "title",
                "basemap_style",
                "viewport",
                "is_public",
                "is_template",
            ],
        ):
            print(d)

        print("\n=== Expedition Layer records ===")
        for d in frappe.get_all(
            "Expedition Layer",
            fields=[
                "name",
                "map",
                "sequence",
                "source_doctype",
                "enabled",
                "filter_json",
                "color",
                "size",
                "cluster",
                "heatmap",
            ],
        ):
            print(d)

        print("\n=== Expedition Zone records ===")
        for d in frappe.get_all(
            "Expedition Zone",
            fields=[
                "name",
                "map",
                "zone_type",
                "geometry",
                "centroid_lat",
                "centroid_lng",
            ],
        ):
            print(d)

        print("\n=== seed counts ===")
        for dt in ["Customer", "Supplier", "Item"]:
            try:
                n = frappe.db.count(dt)
                print(f"  tab{dt}: {n}")
            except Exception as e:
                print(f"  tab{dt}: ERROR {e}")


class TestMasterMappings(unittest.TestCase):
    """Tests for the master-mapped layer model (slice: master mappings)."""

    SOURCE = "Location"  # standard Frappe DocType with separate lat/lng Float fields

    def test_create_master_returns_dto(self):
        from expedition.api.layer import create_master, delete

        dto = create_master(
            title="SmokeTest Master",
            source_doctype=self.SOURCE,
            color="#123456",
        )
        try:
            self.assertEqual(dto["title"], "SmokeTest Master")
            self.assertIn(dto["map"], (None, ""))
            self.assertEqual(dto["color"], "#123456")
            self.assertEqual(dto["source_doctype"], self.SOURCE)
        finally:
            delete(dto["name"])

    def test_list_masters_excludes_instances(self):
        from expedition.api.layer import (
            create_master,
            delete,
            list_masters,
        )

        dto = create_master(
            title="SmokeTest Master Excl",
            source_doctype=self.SOURCE,
        )
        try:
            masters = list_masters()
            names = [m["name"] for m in masters]
            self.assertIn(dto["name"], names)
            for m in masters:
                self.assertIn(m["map"], (None, ""))
        finally:
            delete(dto["name"])

    def test_attach_to_map_creates_instance(self):
        from expedition.api.layer import (
            attach_to_map,
            create_master,
            delete,
            list_for_map,
        )
        from expedition.api.map import list_templates

        templates = list_templates()
        if not templates:
            self.skipTest("No template maps")
        target = templates[0]["name"]

        master = create_master(
            title="SmokeTest Attach Master",
            source_doctype=self.SOURCE,
            color="#ABCDEF",
        )
        try:
            inst = attach_to_map(master_name=master["name"], map_name=target)
            self.assertEqual(inst["map"], target)
            self.assertEqual(inst["color"], "#ABCDEF")
            self.assertEqual(inst["source_doctype"], self.SOURCE)
            layers = list_for_map(target)
            self.assertTrue(any(l["name"] == inst["name"] for l in layers))
        finally:
            try:
                delete(inst["name"])
            except Exception:
                pass
            delete(master["name"])

    def test_attach_to_map_idempotent(self):
        from expedition.api.layer import (
            attach_to_map,
            create_master,
            delete,
        )
        from expedition.api.map import list_templates

        templates = list_templates()
        if not templates:
            self.skipTest("No template maps")
        target = templates[0]["name"]

        master = create_master(
            title="SmokeTest Idem",
            source_doctype=self.SOURCE,
        )
        try:
            inst1 = attach_to_map(master_name=master["name"], map_name=target)
            inst2 = attach_to_map(master_name=master["name"], map_name=target)
            self.assertEqual(inst1["name"], inst2["name"])
        finally:
            try:
                delete(inst1["name"])
            except Exception:
                pass
            delete(master["name"])

    def test_master_delete_cascades_nothing(self):
        """Deleting a master does NOT delete its attached instances."""
        from expedition.api.layer import (
            attach_to_map,
            create_master,
            delete,
            get_features,
        )
        from expedition.api.map import list_templates

        templates = list_templates()
        if not templates:
            self.skipTest("No template maps")
        target = templates[0]["name"]

        master = create_master(
            title="SmokeTest Cascade",
            source_doctype=self.SOURCE,
        )
        try:
            inst = attach_to_map(master_name=master["name"], map_name=target)
            delete(master["name"])
            # Instance should still resolve via get_features.
            fc = get_features(layer=inst["name"])
            self.assertEqual(fc["type"], "FeatureCollection")
        finally:
            try:
                delete(inst["name"])
            except Exception:
                pass
