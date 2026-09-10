import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from expedition.api import layer as layer_api


class TestPythonScriptSourceFilters(unittest.TestCase):
    def setUp(self):
        self.layer = SimpleNamespace(
            source_doctype="Test Source",
            filter_json=json.dumps([["category", "=", "Target"]]),
        )
        self.output = [
            {
                "latitude": 10.0,
                "longitude": 20.0,
                "properties": {"_doctype": "Test Source", "_name": "ROW-001"},
            },
            {
                "latitude": 11.0,
                "longitude": 21.0,
                "properties": {"_doctype": "Test Source", "_name": "ROW-002"},
            },
        ]

    @patch.object(layer_api, "_parse_extra_feature_fields", return_value=[])
    @patch.object(layer_api.frappe, "get_list")
    @patch.object(layer_api.frappe, "get_all")
    def test_filters_real_source_rows(self, get_all, get_list, _parse_fields):
        get_all.return_value = [{"name": "ROW-001"}, {"name": "ROW-002"}]
        get_list.return_value = [{"name": "ROW-001"}]

        filtered, rows = layer_api._python_script_source_rows(
            self.layer, self.output
        )

        self.assertEqual(
            [item["properties"]["_name"] for item in filtered], ["ROW-001"]
        )
        self.assertEqual(set(rows), {"ROW-001"})
        self.assertIn(
            ["category", "=", "Target"],
            get_list.call_args.kwargs["filters"],
        )

    @patch.object(
        layer_api, "_parse_extra_feature_fields", return_value=["title"]
    )
    @patch.object(layer_api.frappe, "get_list")
    @patch.object(layer_api.frappe, "get_all")
    def test_hydrates_requested_search_fields(
        self, get_all, get_list, _parse_fields
    ):
        get_all.return_value = [{"name": "ROW-001"}, {"name": "ROW-002"}]
        get_list.return_value = [
            {"name": "ROW-001", "title": "First row"},
            {"name": "ROW-002", "title": "Second row"},
        ]

        filtered, _rows = layer_api._python_script_source_rows(
            self.layer, self.output, extra_fields=["title"]
        )

        self.assertEqual(
            filtered[0]["properties"]["title"], "First row"
        )
        self.assertEqual(
            filtered[1]["properties"]["title"], "Second row"
        )


if __name__ == "__main__":
    unittest.main()
