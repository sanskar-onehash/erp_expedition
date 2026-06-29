"""App install/uninstall hooks. Seeds the Expedition User role."""

import frappe


ROLE_NAME = "Expedition User"


def _ensure_role() -> None:
    if not frappe.db.exists("Role", ROLE_NAME):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE_NAME}).insert(
            ignore_permissions=True
        )


def after_install() -> None:
    _ensure_role()
    frappe.db.commit()


def after_uninstall() -> None:
    # Leave user data intact; user cleans via Desk if desired. - for now
    pass


def before_tests() -> None:
    _ensure_role()
