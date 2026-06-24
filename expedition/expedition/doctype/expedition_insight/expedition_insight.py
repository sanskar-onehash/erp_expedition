# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpeditionInsight(Document):
    """
    A server-computed insight surfaced on the map.

    Examples (server-side v1):
      - "47 customers in 'North' zone with no architect referral in 12 months"
      - "Lead activity hotspot near District 1 — 23 leads opened this week"
      - "12 dormant accounts (no quotation in 12 months) clustered around Hanoi"

    Insights are *not* user-editable. The Expedition User role has read-only
    permission; only the server creates/expires them.
    """

    def validate(self):
        # Server-only document. If a client somehow posts a write, reject.
        if frappe.flags.in_patch or frappe.flags.in_install:
            return
        if not frappe.has_permission("Expedition Insight", "write"):
            frappe.throw("Expedition Insights are server-managed.")
