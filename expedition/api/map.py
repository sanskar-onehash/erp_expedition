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
import frappe.share
from expedition.api.permission import (
    assert_map_read,
    assert_map_share,
    assert_map_write,
    map_permission,
)


BASE_MAP_FIELDS = [
    "name",
    "title",
    "basemap_style",
    "last_opened_at",
    "modified",
    "owner",
    "owner_user",
    "is_public",
]


def _has_map_field(fieldname: str) -> bool:
    return frappe.db.has_column("Expedition Map", fieldname)


def _map_fields() -> list[str]:
    fields = list(BASE_MAP_FIELDS)
    if _has_map_field("public_access"):
        fields.append("public_access")
    return fields


def _current_user() -> str:
    return frappe.session.user


def _normalize_viewport(viewport):
    if viewport in (None, ""):
        return None
    if isinstance(viewport, str):
        return frappe.parse_json(viewport)
    return viewport


def _map_access(row) -> str:
    user = _current_user()
    if row.get("owner_user") == user or row.get("owner") == user:
        return "owner"
    if row.get("is_public"):
        return "public"
    return "shared"


def _dedupe_maps(rows: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for row in rows:
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        row["access"] = _map_access(row)
        out.append(row)
    return out


def _owned_maps(user: str) -> list[dict]:
    fields = _map_fields()
    by_owner_user = frappe.get_all(
        "Expedition Map",
        filters={"owner_user": user},
        fields=fields,
        order_by="modified desc",
        limit=50,
    )
    by_doc_owner = frappe.get_all(
        "Expedition Map",
        filters={"owner": user},
        fields=fields,
        order_by="modified desc",
        limit=50,
    )
    return _dedupe_maps(by_owner_user + by_doc_owner)


def _normalize_public_access(value: str | None) -> str:
    return "Writable" if value == "Writable" else "Read Only"


def _parse_access_overrides(raw) -> list[dict]:
    try:
        rows = frappe.parse_json(raw) if raw else []
    except Exception:
        rows = []
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        user = str(row.get("user") or "").strip()
        access = str(row.get("access") or "").lower()
        if user and access in {"read", "write"}:
            out.append({"user": user, "access": access})
    return out


def _has_zone_stroke_style() -> bool:
    return frappe.db.has_column("Expedition Zone", "stroke_style")


@frappe.whitelist(allow_guest=True)
def load_full(name: str) -> dict:
    """
    Return a map document with its layers, zones, and the basemap
    style applied. Used by the canvas on first render.

    Public maps (`is_public=1` or `is_template=1`) are browseable by
    guests so the page works without a login. Non-public maps require
    the standard Expedition Map read permission.
    """
    assert_map_read(name)

    map_doc = frappe.get_doc("Expedition Map", name)

    layer_fields = [
        "name",
        "title",
        "map",
        "sequence",
        "enabled",
        "source_doctype",
        "location_source",
        "location_link_field",
        "location_doctype",
        "location_reverse_link_field",
        "location_fields_json",
        "color",
        "icon",
        "size",
        "cluster",
        "heatmap",
        "heatmap_mode",
        "heatmap_weight_field",
        "heatmap_weight_min",
        "heatmap_weight_max",
        "heatmap_weight_scale",
        "heatmap_weight_stops_json",
        "heatmap_radius_min",
        "heatmap_radius_max",
        "heatmap_intensity_min",
        "heatmap_intensity_max",
        "heatmap_opacity",
        "heatmap_ramp_json",
        "territory_enabled",
        "territory_color",
        "territory_opacity",
        "territory_padding_meters",
        "preset",
        "label_field",
        "popup_template",
        "filter_json",
        "group_by_field",
        "group_config_json",
        "popup_fields_json",
        "linked_metrics_json",
        "linked_metric_filters_json",
        "click_action",
        "radius_enabled",
        "radius_field",
        "radius_meters",
        "radius_opacity",
    ]
    if frappe.db.has_column("Expedition Layer", "pin_min_zoom"):
        layer_fields.insert(layer_fields.index("cluster"), "pin_min_zoom")

    layers = frappe.get_all(
        "Expedition Layer",
        filters={"map": name, "enabled": 1},
        fields=layer_fields,
        order_by="sequence asc, modified asc",
    )
    zone_fields = [
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
    ]
    if _has_zone_stroke_style():
        zone_fields.insert(8, "stroke_style")
    zones = frappe.get_all(
        "Expedition Zone",
        filters={"map": name},
        fields=zone_fields,
        order_by="modified asc",
    )
    for zone in zones:
        zone.setdefault("stroke_style", "solid")

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
        "permissions": {
            "write": 1 if map_permission(name, "write") else 0,
            "share": 1 if map_permission(name, "share") else 0,
        },
    }


