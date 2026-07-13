import frappe


def execute():
    """Ensure the pin_min_zoom field is present and initialized."""
    frappe.reload_doc("expedition", "doctype", "expedition_layer")
    if not frappe.db.has_column("Expedition Layer", "pin_min_zoom"):
        return
    frappe.db.sql(
        """
        update `tabExpedition Layer`
        set pin_min_zoom = 0
        where pin_min_zoom is null
        """
    )
