# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""
Map save/load endpoints.

The primary user flow is: build a map on the canvas, then click Save.
That goes through the standard `frappe.model.document.save` path; we
do not need a custom endpoint for it. These helpers cover the bits
the canvas actually needs that aren't in the standard CRUD path:

  - load_full: fetch a map + all its layers + all its zones in one call
  - list_for_user: the user's "my maps" sidebar
  - templates: list of public templates users can clone
"""

import frappe


@frappe.whitelist(allow_guest=True)
def load_full(name: str) -> dict:
    """
    Return a map document with its layers, zones, and the basemap
    style applied. Used by the canvas on first render.

    Public maps (`is_public=1` or `is_template=1`) are browseable by
    guests so the page works without a login. Non-public maps require
    the standard Expedition Map read permission.
    """
    map_doc_meta = frappe.get_meta("Expedition Map")
    is_guest = frappe.session.user == "Guest"
    if is_guest:
        # Guest path: only public/template maps are visible.
        if not frappe.db.get_value(
            "Expedition Map", name, ["is_public", "is_template"]
        ):
            frappe.throw(
                f"Not permitted to read Expedition Map {name}",
                frappe.PermissionError,
            )
        is_public, is_template = frappe.db.get_value(
            "Expedition Map", name, ["is_public", "is_template"]
        )
        if not (is_public or is_template):
            frappe.throw(
                f"Not permitted to read Expedition Map {name}",
                frappe.PermissionError,
            )
    elif not frappe.has_permission("Expedition Map", "read", doc=name):
        frappe.throw(
            f"Not permitted to read Expedition Map {name}", frappe.PermissionError
        )

    map_doc = frappe.get_doc("Expedition Map", name)

    layers = frappe.get_all(
        "Expedition Layer",
        filters={"map": name, "enabled": 1},
        fields=[
            "name",
            "title",
            "sequence",
            "source_doctype",
            "color",
            "icon",
            "size",
            "cluster",
            "heatmap",
            "preset",
            "label_field",
            "popup_template",
            "filter_json",
            "group_by_field",
            "group_config_json",
            "popup_fields_json",
            "click_action",
            "radius_enabled",
            "radius_field",
            "radius_meters",
            "radius_opacity",
        ],
        order_by="sequence asc, modified asc",
    )
    zones = frappe.get_all(
        "Expedition Zone",
        filters={"map": name},
        fields=[
            "name",
            "title",
            "zone_type",
            "geometry",
            "color",
            "fill_opacity",
            "stroke_color",
            "stroke_width",
            "tag",
            "centroid_lat",
            "centroid_lng",
        ],
        order_by="modified asc",
    )

    # Touch last_opened_at asynchronously (cheap DB write)
    frappe.enqueue(
        "expedition.doctype.expedition_map.expedition_map.touch_last_opened",
        name=name,
        enqueue_after_commit=True,
        queue="short",
    )

    return {
        "map": map_doc.as_dict(),
        "layers": layers,
        "zones": zones,
    }


@frappe.whitelist()
def list_for_user(include_public: int = 1, search: str | None = None) -> list[dict]:
    """
    List Expedition Maps the current user can read.
    Used by the canvas's "Open Map" picker.
    """
    or_filters = {}
    if include_public:
        # Standard get_all won't combine owner + public in one call.
        # We do two queries and merge.
        own = frappe.get_all(
            "Expedition Map",
            filters={"owner_user": frappe.session.user},
            fields=["name", "title", "basemap_style", "last_opened_at", "modified"],
            order_by="modified desc",
            limit=50,
        )
        public = frappe.get_all(
            "Expedition Map",
            filters={"is_public": 1},
            fields=["name", "title", "basemap_style", "last_opened_at", "modified"],
            order_by="modified desc",
            limit=50,
        )
        seen = set()
        merged = []
        for m in own + public:
            if m["name"] in seen:
                continue
            seen.add(m["name"])
            merged.append(m)
    else:
        merged = frappe.get_all(
            "Expedition Map",
            filters={"owner_user": frappe.session.user},
            fields=["name", "title", "basemap_style", "last_opened_at", "modified"],
            order_by="modified desc",
            limit=50,
        )

    if search:
        merged = [m for m in merged if search.lower() in (m["title"] or "").lower()]
    return merged


@frappe.whitelist(allow_guest=True)
def list_templates(category: str | None = None) -> list[dict]:
    """List public Expedition Map templates the user can clone. Open to
    guests so the canvas's "Browse templates" picker works pre-login."""
    filters = {"is_template": 1}
    if category:
        filters["template_category"] = category
    return frappe.get_all(
        "Expedition Map",
        filters=filters,
        fields=["name", "title", "template_category", "basemap_style"],
        order_by="title asc",
    )


