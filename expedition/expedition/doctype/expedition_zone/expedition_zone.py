# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document


class ExpeditionZone(Document):
    """
    A user-drawn geographic region on a saved map.

    Geometry is stored as a GeoJSON string in `geometry` and validated
    against the chosen `zone_type` (polygon or circle). The centroid
    fields are denormalized for fast proximity / coverage queries and
    recomputed on save.
    """

    def validate(self):
        self._validate_geometry()
        self._compute_centroid()

    def _validate_geometry(self):
        if not self.geometry:
            return
        try:
            geom = (
                json.loads(self.geometry)
                if isinstance(self.geometry, str)
                else self.geometry
            )
        except Exception:
            frappe.throw("Geometry is not valid JSON")

        gtype = geom.get("type")
        if self.zone_type == "polygon" and gtype not in ("Polygon", "MultiPolygon"):
            frappe.throw(
                f"Zone type 'polygon' requires Polygon geometry, got '{gtype}'"
            )
        if self.zone_type == "circle" and gtype != "Point":
            frappe.throw(f"Zone type 'circle' requires Point geometry, got '{gtype}'")

    def _compute_centroid(self):
        """Cheap centroid for v1: average of the outer ring's points.

        Good enough for proximity queries; full polygon centroid math
        can be added in v1.1 with a proper geo library if needed.
        """
        geom = (
            json.loads(self.geometry)
            if isinstance(self.geometry, str)
            else self.geometry
        )
        if geom.get("type") == "Point":
            lon, lat = geom["coordinates"]
            self.centroid_lat = lat
            self.centroid_lng = lon
            return
        # Polygon: average the outer ring
        coords = geom.get("coordinates", [[]])[0]
        if not coords:
            return
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        self.centroid_lat = sum(lats) / len(lats)
        self.centroid_lng = sum(lons) / len(lons)
