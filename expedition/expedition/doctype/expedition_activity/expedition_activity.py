# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpeditionActivity(Document):
    """
    A visit / call / demo log entry tied to a location or a related doc.

    Used by:
      - The activity layer (GPS / check-in log overlay)
      - The bottom drawer (recent visits timeline)
      - Insight computation (dormant accounts, hot zones)
    """

    def validate(self):
        if not self.user:
            self.user = frappe.session.user
        if not self.occurred_at:
            self.occurred_at = frappe.utils.now()
        # If the activity references a related doc, capture its location
        # so we can place it on the map even if the user didn't supply
        # explicit coordinates.
        if (
            self.related_doctype
            and self.related_name
            and not (self.latitude and self.longitude)
        ):
            self._pull_location_from_related()

    def _pull_location_from_related(self):
        try:
            doc = frappe.get_doc(self.related_doctype, self.related_name)
        except frappe.DoesNotExistError:
            return
        lat = doc.get("latitude")
        lng = doc.get("longitude")
        if lat is not None and lng is not None:
            self.latitude = lat
            self.longitude = lng
