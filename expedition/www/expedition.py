"""Expedition full-bleed custom page controller.

The map is the entire experience. No Frappe Desk chrome, no top bar,
no breadcrumbs. The Vue bundle owns the viewport.

This page is served by Frappe's website router at /expedition. Every
API call still respects Frappe's session and permission system.
"""

import frappe

no_cache = 1
# Bare skeleton — does NOT extend Frappe's templates/web.html, which
# would pull in website.bundle.css and all global selectors (h3, .level,
# etc.) that conflict with our scoped component styles.
base_template_path = "expedition/templates/expedition_base.html"


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw("Not permitted", frappe.PermissionError)

    if not frappe.db.exists(
        "Has Role",
        {
            "parent": frappe.session.user,
            "role": "Expedition User",
        },
    ):
        frappe.throw("Not permitted", frappe.PermissionError)

    from expedition import __frontend_build__

    context.expedition_build = __frontend_build__
    context.csrf_token = frappe.sessions.get_csrf_token()
    context.expedition_user = frappe.session.user

    return context