@frappe.whitelist()
def update_basemap_style(name: str, basemap_style: str) -> dict:
    """
    Persist the chosen basemap skin to the Expedition Map doc so the
    user's skin choice survives a reload. Permission-gated on the map.
    """
    if not frappe.has_permission("Expedition Map", "write", doc=name):
        frappe.throw(
            f"Not permitted to update Expedition Map {name}",
            frappe.PermissionError,
        )
    if basemap_style not in (
        "light",
        "dark",
        "monochrome",
        "ofm-liberty",
        "ofm-bright",
        "ofm-positron",
        "ofm-dark",
    ):
        frappe.throw(f"Unknown basemap_style: {basemap_style}", frappe.ValidationError)
    frappe.db.set_value("Expedition Map", name, "basemap_style", basemap_style)
    frappe.db.commit()
    return {"name": name, "basemap_style": basemap_style}


@frappe.whitelist()
def save_map_card(
    title: str,
    viewport: dict | None = None,
    filters_json: str | None = None,
    summary_json: str | None = None,
    is_public: int = 0,
    campaign: str | None = None,
    basemap_style: str = "light",
) -> dict:
    """
    Create a new Expedition Map (aka "map card") with the current canvas
    state. Returns the new doc name. If a map with this title already
    exists for the user, update it instead (idempotent save-as).

    The viewport, filters_json, and basemap_style capture the current
    canvas view so the user can return to the same state later.
    """
    if not frappe.has_permission("Expedition Map", "create"):
        frappe.throw("Not permitted to create maps", frappe.PermissionError)

    # Check for existing map with the same title (owned by this user).
    existing_name = frappe.db.get_value(
        "Expedition Map",
        {"title": title, "owner_user": frappe.session.user},
        "name",
    )

    if existing_name:
        # Update existing: update viewport, filters, summary, basemap_style.
        doc = frappe.get_doc("Expedition Map", existing_name)
        if not frappe.has_permission("Expedition Map", "write", doc=existing_name):
            frappe.throw("Not permitted to update this map", frappe.PermissionError)
        if viewport is not None:
            doc.viewport = frappe.json.dumps(viewport)
        if filters_json is not None:
            doc.filters_json = filters_json
        if summary_json is not None:
            doc.summary_json = summary_json
        if basemap_style:
            doc.basemap_style = basemap_style
        if campaign is not None:
            doc.campaign = campaign
        doc.is_public = bool(is_public)
        doc.save(ignore_permissions=True)
        return {"name": doc.name, "title": doc.title, "updated": True}

    # Create new.
    doc = frappe.new_doc("Expedition Map")
    doc.title = title
    doc.owner_user = frappe.session.user
    doc.is_public = bool(is_public)
    doc.basemap_style = basemap_style or "light"
    if viewport is not None:
        doc.viewport = frappe.json.dumps(viewport)
    if filters_json is not None:
        doc.filters_json = filters_json
    if summary_json is not None:
        doc.summary_json = summary_json
    if campaign:
        doc.campaign = campaign
    doc.insert(ignore_permissions=True)
    return {"name": doc.name, "title": doc.title, "updated": False}


