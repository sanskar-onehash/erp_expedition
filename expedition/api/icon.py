# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from base64 import b64decode
from binascii import Error as Base64Error

import frappe


CUSTOM_PREFIX = "custom:"
SVG_NS = "http://www.w3.org/2000/svg"
MAX_SVG_CHARS = 100_000
MAX_IMAGE_BYTES = 1_000_000
ET.register_namespace("", SVG_NS)

FORBIDDEN_TAGS = {
    "script",
    "foreignObject",
    "iframe",
    "object",
    "embed",
    "image",
    "audio",
    "video",
    "canvas",
    "style",
    "link",
    "metadata",
}
ALLOWED_TAGS = {
    "svg",
    "g",
    "path",
    "circle",
    "rect",
    "polygon",
    "polyline",
    "line",
    "ellipse",
}
ALLOWED_ATTRS = {
    "viewBox",
    "d",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "fill-rule",
    "clip-rule",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "width",
    "height",
    "points",
    "transform",
    "opacity",
}


def _current_user() -> str:
    return frappe.session.user or "Guest"


def _icon_doctype_ready() -> bool:
    return bool(frappe.db.exists("DocType", "Expedition Icon")) and frappe.db.table_exists("Expedition Icon")


def _has_icon_permission(permission_type: str) -> bool:
    if not _icon_doctype_ready():
        return False
    return frappe.has_permission("Expedition Icon", permission_type)


def can_create_icons() -> bool:
    return _has_icon_permission("create")


def can_read_icons() -> bool:
    return _has_icon_permission("read")


def can_write_icons() -> bool:
    return _has_icon_permission("write")


def can_delete_icons() -> bool:
    return _has_icon_permission("delete")


def _require_login() -> None:
    if _current_user() == "Guest":
        frappe.throw("Login required", frappe.PermissionError)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean_attr_name(name: str) -> str:
    return name.rsplit("}", 1)[-1] if "}" in name else name


def _unsafe_value(value: str) -> bool:
    v = (value or "").strip().lower()
    return "url(" in v or "javascript:" in v or "data:" in v or "http:" in v or "https:" in v


def _sanitize_node(node: ET.Element) -> None:
    tag = _local_name(node.tag)
    if tag in FORBIDDEN_TAGS or tag not in ALLOWED_TAGS:
        frappe.throw(f"Unsupported SVG element: {tag}", frappe.ValidationError)
    node.tag = f"{{{SVG_NS}}}{tag}"

    for child in list(node):
        _sanitize_node(child)

    for attr in list(node.attrib):
        clean = _clean_attr_name(attr)
        value = node.attrib[attr]
        if clean.startswith("on") or clean not in ALLOWED_ATTRS or _unsafe_value(value):
            del node.attrib[attr]
            continue
        if clean != attr:
            node.attrib[clean] = node.attrib.pop(attr)

    if tag != "svg":
        if "fill" in node.attrib and node.attrib["fill"].lower() != "none":
            node.attrib["fill"] = "currentColor"
        if "stroke" in node.attrib and node.attrib["stroke"].lower() != "none":
            node.attrib["stroke"] = "currentColor"


def sanitize_svg(svg_text: str) -> str:
    if not svg_text or len(svg_text) > MAX_SVG_CHARS:
        frappe.throw("SVG is empty or too large", frappe.ValidationError)
    if re.search(r"<!doctype|<!entity|<\s*script|foreignObject", svg_text, re.I):
        frappe.throw("SVG contains unsupported content", frappe.ValidationError)
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        frappe.throw("Invalid SVG", frappe.ValidationError)
    if _local_name(root.tag) != "svg":
        frappe.throw("File must be an SVG", frappe.ValidationError)
    _sanitize_node(root)
    if not root.get("viewBox"):
        root.set("viewBox", "0 0 24 24")
    root.set("xmlns", SVG_NS)
    return ET.tostring(root, encoding="unicode", method="xml")


def sanitize_image_data_url(data_url: str) -> tuple[str, str]:
    value = (data_url or "").strip()
    match = re.fullmatch(r"data:(image/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=\s]+)", value, re.I)
    if not match:
        frappe.throw("Image must be uploaded as a base64 data URL", frappe.ValidationError)

    mime = match.group(1).lower()
    if mime == "image/svg+xml":
        frappe.throw("SVG uploads must use the SVG sanitizer", frappe.ValidationError)

    encoded = re.sub(r"\s+", "", match.group(2))
    try:
        raw = b64decode(encoded, validate=True)
    except (Base64Error, ValueError):
        frappe.throw("Image data is not valid base64", frappe.ValidationError)

    if not raw or len(raw) > MAX_IMAGE_BYTES:
        frappe.throw("Image is empty or too large", frappe.ValidationError)

    return mime, f"data:{mime};base64,{encoded}"


def _icon_key(name: str) -> str:
    return f"{CUSTOM_PREFIX}{name}"


def _icon_to_dto(row: dict) -> dict:
    icon_format = row.get("icon_format") or ("Image" if row.get("image_data_url") else "SVG")
    return {
        "key": row["icon_key"] or _icon_key(row["name"]),
        "name": row["name"],
        "title": row["title"],
        "source": "custom",
        "icon_format": icon_format,
        "scope": row["scope"],
        "owner_user": row.get("owner_user"),
        "svg_content": row["svg_content"],
        "image_mime": row.get("image_mime"),
        "image_data_url": row.get("image_data_url"),
        "modified": str(row.get("modified") or ""),
        "can_edit": _can_edit_row(row),
        "can_delete": _can_delete_row(row),
    }