@frappe.whitelist()
def list_for_user(include_public: int = 1, search: str | None = None) -> list[dict]:
    """
    List Expedition Maps the current user can read.
    Used by the canvas's "Open Map" picker.
    """
    user = _current_user()
    if user == "Guest":
        if not include_public:
            return []
        merged = frappe.get_all(
            "Expedition Map",
            filters={"is_public": 1},
            fields=_map_fields(),
            order_by="modified desc",
            limit=50,
        )
        if search:
            merged = [m for m in merged if search.lower() in (m["title"] or "").lower()]
        return _dedupe_maps(merged)

    if include_public:
        # Standard get_all won't combine owner + shared + public in one
        # call. We do small bounded queries and merge client-side.
        own = _owned_maps(user)
        shared_names = [
            row.share_name
            for row in frappe.get_all(
                "DocShare",
                filters={
                    "share_doctype": "Expedition Map",
                    "user": user,
                    "read": 1,
                },
                fields=["share_name"],
                limit=50,
            )
        ]
        shared = (
            frappe.get_all(
                "Expedition Map",
                filters={"name": ["in", shared_names]},
                fields=_map_fields(),
                order_by="modified desc",
                limit=50,
            )
            if shared_names
            else []
        )
        public = frappe.get_all(
            "Expedition Map",
            filters={"is_public": 1},
            fields=_map_fields(),
            order_by="modified desc",
            limit=50,
        )
        merged = _dedupe_maps(own + shared + public)
    else:
        owned = _owned_maps(user)
        shared_names = [
            row.share_name
            for row in frappe.get_all(
                "DocShare",
                filters={
                    "share_doctype": "Expedition Map",
                    "user": user,
                    "read": 1,
                },
                fields=["share_name"],
                limit=50,
            )
        ]
        shared = (
            frappe.get_all(
                "Expedition Map",
                filters={"name": ["in", shared_names]},
                fields=_map_fields(),
                order_by="modified desc",
                limit=50,
            )
            if shared_names
            else []
        )
        merged = _dedupe_maps(owned + shared)

    if search:
        merged = [m for m in merged if search.lower() in (m["title"] or "").lower()]
    return merged


@frappe.whitelist()
def create_blank_map(
    title: str,
    basemap_style: str = "ofm-liberty",
    viewport: dict | str | None = None,
    is_public: int = 0,
    public_access: str = "Read Only",
) -> dict:
    """Create a private blank map owned by the current user."""
    if not frappe.has_permission("Expedition Map", "create"):
        frappe.throw("Not permitted to create maps", frappe.PermissionError)
    title = (title or "").strip()
    if not title:
        frappe.throw("Map name is required", frappe.ValidationError)

    doc = frappe.new_doc("Expedition Map")
    doc.title = title
    doc.owner_user = _current_user()
    doc.is_public = 1 if int(is_public or 0) else 0
    if _has_map_field("public_access"):
        doc.public_access = _normalize_public_access(public_access)
    doc.basemap_style = basemap_style or "ofm-liberty"
    normalized_viewport = _normalize_viewport(viewport)
    if normalized_viewport is not None:
        doc.viewport = frappe.json.dumps(normalized_viewport)
    doc.insert()
    return doc.as_dict()


@frappe.whitelist()
def update_map(
    name: str,
    title: str | None = None,
    viewport: dict | str | None = None,
    basemap_style: str | None = None,
    is_public: int | None = None,
    public_access: str | None = None,
) -> dict:
    """Persist editable map metadata and camera state."""
    assert_map_write(name)
    doc = frappe.get_doc("Expedition Map", name)
    if title is not None:
        title = title.strip()
        if not title:
            frappe.throw("Map name is required", frappe.ValidationError)
        doc.title = title
    if viewport is not None:
        doc.viewport = frappe.json.dumps(_normalize_viewport(viewport))
    if basemap_style:
        doc.basemap_style = basemap_style
    if is_public is not None:
        doc.is_public = 1 if int(is_public or 0) else 0
    if public_access is not None and _has_map_field("public_access"):
        doc.public_access = _normalize_public_access(public_access)
    doc.save()
    return doc.as_dict()


