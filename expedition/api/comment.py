"""Desk-compatible comments and record activity for Expedition popups."""

from __future__ import annotations

import html
from typing import Any

import frappe
from frappe import _
from frappe.desk.form import load as form_load
from frappe.desk.form.utils import add_comment as desk_add_comment
from frappe.desk.form.utils import update_comment as desk_update_comment
from frappe.utils.file_manager import save_file


def _reference_doc(reference_doctype: str, reference_name: str):
    if not reference_doctype or not reference_name:
        frappe.throw(_("A reference document is required"), frappe.ValidationError)
    doc = frappe.get_doc(reference_doctype, reference_name)
    doc.check_permission("read")
    return doc


def _safe(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _user_info(info: dict, user: str | None) -> dict:
    row = (info or {}).get(user or "") or {}
    return {
        "id": user or "",
        "name": row.get("fullname")
        or row.get("full_name")
        or user
        or _("Unknown user"),
        "image": row.get("image") or row.get("user_image") or "",
    }


def _files_for_comments(comment_names: list[str]) -> dict[str, list[dict]]:
    if not comment_names:
        return {}
    rows = frappe.get_all(
        "File",
        fields=["name", "file_name", "file_url", "is_private", "attached_to_name"],
        filters={
            "attached_to_doctype": "Comment",
            "attached_to_name": ["in", comment_names],
        },
        order_by="creation asc",
    )
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row.attached_to_name, []).append(
            {
                "name": row.name,
                "file_name": row.file_name,
                "file_url": row.file_url,
                "is_private": bool(row.is_private),
            }
        )
    return out


def _comment_permissions(row: dict) -> tuple[bool, bool]:
    owner = row.get("owner")
    editable = frappe.session.user in {"Administrator", owner}
    deletable = bool(
        frappe.has_permission("Comment", "delete")
        and (editable or "System Manager" in frappe.get_roles(frappe.session.user))
    )
    return editable, deletable


def _event(
    kind: str,
    name: str,
    creation: Any,
    content_html: str,
    actor: dict,
    **extra,
) -> dict:
    return {
        "id": f"{kind}:{name}",
        "kind": kind,
        "name": name,
        "creation": creation,
        "content_html": content_html or "",
        "actor": actor,
        **extra,
    }


def _version_html(version: dict, labels: dict[str, str]) -> str:
    try:
        data = frappe.parse_json(version.get("data") or "{}") or {}
    except Exception:
        data = {}
    changed = data.get("changed") or []
    lines = []
    for change in changed[:8]:
        if not isinstance(change, (list, tuple)) or not change:
            continue
        field = change[0]
        label = labels.get(field) or str(field).replace("_", " ").title()
        old = change[1] if len(change) > 1 else ""
        new = change[2] if len(change) > 2 else ""
        lines.append(
            f"<li><strong>{_safe(label)}</strong>: <span>{_safe(old)}</span> → <span>{_safe(new)}</span></li>"
        )
    if not lines:
        return _("Updated the document")
    suffix = f"<li>{_('Additional fields changed')}</li>" if len(changed) > 8 else ""
    return f"<div>{_('Updated the document')}</div><ul>{''.join(lines)}{suffix}</ul>"


