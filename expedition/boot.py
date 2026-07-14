import frappe


def add_navbar_item(bootinfo):
    if frappe.session.user == "Guest":
        return

    has_role = frappe.db.exists(
        "Has Role",
        {
            "parent": frappe.session.user,
            "role": "Expedition User",
        },
    )
    if not has_role:
        return

    bootinfo.navbar_items = bootinfo.get("navbar_items", []) + [
        {
            "item_label": "Expedition",
            "item_type": "Route",
            "route": "/expedition",
            "is_standard": 1,
            "hidden": 0,
        }
    ]
