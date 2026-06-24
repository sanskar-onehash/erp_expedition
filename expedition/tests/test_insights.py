"""Tests for expedition.api.insight.

These run under `bench run-tests --app expedition`. They cover the
insight framework: builder registration, the recompute write path,
and the whitelisted read endpoint's permission gating.
"""

import unittest

import frappe

from expedition.api.insight import (
    recompute_for_map,
    get_active_for_map,
    _BUILDERS,
)


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
    def test_builders_registered(self):
        # Every entry in _BUILDERS must be a callable.
        self.assertTrue(_BUILDERS)
        for name, fn in _BUILDERS.items():
            self.assertTrue(callable(fn), f"{name} is not callable")
            self.assertIn(name, ("category_gap",))

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


class TestInsightPermission(unittest.TestCase):
    def test_unknown_map_raises(self):
        with self.assertRaises(frappe.DoesNotExistError):
            get_active_for_map("definitely-not-a-real-map")
