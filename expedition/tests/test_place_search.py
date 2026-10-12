"""Focused tests for Expedition's provider-backed place-search adapter."""

import unittest
from unittest.mock import Mock, patch

from expedition.api import place


class TestPlaceSearch(unittest.TestCase):
	def test_normalise_result(self):
		result = place._normalise_result(
			{
				"osm_type": "way",
				"osm_id": 123,
				"name": "Example Hotel",
				"display_name": "Example Hotel, Hanoi, Vietnam",
				"category": "tourism",
				"type": "hotel",
				"lat": "21.0285",
				"lon": "105.8542",
				"boundingbox": ["21.0280", "21.0290", "105.8530", "105.8550"],
			}
		)

		self.assertEqual(result["id"], "way:123")
		self.assertEqual(result["name"], "Example Hotel")
		self.assertEqual(result["type"], "hotel")
		self.assertEqual(result["latitude"], 21.0285)
		self.assertEqual(
			result["bounds"],
			{"south": 21.028, "north": 21.029, "west": 105.853, "east": 105.855},
		)

	def test_normalise_result_rejects_invalid_coordinates(self):
		self.assertIsNone(
			place._normalise_result(
				{"display_name": "Invalid", "lat": "91", "lon": "105"}
			)
		)

	def test_normalise_photon_result(self):
		result = place._normalise_photon_result(
			{
				"properties": {
					"osm_type": "N",
					"osm_id": 4171787601,
					"osm_key": "tourism",
					"osm_value": "museum",
					"name": "Địa Đạo Bến Dược",
					"street": "Tỉnh lộ 15",
					"city": "Thành phố Hồ Chí Minh",
					"country": "Việt Nam",
				},
				"geometry": {"type": "Point", "coordinates": [106.4632107, 11.1432528]},
			}
		)

		self.assertEqual(result["id"], "n:4171787601")
		self.assertEqual(result["name"], "Địa Đạo Bến Dược")
		self.assertEqual(result["type"], "museum")
		self.assertEqual(result["latitude"], 11.1432528)
		self.assertEqual(result["longitude"], 106.4632107)

	@patch("expedition.api.place.get_request_session")
	def test_request_places_uses_nominatim_search_endpoint(self, get_session):
		response = Mock()
		response.json.return_value = [
			{
				"place_id": 7,
				"display_name": "Example Hotel, Hanoi, Vietnam",
				"lat": "21.0285",
				"lon": "105.8542",
			}
		]
		get_session.return_value.get.return_value = response

		with patch("expedition.api.place._search_url", return_value=place.DEFAULT_NOMINATIM_SEARCH_URL), patch(
			"expedition.api.place._user_agent", return_value=place.DEFAULT_USER_AGENT
		):
			results = place._request_places("Example Hotel", "en-US", 5, "nominatim")

		self.assertEqual(len(results), 1)
		response.raise_for_status.assert_called_once_with()
		_, kwargs = get_session.return_value.get.call_args
		self.assertEqual(kwargs["params"]["q"], "Example Hotel")
		self.assertEqual(kwargs["params"]["format"], "jsonv2")
		self.assertEqual(kwargs["headers"]["Accept-Language"], "en-US")
		self.assertEqual(kwargs["timeout"], 8)

	@patch("expedition.api.place.get_request_session")
	def test_request_places_uses_photon_search_endpoint(self, get_session):
		response = Mock()
		response.json.return_value = {
			"type": "FeatureCollection",
			"features": [
				{
					"properties": {"name": "Example Hotel", "city": "Hanoi"},
					"geometry": {"type": "Point", "coordinates": [105.8542, 21.0285]},
				}
			],
		}
		get_session.return_value.get.return_value = response

		with patch("expedition.api.place._search_url", return_value=place.DEFAULT_PHOTON_SEARCH_URL), patch(
			"expedition.api.place._user_agent", return_value=place.DEFAULT_USER_AGENT
		):
			results = place._request_places("Example Hotel", "en-US", 5, "photon")

		self.assertEqual(len(results), 1)
		response.raise_for_status.assert_called_once_with()
		_, kwargs = get_session.return_value.get.call_args
		self.assertEqual(kwargs["params"], {"q": "Example Hotel", "limit": 5, "lang": "en"})
		self.assertNotIn("format", kwargs["params"])

	def test_cache_key_is_case_insensitive_but_language_specific(self):
		self.assertEqual(
			place._cache_key("Example Hotel", "en-US", 5),
			place._cache_key("example hotel", "EN-us", 5),
		)
		self.assertNotEqual(
			place._cache_key("Example Hotel", "en-US", 5),
			place._cache_key("Example Hotel", "vi-VN", 5),
		)

	def test_search_returns_cached_result_without_upstream_request(self):
		cached = [{"id": "way:123", "name": "Example Hotel"}]
		cache = Mock()
		cache.get_value.return_value = cached

		with patch.object(place.frappe, "cache", cache), patch(
			"expedition.api.place._provider_name", return_value="photon"
		), patch(
			"expedition.api.place._fetch_with_upstream_throttle"
		) as fetch:
			result = place.search.__wrapped__("Example Hotel", "en-US", 5)

		self.assertEqual(result["results"], cached)
		fetch.assert_not_called()


if __name__ == "__main__":
	unittest.main()
