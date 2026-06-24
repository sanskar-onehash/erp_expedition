"""Scheduled tasks. Wired in hooks.py — never on the request path."""

import frappe


def recompute_insights() -> None:
    """Hourly: refresh Expedition Insight rows for all maps."""
    from expedition.api.insight import recompute_all

    try:
        count = recompute_all()
        frappe.logger("expedition").info(f"Recomputed {count} insights")
    except Exception:
        # Do not raise — scheduler tasks must not block other hooks.
        frappe.logger("expedition").exception("Insight recompute failed")


def recompute_zone_coverage() -> None:
    """Daily: store per-zone row counts on each map's summary_json."""
    frappe.db.sql("""
		update `tabExpedition Map` map
		left join (
			select map, count(*) as n
			from `tabExpedition Zone`
			where ifnull(tag, '') != ''
			group by map
		) z on z.map = map.name
		set map.summary_json = JSON_OBJECT('zones', coalesce(z.n, 0))
		where 1=1
	""")