def _normalize(doc, info: dict) -> list[dict]:
    users = info.get("user_info") or {}
    labels = {df.fieldname: df.label for df in doc.meta.fields}
    events: list[dict] = []
    comment_rows = list(info.get("comments") or [])
    comment_files = _files_for_comments(
        [row.get("name") for row in comment_rows if row.get("name")]
    )

    for row in comment_rows:
        editable, deletable = _comment_permissions(row)
        events.append(
            _event(
                "comment",
                row.get("name"),
                row.get("creation"),
                row.get("content") or "",
                _user_info(users, row.get("owner")),
                attachments=comment_files.get(row.get("name"), []),
                editable=editable,
                deletable=deletable,
            )
        )

    communications = list(info.get("communications") or []) + list(
        info.get("automated_messages") or []
    )
    for row in communications:
        attachments = row.get("attachments") or []
        if isinstance(attachments, str):
            try:
                attachments = frappe.parse_json(attachments)
            except Exception:
                attachments = []
        events.append(
            _event(
                "automated_message"
                if row.get("communication_type") == "Automated Message"
                else "communication",
                row.get("name"),
                row.get("communication_date") or row.get("creation"),
                frappe.utils.sanitize_html(
                    row.get("content") or "", always_sanitize=True
                ),
                {
                    "id": row.get("sender") or "",
                    "name": row.get("sender_full_name")
                    or row.get("sender")
                    or _("Unknown sender"),
                    "image": "",
                },
                attachments=attachments,
                subject=row.get("subject") or "",
                medium=row.get("communication_medium") or "",
                delivery_status=row.get("delivery_status") or "",
            )
        )

    log_groups = {
        "assignment": "assignment_logs",
        "share": "shared",
        "workflow": "workflow_logs",
        "attachment": "attachment_logs",
        "info": "info_logs",
        "like": "like_logs",
    }
    for kind, key in log_groups.items():
        for row in info.get(key) or []:
            events.append(
                _event(
                    kind,
                    row.get("name") or f"{row.get('creation')}:{len(events)}",
                    row.get("creation"),
                    frappe.utils.sanitize_html(
                        row.get("content") or kind.title(), always_sanitize=True
                    ),
                    _user_info(users, row.get("owner") or row.get("user")),
                    comment_type=row.get("comment_type") or "",
                )
            )

    for row in info.get("versions") or []:
        events.append(
            _event(
                "version",
                row.get("name"),
                row.get("creation"),
                _version_html(row, labels),
                _user_info(users, row.get("owner")),
            )
        )
    for row in info.get("views") or []:
        events.append(
            _event(
                "view",
                row.get("name"),
                row.get("creation"),
                _("Viewed the document"),
                _user_info(users, row.get("owner")),
            )
        )
    for row in info.get("energy_point_logs") or []:
        points = row.get("points") or 0
        events.append(
            _event(
                "energy",
                row.get("name"),
                row.get("creation"),
                _("Energy points: {0}").format(_safe(points)),
                _user_info(users, row.get("owner")),
                points=points,
            )
        )
    for row in info.get("milestones") or []:
        label = (
            labels.get(row.get("track_field"))
            or row.get("track_field")
            or _("Milestone")
        )
        content = _("Changed {0} to {1}").format(_safe(label), _safe(row.get("value")))
        events.append(
            _event(
                "milestone",
                row.get("name") or str(row.get("creation")),
                row.get("creation"),
                content,
                _user_info(users, row.get("owner")),
            )
        )
    for index, row in enumerate(info.get("additional_timeline_content") or []):
        content = row.get("content") or ""
        if not content and row.get("template"):
            try:
                content = frappe.render_template(
                    row.get("template"), row.get("template_data") or {}
                )
            except Exception:
                content = _("Additional activity")
        events.append(
            _event(
                "custom",
                row.get("name") or f"custom-{index}",
                row.get("creation"),
                frappe.utils.sanitize_html(content, always_sanitize=True),
                _user_info(users, row.get("owner")),
                icon=row.get("icon") or "",
            )
        )

    events.extend(
        [
            _event(
                "created",
                "created",
                doc.creation,
                _("Created the document"),
                _user_info(users, doc.owner),
            ),
            _event(
                "modified",
                "modified",
                doc.modified,
                _("Last edited the document"),
                _user_info(users, doc.modified_by),
            ),
        ]
    )
    events.sort(key=lambda row: str(row.get("creation") or ""), reverse=True)
    return events


@frappe.whitelist()
def get_activity(reference_doctype: str, reference_name: str) -> dict:
    doc = _reference_doc(reference_doctype, reference_name)
    form_load.get_docinfo(doc=doc)
    info = frappe.response.pop("docinfo", frappe._dict())
    events = _normalize(doc, info)
    return {
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "comment_count": sum(1 for row in events if row["kind"] == "comment"),
        "events": events,
        "has_more_communications": len(info.get("communications") or [])
        + len(info.get("automated_messages") or [])
        >= 21,
    }


