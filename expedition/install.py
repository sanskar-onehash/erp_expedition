"""App install/uninstall hooks for Expedition access."""

import frappe


ROLE_NAME = "Expedition User"


def _ensure_role() -> None:
    if not frappe.db.exists("Role", ROLE_NAME):
        frappe.get_doc({"doctype": "Role", "role_name": ROLE_NAME}).insert(
            ignore_permissions=True
        )


def _grant_role_to_administrators() -> None:
    """Give Expedition access to Administrator and existing System Managers."""
    users = {"Administrator"}
    users.update(
        frappe.get_all(
            "Has Role",
            filters={"parenttype": "User", "role": "System Manager"},
            pluck="parent",
        )
    )

    for user in users:
        frappe.get_doc("User", user).add_roles(ROLE_NAME)


def after_install() -> None:
    _ensure_role()
    _grant_role_to_administrators()
    frappe.db.commit()


def after_uninstall() -> None:
    # Leave user data intact; user cleans via Desk if desired. - for now
    pass


def before_tests() -> None:
    _ensure_role()
