# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""OpenStreetMap place search for the Expedition canvas.

The public Nominatim service is deliberately called from the server instead of
the browser. This gives us one cache and one upstream throttle for the whole
site, keeps the provider replaceable through site_config, and prevents the
search UI from turning into a prohibited autocomplete client.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, get_request_session


DEFAULT_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_USER_AGENT = "Expedition-Frappe/0.1 (engineering@onehash.ai)"
CACHE_TTL_SECONDS = 24 * 60 * 60
UPSTREAM_INTERVAL_SECONDS = 1.0
_UPSTREAM_LOCK_KEY = "expedition:place-search:nominatim-lock"
_LAST_UPSTREAM_REQUEST_KEY = "expedition:place-search:nominatim-last-request"


def _clean_query(query: str | None) -> str:
	query = " ".join(str(query or "").split())
	if len(query) < 2:
		frappe.throw(_("Enter at least two characters to search places."), frappe.ValidationError)
	if len(query) > 200:
		frappe.throw(_("Place search is limited to 200 characters."), frappe.ValidationError)
	return query


def _clean_language(language: str | None) -> str:
	# Accept-Language is forwarded as a preference only. Keep it compact and
	# header-safe; a blank value lets Nominatim choose its default language.
	return re.sub(r"[^A-Za-z0-9,;=._-]", "", str(language or ""))[:64]


def _cache_key(query: str, language: str, limit: int) -> str:
	raw = f"{query.casefold()}\n{language.casefold()}\n{limit}"
	digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
	return f"expedition:place-search:v1:{digest}"


def _finite_float(value: Any) -> float | None:
	try:
		value = float(value)
	except (TypeError, ValueError):
		return None
	return value if math.isfinite(value) else None


def _normalise_result(row: dict[str, Any]) -> dict[str, Any] | None:
	lat = _finite_float(row.get("lat"))
	lng = _finite_float(row.get("lon"))
	if lat is None or lng is None or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
		return None

	bounds = None
	raw_bounds = row.get("boundingbox")
	if isinstance(raw_bounds, (list, tuple)) and len(raw_bounds) == 4:
		south, north, west, east = (_finite_float(value) for value in raw_bounds)
		if (
			None not in (south, north, west, east)
			and -90 <= south <= north <= 90
			and -180 <= west <= east <= 180
		):
			bounds = {
				"south": south,
				"north": north,
				"west": west,
				"east": east,
			}

	display_name = str(row.get("display_name") or row.get("name") or "").strip()
	if not display_name:
		return None

	osm_type = str(row.get("osm_type") or "").strip()
	osm_id = str(row.get("osm_id") or row.get("place_id") or "").strip()
	return {
		"id": f"{osm_type}:{osm_id}" if osm_type or osm_id else f"{lat}:{lng}",
		"name": str(row.get("name") or display_name.split(",", 1)[0]).strip(),
		"display_name": display_name,
		"category": str(row.get("category") or row.get("class") or "").strip(),
		"type": str(row.get("type") or "").strip(),
		"latitude": lat,
		"longitude": lng,
		"bounds": bounds,
	}


def _search_url() -> str:
	return str(frappe.conf.get("expedition_nominatim_url") or DEFAULT_SEARCH_URL).strip()


def _user_agent() -> str:
	return str(
		frappe.conf.get("expedition_geocoder_user_agent") or DEFAULT_USER_AGENT
	).strip()


def _request_places(query: str, language: str, limit: int) -> list[dict[str, Any]]:
	headers = {
		"Accept": "application/json",
		"User-Agent": _user_agent(),
	}
	if language:
		headers["Accept-Language"] = language

	response = get_request_session().get(
		_search_url(),
		params={
			"q": query,
			"format": "jsonv2",
			"addressdetails": 1,
			"limit": limit,
		},
		headers=headers,
		timeout=8,
	)
	response.raise_for_status()
	payload = response.json()
	if not isinstance(payload, list):
		return []

	results = []
	for row in payload:
		if not isinstance(row, dict):
			continue
		result = _normalise_result(row)
		if result:
			results.append(result)
	return results


def _fetch_with_upstream_throttle(
	query: str, language: str, limit: int, cache_key: str
) -> list[dict[str, Any]]:
	# Redis' lock is shared by all web workers. Recheck the response cache after
	# acquiring it because another request may have populated the same query
	# while this request was waiting.
	lock_name = frappe.cache.make_key(_UPSTREAM_LOCK_KEY)
	with frappe.cache.lock(lock_name, timeout=10, blocking_timeout=5):
		cached = frappe.cache.get_value(cache_key, expires=True)
		if cached is not None:
			return cached

		last_request = frappe.cache.get_value(_LAST_UPSTREAM_REQUEST_KEY, expires=True)
		if last_request:
			remaining = UPSTREAM_INTERVAL_SECONDS - (time.time() - float(last_request))
			if remaining > 0:
				time.sleep(min(remaining, UPSTREAM_INTERVAL_SECONDS))

		# Store the timestamp immediately before the request. This keeps starts of
		# upstream calls at least one second apart across the site.
		frappe.cache.set_value(
			_LAST_UPSTREAM_REQUEST_KEY,
			time.time(),
			expires_in_sec=5,
		)
		results = _request_places(query, language, limit)
		frappe.cache.set_value(cache_key, results, expires_in_sec=CACHE_TTL_SECONDS)
		return results


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def search(query: str | None = None, language: str | None = None, limit: int = 5):
	"""Return a small, cached list of Nominatim-compatible place results."""
	query = _clean_query(query)
	language = _clean_language(language)
	limit = max(1, min(cint(limit) or 5, 5))
	cache_key = _cache_key(query, language, limit)

	results = frappe.cache.get_value(cache_key, expires=True)
	if results is None:
		try:
			results = _fetch_with_upstream_throttle(query, language, limit, cache_key)
		except Exception:
			# Do not include the query in logs or error output: place searches may
			# contain private addresses.
			frappe.throw(
				_("Place search is temporarily unavailable. Please try again."),
				frappe.ValidationError,
			)

	return {
		"results": results,
		"provider": "OpenStreetMap Nominatim",
		"attribution": "Search data © OpenStreetMap contributors",
	}
