# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ExpeditionPreset(Document):
    """
    A pre-styled layer template. A user picks a preset when creating a
    new layer; the preset's color/icon/size/cluster defaults are copied
    into the layer document.

    System presets (is_system=1) ship with the app via the post-install
    patch and cannot be deleted. Users can duplicate-and-modify to make
    their own.
    """

    def on_trash(self):
        if self.is_system:
            frappe.throw(
                "System presets cannot be deleted. Duplicate it to make your own."
            )