@frappe.whitelist()
def get_shared_users(name: str) -> list[dict]:
    """Return explicit user shares for a map."""
    assert_map_share(name)
    override_by_user = {
        row["user"]: row["access"]
        for row in _parse_access_overrides(
            frappe.db.get_value("Expedition Map", name, "access_overrides_json")
            if _has_map_field("access_overrides_json")
            else ""
        )
    }
    rows = frappe.get_all(
        "DocShare",
        filters={"share_doctype": "Expedition Map", "share_name": name, "read": 1},
        fields=["user", "write", "share"],
        order_by="user asc",
    )
    by_user = {}
    for row in rows:
        by_user[row.user] = {
            "user": row.user,
            "read": 1,
            "write": 1 if int(row.write or 0) else 0,
            "share": 1 if int(row.share or 0) else 0,
            "access": "write" if int(row.write or 0) else "read",
        }
    for user, access in override_by_user.items():
        by_user[user] = {
            "user": user,
            "read": 1,
            "write": 1 if access == "write" else 0,
            "share": by_user.get(user, {}).get("share", 0),
            "access": access,
        }
    return sorted(by_user.values(), key=lambda row: row["user"])


@frappe.whitelist()
def share_map(name: str, users: list | str | None = None) -> list[dict]:
    """Replace explicit shares for a map with the provided users."""
    assert_map_share(name)
    parsed = frappe.parse_json(users) if isinstance(users, str) else users
    next_shares = {}
    for item in parsed or []:
        if isinstance(item, dict):
            user = str(item.get("user") or item.get("value") or "").strip()
            access = str(item.get("access") or "").lower()
            if access not in {"read", "write"}:
                access = "write" if int(item.get("write") or 0) else "read"
            share = 1 if int(item.get("share") or 0) else 0
            read = 1 if int(item.get("read", 1) or 0) else 0
        else:
            user = str(item or "").strip()
            access = "read"
            share = 0
            read = 1
        if user and user != _current_user() and read:
            next_shares[user] = {"access": access, "share": share}

    existing = frappe.get_all(
        "DocShare",
        filters={"share_doctype": "Expedition Map", "share_name": name, "read": 1},
        fields=["name", "user", "write", "share"],
    )
    for row in existing:
        if row.user not in next_shares:
            frappe.delete_doc("DocShare", row.name, ignore_permissions=True)

    existing_users = {row.user for row in existing}
    for row in existing:
        if row.user in next_shares:
            write_value = 1 if next_shares[row.user]["access"] == "write" else 0
            if int(row.write or 0) != write_value:
                frappe.db.set_value("DocShare", row.name, "write", write_value)
            share_value = 1 if next_shares[row.user]["share"] else 0
            if int(row.share or 0) != share_value:
                frappe.db.set_value("DocShare", row.name, "share", share_value)

    for user, share_row in next_shares.items():
        if user in existing_users:
            continue
        if not frappe.db.exists("User", user):
            frappe.throw(f"Unknown user: {user}", frappe.ValidationError)
        frappe.share.add(
            "Expedition Map",
            name,
            user,
            read=1,
            write=1 if share_row["access"] == "write" else 0,
            share=1 if share_row["share"] else 0,
            notify=0,
        )
    doc = frappe.get_doc("Expedition Map", name)
    if _has_map_field("access_overrides_json"):
        doc.access_overrides_json = frappe.json.dumps(
            [{"user": user, "access": row["access"]} for user, row in sorted(next_shares.items())]
        )
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return get_shared_users(name)


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
    assert_map_write(name)
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
        assert_map_write(existing_name)
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