@frappe.whitelist()
def clone_template(template_name: str, title: str | None = None) -> dict:
    """
    Clone a template map into a new Expedition Map owned by the current
    user. Copies layers and zones, but clears template flags so the clone
    lives in the user's private collection.
    """
    if not frappe.has_permission("Expedition Map", "create"):
        frappe.throw("Not permitted to create maps", frappe.PermissionError)
    template = frappe.get_doc("Expedition Map", template_name)
    if not template.is_template:
        frappe.throw("Source map is not a template", frappe.ValidationError)

    # Create the clone.
    new_title = (title or template.title or "Untitled") + " (Copy)"
    new_map = frappe.new_doc("Expedition Map")
    new_map.title = new_title
    new_map.owner_user = frappe.session.user
    new_map.is_public = 0
    new_map.is_template = 0
    new_map.basemap_style = template.basemap_style or "light"
    new_map.insert(ignore_permissions=True)

    # Clone layers: create a layer for each layer in the template,
    # pointing at the new map and copying all fields.
    template_layers = frappe.get_all(
        "Expedition Layer",
        filters={"map": template_name},
        fields=[
            "name",
            "title",
            "source_doctype",
            "latitude_field",
            "longitude_field",
            "label_field",
            "filter_json",
            "color",
            "icon",
            "size",
            "cluster",
            "heatmap",
            "stroke_color",
            "stroke_width",
            "fill_opacity",
            "popup_template",
            "popup_fields_json",
            "group_by_field",
            "group_config_json",
            "click_action",
            "sequence",
        ],
        order_by="sequence asc",
    )
    for tl in template_layers:
        new_layer = frappe.new_doc("Expedition Layer")
        new_layer.map = new_map.name
        new_layer.title = tl.title
        new_layer.source_doctype = tl.source_doctype
        new_layer.latitude_field = tl.latitude_field
        new_layer.longitude_field = tl.longitude_field
        new_layer.label_field = tl.label_field or ""
        new_layer.filter_json = tl.filter_json or ""
        new_layer.color = tl.color
        new_layer.icon = tl.icon or ""
        new_layer.size = tl.size
        new_layer.cluster = tl.cluster if tl.cluster is not None else 1
        new_layer.heatmap = tl.heatmap if tl.heatmap is not None else 0
        new_layer.stroke_color = tl.stroke_color
        new_layer.stroke_width = tl.stroke_width if tl.stroke_width is not None else 2
        new_layer.fill_opacity = tl.fill_opacity if tl.fill_opacity is not None else 0.6
        new_layer.popup_template = tl.popup_template or ""
        new_layer.popup_fields_json = tl.popup_fields_json or ""
        new_layer.group_by_field = tl.group_by_field or ""
        new_layer.group_config_json = tl.group_config_json or ""
        new_layer.click_action = tl.click_action or "popup"
        new_layer.sequence = tl.sequence
        new_layer.enabled = 1
        new_layer.use_source_permissions = 1
        new_layer.insert(ignore_permissions=True)

    # Clone zones.
    template_zones = frappe.get_all(
        "Expedition Zone",
        filters={"map": template_name},
        fields=[
            "title",
            "zone_type",
            "geometry",
            "color",
            "fill_opacity",
            "stroke_color",
            "stroke_width",
            "tag",
        ],
    )
    for tz in template_zones:
        new_zone = frappe.new_doc("Expedition Zone")
        new_zone.map = new_map.name
        new_zone.title = tz.title
        new_zone.zone_type = tz.zone_type
        new_zone.geometry = tz.geometry
        new_zone.color = tz.color
        new_zone.fill_opacity = tz.fill_opacity
        new_zone.stroke_color = tz.stroke_color
        new_zone.stroke_width = tz.stroke_width
        new_zone.tag = tz.tag
        new_zone.insert(ignore_permissions=True)

    return {"name": new_map.name, "title": new_map.title}
