# Copyright (c) 2026, OneHash and contributors
# For license information, please see license.txt

"""Provider-backed place search for the Expedition canvas.

Place providers are called from the server instead of the browser. This gives
us one cache and one upstream throttle for the whole site, keeps the provider
replaceable, and prevents every browser client from contacting a provider
directly.
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


DEFAULT_PROVIDER = "photon"
DEFAULT_NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_PHOTON_SEARCH_URL = "https://photon.komoot.io/api/"
DEFAULT_USER_AGENT = "Expedition-Frappe/0.1 (engineering@onehash.ai)"
CACHE_TTL_SECONDS = 24 * 60 * 60
UPSTREAM_INTERVAL_SECONDS = 1.0

PROVIDER_DETAILS = {
	"nominatim": {
		"label": "OpenStreetMap Nominatim",
		"attribution": "Search data © OpenStreetMap contributors",
	},
	"photon": {
		"label": "Photon / OpenStreetMap",
		"attribution": "Search data © OpenStreetMap contributors",
	},
}


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


def _cache_key(query: str, language: str, limit: int, provider: str = DEFAULT_PROVIDER) -> str:
	raw = f"{provider}\n{query.casefold()}\n{language.casefold()}\n{limit}"
	digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
	return f"expedition:place-search:v2:{digest}"


def _finite_float(value: Any) -> float | None:
	try:
		value = float(value)
	except (TypeError, ValueError):
		return None
	return value if math.isfinite(value) else None


def _normalise_nominatim_result(row: dict[str, Any]) -> dict[str, Any] | None:
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


def _normalise_result(row: dict[str, Any]) -> dict[str, Any] | None:
	"""Backward-compatible alias for the Nominatim result normalizer."""
	return _normalise_nominatim_result(row)


def _normalise_photon_result(feature: dict[str, Any]) -> dict[str, Any] | None:
	geometry = feature.get("geometry")
	properties = feature.get("properties")
	if not isinstance(geometry, dict) or not isinstance(properties, dict):
		return None

	coordinates = geometry.get("coordinates")
	if not isinstance(coordinates, (list, tuple)) or len(coordinates) < 2:
		return None
	lng = _finite_float(coordinates[0])
	lat = _finite_float(coordinates[1])
	if lat is None or lng is None or not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
		return None

	name = str(properties.get("name") or "").strip()
	street = " ".join(
		part
		for part in (
			str(properties.get("housenumber") or "").strip(),
			str(properties.get("street") or "").strip(),
		)
		if part
	)
	parts = [
		name,
		street,
		str(properties.get("locality") or "").strip(),
		str(properties.get("district") or "").strip(),
		str(properties.get("city") or "").strip(),
		str(properties.get("state") or "").strip(),
		str(properties.get("country") or "").strip(),
	]
	# Photon may return the same locality in more than one address field.
	unique_parts = list(dict.fromkeys(part for part in parts if part))
	display_name = ", ".join(unique_parts)
	if not display_name:
		return None

	bounds = None
	raw_bounds = feature.get("bbox")
	if isinstance(raw_bounds, (list, tuple)) and len(raw_bounds) == 4:
		west, south, east, north = (_finite_float(value) for value in raw_bounds)
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

	osm_type = str(properties.get("osm_type") or "").strip().lower()
	osm_id = str(properties.get("osm_id") or "").strip()
	return {
		"id": f"{osm_type}:{osm_id}" if osm_type or osm_id else f"{lat}:{lng}",
		"name": name or display_name.split(",", 1)[0],
		"display_name": display_name,
		"category": str(properties.get("osm_key") or "").strip(),
		"type": str(properties.get("osm_value") or properties.get("type") or "").strip(),
		"latitude": lat,
		"longitude": lng,
		"bounds": bounds,
	}


def _provider_name() -> str:
	"""Return the configured provider, allowing site_config to override the UI setting."""
	provider = frappe.conf.get("expedition_place_search_provider")
	if not provider:
		try:
			provider = frappe.db.get_single_value("Expedition Settings", "place_search_provider")
		except Exception:
			# A site upgrade may load this code before the new setting is synced.
			provider = None

	provider = str(provider or DEFAULT_PROVIDER).strip().lower()
	return provider if provider in PROVIDER_DETAILS else DEFAULT_PROVIDER


def _search_url(provider: str) -> str:
	if provider == "nominatim":
		return str(
			frappe.conf.get("expedition_nominatim_url") or DEFAULT_NOMINATIM_SEARCH_URL
		).strip()
	return str(frappe.conf.get("expedition_photon_url") or DEFAULT_PHOTON_SEARCH_URL).strip()


def _user_agent() -> str:
	return str(
		frappe.conf.get("expedition_geocoder_user_agent") or DEFAULT_USER_AGENT
	).strip()


def _photon_language(language: str) -> str:
	return language.split(",", 1)[0].split("-", 1)[0].strip().lower()


def _request_places(query: str, language: str, limit: int, provider: str) -> list[dict[str, Any]]:
	headers = {
		"Accept": "application/json",
		"User-Agent": _user_agent(),
	}
	if language:
		headers["Accept-Language"] = language
	params: dict[str, Any] = {"q": query, "limit": limit}
	if provider == "nominatim":
		params.update({"format": "jsonv2", "addressdetails": 1})
	else:
		photon_language = _photon_language(language)
		if photon_language:
			params["lang"] = photon_language

	response = get_request_session().get(
		_search_url(provider),
		params=params,
		headers=headers,
		timeout=8,
	)
	response.raise_for_status()
	payload = response.json()
	rows: list[Any]
	normalise = _normalise_nominatim_result
	if provider == "photon":
		rows = payload.get("features", []) if isinstance(payload, dict) else []
		normalise = _normalise_photon_result
	else:
		rows = payload if isinstance(payload, list) else []

	results = []
	for row in rows:
		if not isinstance(row, dict):
			continue
		result = normalise(row)
		if result:
			results.append(result)
	return results


def _fetch_with_upstream_throttle(
	query: str, language: str, limit: int, provider: str, cache_key: str
) -> list[dict[str, Any]]:
	# Redis' lock is shared by all web workers. Recheck the response cache after
	# acquiring it because another request may have populated the same query
	# while this request was waiting.
	lock_name = frappe.cache.make_key(f"expedition:place-search:{provider}:lock")
	with frappe.cache.lock(lock_name, timeout=10, blocking_timeout=5):
		cached = frappe.cache.get_value(cache_key, expires=True)
		if cached is not None:
			return cached

		last_request_key = f"expedition:place-search:{provider}:last-request"
		last_request = frappe.cache.get_value(last_request_key, expires=True)
		if last_request:
			remaining = UPSTREAM_INTERVAL_SECONDS - (time.time() - float(last_request))
			if remaining > 0:
				time.sleep(min(remaining, UPSTREAM_INTERVAL_SECONDS))

		# Store the timestamp immediately before the request. This keeps starts of
		# upstream calls at least one second apart across the site.
		frappe.cache.set_value(
			last_request_key,
			time.time(),
			expires_in_sec=5,
		)
		results = _request_places(query, language, limit, provider)
		frappe.cache.set_value(cache_key, results, expires_in_sec=CACHE_TTL_SECONDS)
		return results


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def search(query: str | None = None, language: str | None = None, limit: int = 5):
	"""Return a small, cached list of normalized place results."""
	query = _clean_query(query)
	language = _clean_language(language)
	limit = max(1, min(cint(limit) or 5, 5))
	provider = _provider_name()
	cache_key = _cache_key(query, language, limit, provider)

	results = frappe.cache.get_value(cache_key, expires=True)
	if results is None:
		try:
			results = _fetch_with_upstream_throttle(query, language, limit, provider, cache_key)
		except Exception:
			# Do not include the query in logs or error output: place searches may
			# contain private addresses.
			frappe.throw(
				_("Place search is temporarily unavailable. Please try again."),
				frappe.ValidationError,
			)

	return {
		"results": results,
		"provider": PROVIDER_DETAILS[provider]["label"],
		"attribution": PROVIDER_DETAILS[provider]["attribution"],
	}