def _clone_child_table(
    child_doctype: str, source_filters: dict, target_filters: dict
) -> None:
    """Copy child table rows from a source doc to a target doc."""
    for row in frappe.get_all(child_doctype, filters=source_filters, fields=["*"]):
        new_row = frappe.new_doc(child_doctype)
        new_row.update({k: v for k, v in row.items() if k != "name"})
        new_row.parent = target_filters.get("parent")
        new_row.parenttype = target_filters.get("parenttype")
        new_row.parentfield = row.get("parentfield", "")
        new_row.insert(ignore_permissions=True)


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
    template_layer_fields = [
        "name",
        "title",
        "source_doctype",
        "location_source",
        "location_link_field",
        "location_doctype",
        "location_reverse_link_field",
        "location_fields_json",
        "latitude_field",
        "longitude_field",
        "label_field",
        "filter_json",
        "color",
        "icon",
        "size",
        "cluster",
        "heatmap",
        "heatmap_mode",
        "heatmap_weight_field",
        "heatmap_weight_min",
        "heatmap_weight_max",
        "heatmap_weight_scale",
        "heatmap_weight_stops_json",
        "heatmap_radius_min",
        "heatmap_radius_max",
        "heatmap_intensity_min",
        "heatmap_intensity_max",
        "heatmap_opacity",
        "heatmap_ramp_json",
        "stroke_color",
        "stroke_width",
        "fill_opacity",
        "popup_template",
        "popup_fields_json",
        "linked_metrics_json",
        "linked_metric_filters_json",
        "group_by_field",
        "group_config_json",
        "click_action",
        "sequence",
    ]
    if frappe.db.has_column("Expedition Layer", "pin_min_zoom"):
        template_layer_fields.insert(template_layer_fields.index("cluster"), "pin_min_zoom")

    template_layers = frappe.get_all(
        "Expedition Layer",
        filters={"map": template_name},
        fields=template_layer_fields,
        order_by="sequence asc",
    )
    for tl in template_layers:
        new_layer = frappe.new_doc("Expedition Layer")
        new_layer.map = new_map.name
        new_layer.title = tl.title
        new_layer.source_doctype = tl.source_doctype
        new_layer.location_source = tl.location_source or "Direct Fields"
        new_layer.location_link_field = tl.location_link_field or ""
        new_layer.location_doctype = tl.location_doctype or ""
        new_layer.location_reverse_link_field = tl.location_reverse_link_field or ""
        new_layer.location_fields_json = tl.location_fields_json or ""
        new_layer.latitude_field = tl.latitude_field
        new_layer.longitude_field = tl.longitude_field
        new_layer.label_field = tl.label_field or ""
        new_layer.filter_json = tl.filter_json or ""
        new_layer.color = tl.color
        new_layer.icon = tl.icon or ""
        new_layer.size = tl.size
        new_layer.pin_min_zoom = getattr(tl, "pin_min_zoom", 0) or 0
        new_layer.cluster = tl.cluster if tl.cluster is not None else 1
        new_layer.heatmap = tl.heatmap if tl.heatmap is not None else 0
        new_layer.heatmap_mode = tl.heatmap_mode or "count"
        new_layer.heatmap_weight_field = tl.heatmap_weight_field or ""
        new_layer.heatmap_weight_min = tl.heatmap_weight_min
        new_layer.heatmap_weight_max = tl.heatmap_weight_max
        new_layer.heatmap_weight_scale = tl.heatmap_weight_scale or "linear"
        new_layer.heatmap_weight_stops_json = tl.heatmap_weight_stops_json or ""
        new_layer.heatmap_radius_min = tl.heatmap_radius_min or 10
        new_layer.heatmap_radius_max = tl.heatmap_radius_max or 30
        new_layer.heatmap_intensity_min = tl.heatmap_intensity_min or 1
        new_layer.heatmap_intensity_max = tl.heatmap_intensity_max or 2.5
        new_layer.heatmap_opacity = (
            tl.heatmap_opacity if tl.heatmap_opacity is not None else 0.75
        )
        new_layer.heatmap_ramp_json = tl.heatmap_ramp_json or ""
        new_layer.stroke_color = tl.stroke_color
        new_layer.stroke_width = tl.stroke_width if tl.stroke_width is not None else 2
        new_layer.fill_opacity = tl.fill_opacity if tl.fill_opacity is not None else 0.6
        new_layer.popup_template = tl.popup_template or ""
        new_layer.popup_fields_json = tl.popup_fields_json or ""
        new_layer.linked_metrics_json = tl.linked_metrics_json or ""
        new_layer.linked_metric_filters_json = tl.linked_metric_filters_json or ""
        new_layer.group_by_field = tl.group_by_field or ""
        new_layer.group_config_json = tl.group_config_json or ""
        new_layer.click_action = tl.click_action or "popup"
        new_layer.sequence = tl.sequence
        new_layer.enabled = 1
        new_layer.use_source_permissions = 1
        new_layer.insert(ignore_permissions=True)

        # Clone filter child table rows from the template layer.
        _clone_child_table(
            "Expedition Layer Filter",
            {"parent": tl.name, "parenttype": "Expedition Layer"},
            {"parent": new_layer.name, "parenttype": "Expedition Layer"},
        )

        # Clone group child table rows from the template layer.
        _clone_child_table(
            "Expedition Layer Group",
            {"parent": tl.name, "parenttype": "Expedition Layer"},
            {"parent": new_layer.name, "parenttype": "Expedition Layer"},
        )

    # Clone zones.
    zone_fields = [
        "title",
        "zone_type",
        "geometry",
        "color",
        "fill_opacity",
        "stroke_color",
        "stroke_width",
        "tag",
    ]
    has_stroke_style = _has_zone_stroke_style()
    if has_stroke_style:
        zone_fields.insert(7, "stroke_style")
    template_zones = frappe.get_all(
        "Expedition Zone",
        filters={"map": template_name},
        fields=zone_fields,
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
        if has_stroke_style:
            new_zone.stroke_style = getattr(tz, "stroke_style", None) or "solid"
        new_zone.tag = tz.tag
        new_zone.insert(ignore_permissions=True)

    return {"name": new_map.name, "title": new_map.title}