def _can_edit_row(row: dict) -> bool:
    if row.get("scope") == "Global":
        return can_write_icons()
    return row.get("owner_user") == _current_user() and can_write_icons()


def _can_delete_row(row: dict) -> bool:
    if row.get("scope") == "Global":
        return can_delete_icons()
    return row.get("owner_user") == _current_user() and can_delete_icons()


def _visible_filters() -> list[dict]:
    user = _current_user()
    filters = [{"scope": "Global", "is_active": 1}]
    if user != "Guest":
        filters.append({"scope": "Personal", "owner_user": user, "is_active": 1})
    return filters


@frappe.whitelist()
def list_icons() -> dict:
    rows: list[dict] = []
    if _icon_doctype_ready() and can_read_icons():
        for filters in _visible_filters():
            rows.extend(
                frappe.get_all(
                    "Expedition Icon",
                    filters=filters,
                    fields=[
                        "name",
                        "title",
                        "icon_key",
                        "icon_format",
                        "scope",
                        "owner_user",
                        "svg_content",
                        "image_mime",
                        "image_data_url",
                        "modified",
                    ],
                    order_by="scope asc, title asc",
                )
            )
    return {
        "custom": [_icon_to_dto(r) for r in rows],
        "can_create": can_create_icons(),
        "can_manage_global": can_create_icons(),
    }


def _get_icon_doc(icon_name: str, permission_type: str = "write"):
    doc = frappe.get_doc("Expedition Icon", icon_name)
    row = doc.as_dict()
    ok = _can_delete_row(row) if permission_type == "delete" else _can_edit_row(row)
    if not ok:
        frappe.throw("Not permitted to manage this icon", frappe.PermissionError)
    return doc


@frappe.whitelist()
def upload_icon(
    title: str,
    scope: str = "Personal",
    svg_text: str | None = None,
    image_data_url: str | None = None,
) -> dict:
    _require_login()
    if not can_create_icons():
        frappe.throw("Not permitted to create icons", frappe.PermissionError)
    scope = scope if scope in ("Personal", "Global") else "Personal"

    if image_data_url:
        image_mime, image_value = sanitize_image_data_url(image_data_url)
        icon_format = "Image"
        sanitized = None
    else:
        image_mime, image_value = None, None
        icon_format = "SVG"
        sanitized = sanitize_svg(svg_text or "")

    doc = frappe.new_doc("Expedition Icon")
    doc.title = (title or "Custom icon").strip()[:140]
    doc.icon_format = icon_format
    doc.scope = scope
    doc.owner_user = None if scope == "Global" else _current_user()
    doc.is_active = 1
    doc.svg_content = sanitized
    doc.image_mime = image_mime
    doc.image_data_url = image_value
    doc.insert(ignore_permissions=True)
    doc.icon_key = _icon_key(doc.name)
    doc.save(ignore_permissions=True)
    return _icon_to_dto(doc.as_dict())


@frappe.whitelist()
def update_icon(
    icon_name: str,
    title: str | None = None,
    svg_text: str | None = None,
    image_data_url: str | None = None,
    is_active: int | None = None,
) -> dict:
    _require_login()
    doc = _get_icon_doc(icon_name, "write")
    if title is not None:
        doc.title = title.strip()[:140] or doc.title
    if image_data_url is not None:
        image_mime, image_value = sanitize_image_data_url(image_data_url)
        doc.icon_format = "Image"
        doc.svg_content = None
        doc.image_mime = image_mime
        doc.image_data_url = image_value
    if svg_text is not None:
        doc.icon_format = "SVG"
        doc.svg_content = sanitize_svg(svg_text)
        doc.image_mime = None
        doc.image_data_url = None
    if is_active is not None:
        doc.is_active = 1 if int(is_active) else 0
    doc.save(ignore_permissions=True)
    return _icon_to_dto(doc.as_dict())


def _icon_is_used(icon_key: str) -> bool:
    if frappe.db.exists("Expedition Layer", {"icon": icon_key}):
        return True
    rows = frappe.get_all("Expedition Layer", fields=["group_config_json"], filters={"group_config_json": ["is", "set"]})
    for row in rows:
        try:
            cfg = json.loads(row.get("group_config_json") or "{}")
        except ValueError:
            continue
        if any(isinstance(v, dict) and v.get("icon") == icon_key for v in cfg.values()):
            return True
    return False


@frappe.whitelist()
def delete_icon(icon_name: str) -> dict:
    _require_login()
    doc = _get_icon_doc(icon_name, "delete")
    icon_key = doc.icon_key or _icon_key(doc.name)
    if _icon_is_used(icon_key):
        doc.is_active = 0
        doc.save(ignore_permissions=True)
        return {"disabled": icon_key}
    frappe.delete_doc("Expedition Icon", doc.name, ignore_permissions=True)
    return {"deleted": icon_key}


def assert_icon_readable(icon_key: str | None) -> None:
    if not icon_key or not icon_key.startswith(CUSTOM_PREFIX):
        return
    if not can_read_icons():
        frappe.throw("Not permitted to use custom icons", frappe.PermissionError)
    name = icon_key[len(CUSTOM_PREFIX):]
    if not frappe.db.exists("Expedition Icon", name):
        frappe.throw("Icon does not exist", frappe.ValidationError)
    doc = frappe.get_doc("Expedition Icon", name)
    if not doc.is_active:
        frappe.throw("Icon is inactive", frappe.ValidationError)
    if doc.scope == "Personal" and doc.owner_user != _current_user():
        frappe.throw("Not permitted to use this icon", frappe.PermissionError)
