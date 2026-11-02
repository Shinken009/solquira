"""Synthetic metadata fixtures only. No source measurements or network access."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from land_evidence import SLOTS, evidence_inventory, footprint_contains, metadata_state, query_point

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
POINT = {"latitude": 52.520008, "longitude": 13.404954}


def forecast_fixture(now=NOW):
    return {"context_ref": "slq-context-" + "A" * 32, "location": query_point(POINT),
            "retrieved_at": now.isoformat(), "weather": {"source": "Open-Meteo", "kind": "model_forecast",
            "time_zone": "UTC", "state": "ready", "days": [
                {"date": (now.date() + timedelta(days=index)).isoformat()} for index in range(7)]}}


class LandEvidenceTests(unittest.TestCase):
    def test_missing_inventory_is_honest_and_preserves_raw_point(self):
        with patch("socket.socket.connect", side_effect=AssertionError("No network")):
            value = evidence_inventory(POINT, now=NOW)
        self.assertEqual(value["location"], POINT)
        self.assertEqual(value["status"], "inventory_only")
        self.assertEqual(value["scientific_validation"], "not_validated")
        self.assertFalse(value["is_medalus_r"])
        self.assertEqual([item["id"] for item in value["items"]], [slot[0] for slot in SLOTS])
        for item in value["items"]:
            with self.subTest(slot=item["id"]):
                self.assertEqual(item["state"], "missing")
                self.assertEqual(item["quality"], "not_assessed")
                for key in ("source", "period", "retrieved_at", "spatial_support", "units", "coverage"):
                    self.assertIsNone(item[key])
                self.assertTrue(item["reason"] and item["uncertainty"] and item["next_observation"])
        for forbidden in ("score", "sdi", "risk_class", "ranking", "irrigation_mm", "value"):
            self.assertNotIn('"' + forbidden + '":', json.dumps(value))

    def test_current_bound_forecast_populates_only_context_without_resolution_claim(self):
        context = forecast_fixture()
        original = copy.deepcopy(context)
        value = evidence_inventory(POINT, context, now=NOW)
        self.assertEqual(context, original)
        item = value["items"][0]
        self.assertEqual(item["state"], "available")
        self.assertIsNone(item["coverage"])
        self.assertIsNone(item["source"]["version"])
        self.assertIsNone(item["spatial_support"]["resolution_m"])
        self.assertEqual(item["spatial_support"]["query_precision_degrees"], .001)
        self.assertEqual(item["quality"], "metadata_checked")
        self.assertNotIn("context_ref", json.dumps(value))
        self.assertEqual(value["items"][1]["state"], "missing")
        self.assertTrue(all(row["state"] == "missing" for row in value["items"][1:]))

    def test_cached_forecast_has_original_retrieval_time(self):
        context = forecast_fixture(NOW - timedelta(minutes=5))
        context["weather"]["state"] = "cached"
        item = evidence_inventory(POINT, context, now=NOW)["items"][0]
        self.assertEqual(item["state"], "available")
        self.assertEqual(item["retrieved_at"], context["retrieved_at"])

    def test_stale_ten_minutes_and_expired_source_period_do_not_become_current(self):
        for age, period_age in ((600, 0), (601, 0), (0, 3)):
            with self.subTest(age=age, period_age=period_age):
                context = forecast_fixture(NOW - timedelta(seconds=age))
                if period_age:
                    for index, day in enumerate(context["weather"]["days"]):
                        day["date"] = (NOW.date() - timedelta(days=period_age) + timedelta(days=index)).isoformat()
                self.assertEqual(evidence_inventory(POINT, context, now=NOW)["items"][0]["state"], "stale")

    def test_unavailable_or_invalid_metadata_is_explicit_not_filled(self):
        cases = []
        for key, bad in (("source", "Synthetic table"), ("kind", "reanalysis"), ("time_zone", "local"), ("days", [])):
            value = forecast_fixture(); value["weather"][key] = bad; cases.append(value)
        value = forecast_fixture(); value["retrieved_at"] = "2026-11-01T12:00:00"; cases.append(value)
        value = forecast_fixture(); value["weather"]["state"] = "unavailable"; cases.append(value)
        value = forecast_fixture(); value["location"] = {"latitude": 1, "longitude": 2}; cases.append(value)
        value = forecast_fixture(); value["weather"]["days"][2]["date"] = "2026-11-01"; cases.append(value)
        for value in cases:
            with self.subTest(value=value):
                item = evidence_inventory(POINT, value, now=NOW)["items"][0]
                self.assertEqual(item["state"], "unavailable")
                self.assertEqual(item["quality"], "not_assessed")
                self.assertIsNone(item["source"])

    def test_invalid_and_non_finite_points_rejected(self):
        for point in ({}, {"latitude": 0}, {"latitude": 0, "longitude": 0, "x": 1},
                      *({"latitude": value, "longitude": 0} for value in (True, "1", None, float("nan"), float("inf"), 10 ** 1000, -91, 91)),
                      *({"latitude": 0, "longitude": value} for value in (True, "1", float("nan"), float("inf"), 10 ** 1000, -181, 181))):
            with self.subTest(point=point), self.assertRaises(ValueError):
                evidence_inventory(point, now=NOW)

    def test_world_boundaries_and_hemispheres_have_no_regional_fallback(self):
        for latitude, longitude in ((-90, -180), (90, 180), (-33.45, -70.66), (52.52, 13.405), (0, 0), (-20, 175)):
            point = {"latitude": latitude, "longitude": longitude}
            self.assertEqual(evidence_inventory(point, now=NOW)["location"], point)

    def test_query_rounding_matches_browser_and_never_mutates_raw(self):
        for raw, expected in ((1.2345, 1.235), (-1.2345, -1.234), (-1.2355, -1.235), (0, 0)):
            point = {"latitude": raw, "longitude": raw}
            self.assertEqual(query_point(point), {"latitude": expected, "longitude": expected})
            self.assertEqual(point["latitude"], raw)

    def test_footprints_and_antimeridian(self):
        for longitude in (170, 179, 180, -180, -179, -170):
            self.assertTrue(footprint_contains({"latitude": 0, "longitude": longitude}, [170, -10, -170, 10]))
        for longitude in (-169, 0, 169):
            self.assertFalse(footprint_contains({"latitude": 0, "longitude": longitude}, [170, -10, -170, 10]))
        self.assertFalse(footprint_contains({"latitude": 11, "longitude": 180}, [170, -10, -170, 10]))
        self.assertTrue(footprint_contains({"latitude": 0, "longitude": 180}, [-180, -1, -170, 1]))
        self.assertTrue(footprint_contains({"latitude": 90, "longitude": 180}, [-180, -90, 180, 90]))

    def test_malformed_footprints_rejected(self):
        for bbox in ([], [1, 2, 3], [0, 10, 1, -10], [0, -91, 1, 1], [181, 0, 1, 1],
                     [True, 0, 1, 1], [float("nan"), 0, 1, 1]):
            with self.subTest(bbox=bbox), self.assertRaises(ValueError):
                footprint_contains(POINT, bbox)

    def metadata(self, **updates):
        return dict(slot_id="land_cover_change", nature="satellite", retrieved_at=NOW,
                    valid_until=NOW + timedelta(days=1), period_start="2025-01-01", period_end="2025-12-31",
                    now=NOW, **updates)

    def test_metadata_states_for_future_adapters_do_not_diagnose(self):
        self.assertEqual(metadata_state(POINT, **self.metadata()), "available")
        self.assertEqual(metadata_state(POINT, **self.metadata(coverage=[170, -10, -170, 10])), "not_applicable")
        self.assertEqual(metadata_state(POINT, **self.metadata(applicable=False)), "not_applicable")
        self.assertEqual(metadata_state(POINT, **self.metadata(source_available=False)), "unavailable")
        kwargs = self.metadata(); kwargs["now"] = NOW + timedelta(days=1)
        self.assertEqual(metadata_state(POINT, **kwargs), "stale")

    def test_source_applicability_flags_and_nature_are_not_coerced(self):
        for key, value in (("source_available", 1), ("source_available", "yes"),
                           ("applicable", None), ("applicable", "false"), ("nature", "modeled_soil")):
            kwargs = self.metadata(); kwargs[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                metadata_state(POINT, **kwargs)

    def test_forecast_cannot_be_climatology_and_invalid_period_is_rejected(self):
        kwargs = self.metadata(); kwargs.update(slot_id="climate_baseline", nature="forecast")
        with self.assertRaisesRegex(ValueError, "Forecasts"):
            metadata_state(POINT, **kwargs)
        for key, value in (("slot_id", "invented"), ("nature", "invented"), ("period_end", "2020-01-01"),
                           ("retrieved_at", "2026-11-01T12:00:00"), ("valid_until", NOW),
                           ("retrieved_at", NOW + timedelta(minutes=5))):
            kwargs = self.metadata(); kwargs[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                metadata_state(POINT, **kwargs)


if __name__ == "__main__":
    unittest.main()