@frappe.whitelist()
def get_more_communications(
    reference_doctype: str, reference_name: str, start: int = 0, limit: int = 21
) -> list[dict]:
    doc = _reference_doc(reference_doctype, reference_name)
    rows = form_load.get_communications(
        doc.doctype, doc.name, start=start, limit=min(int(limit), 50)
    )
    info = frappe._dict(
        comments=[],
        communications=[],
        automated_messages=[],
        user_info={},
        versions=[],
        views=[],
        energy_point_logs=[],
        additional_timeline_content=[],
        milestones=[],
        shared=[],
        assignment_logs=[],
        attachment_logs=[],
        info_logs=[],
        like_logs=[],
        workflow_logs=[],
    )
    for row in rows:
        info.automated_messages.append(row) if row.get(
            "communication_type"
        ) == "Automated Message" else info.communications.append(row)
    return [row for row in _normalize_communications(info) if row]


def _normalize_communications(info: dict) -> list[dict]:
    """Normalize communication-only pages without synthetic document events."""
    holder = frappe._dict(
        creation=None,
        modified=None,
        owner=None,
        modified_by=None,
        meta=frappe._dict(fields=[]),
    )
    rows = _normalize(holder, info)
    return [
        row for row in rows if row["kind"] in {"communication", "automated_message"}
    ]


@frappe.whitelist()
def search_mentions(search_term: str = "") -> list[dict]:
    from frappe.desk.search import get_names_for_mentions

    return get_names_for_mentions(search_term or "")


def _uploaded_files() -> list:
    files = frappe.request.files if getattr(frappe, "request", None) else None
    if not files:
        return []
    if hasattr(files, "getlist"):
        return files.getlist("files") or files.getlist("file")
    return list(files.values())


def _attach_uploads(comment_name: str) -> None:
    for uploaded in _uploaded_files():
        save_file(
            uploaded.filename,
            uploaded.stream.read(),
            "Comment",
            comment_name,
            is_private=1,
        )


def _reconcile_files(comment_name: str, retained_files: str | list | None) -> None:
    if retained_files is None:
        return
    retained = set(
        frappe.parse_json(retained_files)
        if isinstance(retained_files, str)
        else retained_files
    )
    for name in frappe.get_all(
        "File",
        filters={"attached_to_doctype": "Comment", "attached_to_name": comment_name},
        pluck="name",
    ):
        if name not in retained:
            frappe.delete_doc("File", name, ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def add(reference_doctype: str, reference_name: str, content: str) -> dict:
    _reference_doc(reference_doctype, reference_name)
    if (
        not frappe.utils.strip_html(content or "").strip()
        and "<img" not in (content or "").lower()
    ):
        frappe.throw(_("Comment cannot be empty"), frappe.ValidationError)
    comment = desk_add_comment(
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        content=content,
        comment_email=frappe.session.user,
        comment_by=frappe.utils.get_fullname(frappe.session.user),
    )
    _attach_uploads(comment.name)
    return get_activity(reference_doctype, reference_name)


@frappe.whitelist(methods=["POST"])
def update(name: str, content: str, retained_files: str | list | None = None) -> dict:
    comment = frappe.get_doc("Comment", name)
    _reference_doc(comment.reference_doctype, comment.reference_name)
    desk_update_comment(name, content)
    _reconcile_files(name, retained_files)
    _attach_uploads(name)
    return get_activity(comment.reference_doctype, comment.reference_name)


@frappe.whitelist(methods=["POST"])
def delete(name: str) -> dict:
    comment = frappe.get_doc("Comment", name)
    reference_doctype, reference_name = (
        comment.reference_doctype,
        comment.reference_name,
    )
    _reference_doc(reference_doctype, reference_name)
    editable, deletable = _comment_permissions(comment.as_dict())
    if not deletable:
        frappe.throw(_("Not permitted to delete this comment"), frappe.PermissionError)
    frappe.delete_doc("Comment", name, ignore_permissions=False)
    return get_activity(reference_doctype, reference_name)
