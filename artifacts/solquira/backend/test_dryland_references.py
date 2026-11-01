"""Reference-table contracts: no network, fabricated measurements or proximity inference."""
from dataclasses import FrozenInstanceError
import math
import unittest
from unittest.mock import patch

from dryland_brief import fallback_brief
from dryland_references import (
    REFERENCE_SITES, get_reference, list_reference_sites, reference_for_coordinates,
    reference_metadata, reference_payload, resolve_reference_payload,
)


class ReferenceTableTests(unittest.TestCase):
    def setUp(self):
        self.no_network = patch("socket.socket.connect", side_effect=AssertionError("No network in reference table"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def test_reference_places_have_distinct_server_bands(self):
        expected = {
            "ouagadougou": ("Weak", "Fair"),
            "andalusia": ("Building", "Fair"),
            "east_african_plateau": ("Strong", "Good"),
        }
        self.assertEqual(set(REFERENCE_SITES), set(expected))
        for place, bands in expected.items():
            with self.subTest(place=place):
                payload = reference_payload(place)
                self.assertEqual((payload.soc_band, payload.awc_band), bands)
                brief = fallback_brief(payload)
                self.assertEqual((brief.vital_signs.soil_life, brief.vital_signs.water_held), bands)
                self.assertTrue({path.id for path in brief.pathways}.issubset(payload.fao_crops))

    def test_existing_map_presets_are_exactly_reused(self):
        expected = {"ouagadougou": (12.371, -1.519), "andalusia": (37.1, -4.6), "east_african_plateau": (-0.5, 36.9)}
        for place, coordinates in expected.items():
            self.assertEqual(reference_for_coordinates(*coordinates), place)
            payload = reference_payload(place)
            self.assertEqual((payload.lat, payload.lon), coordinates)

    def test_no_nearest_point_assignment(self):
        self.assertIsNone(reference_for_coordinates(12.372, -1.519))
        self.assertIsNone(reference_for_coordinates(0.0, 0.0))
        for coordinates in ((math.nan, 0), (0, math.inf), (91, 0), (0, 181), (True, 0), ("12.371", -1.519)):
            with self.subTest(coordinates=coordinates), self.assertRaises(ValueError):
                reference_for_coordinates(*coordinates)

    def test_metadata_labels_all_inputs_as_synthetic_reference_table(self):
        records = list_reference_sites()
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertEqual(record["soil_source"], "reference-table")
            self.assertIs(record["synthetic"], True)
            self.assertIsNone(record["observed_at"])
            assumptions = record["assumptions"]
            self.assertAlmostEqual(assumptions["awc_mm"],
                                   (assumptions["field_capacity_fraction"] - assumptions["wilting_point_fraction"])
                                   * assumptions["depth_mm"])
            self.assertIn("Demonstration", record["pathway_source"])

    def test_weather_switch_does_not_rewrite_soil_assumptions(self):
        for place in REFERENCE_SITES:
            dry, rain = reference_payload(place, "dry"), reference_payload(place, "rain")
            self.assertEqual((dry.soc_band, dry.awc_band), (rain.soc_band, rain.awc_band))
            self.assertEqual((dry.dry_days_ahead, rain.dry_days_ahead), (6, 1))
            self.assertNotEqual(dry.rain_phrase, rain.rain_phrase)

    def test_all_eight_canonical_payloads_round_trip(self):
        for place in REFERENCE_SITES:
            for scenario in ("dry", "rain"):
                original = reference_payload(place, scenario)
                found_place, found_scenario, canonical = resolve_reference_payload(original)
                self.assertEqual((found_place, found_scenario), (place, scenario))
                self.assertEqual(original.model_dump(), canonical.model_dump())
                self.assertIsNot(original, canonical)

    def test_forged_payload_does_not_resolve(self):
        original = reference_payload()
        for field, value in (("soc_band", "Strong"), ("awc_band", "Good"),
                             ("lat", 12.372), ("dry_days_ahead", 5),
                             ("rain_phrase", "Rain expected throughout the week"),
                             ("fao_crops", ["quinoa", "opuntia", "amaranth"])):
            value_dict = original.model_dump()
            value_dict[field] = value
            forged = type(original).model_validate(value_dict)
            with self.subTest(field=field), self.assertRaises(ValueError):
                resolve_reference_payload(forged)

    def test_catalog_and_returned_values_cannot_mutate_each_other(self):
        with self.assertRaises(TypeError):
            REFERENCE_SITES["new"] = get_reference()
        with self.assertRaises(FrozenInstanceError):
            get_reference().soc_g_per_kg = 999
        first = reference_metadata()
        first["assumptions"]["soc_g_per_kg"] = 999
        self.assertEqual(reference_metadata()["assumptions"]["soc_g_per_kg"], 6.0)
        payload = reference_payload()
        payload.fao_crops.clear()
        self.assertEqual(len(reference_payload().fao_crops), 3)

    def test_unknown_selections_fail_closed(self):
        for place in ("", "invalid/../ouagadougou", "other", None, []):
            with self.subTest(place=place), self.assertRaises(ValueError):
                reference_payload(place)
        with self.assertRaises(ValueError):
            reference_payload(scenario="forecast")
        with self.assertRaises(ValueError):
            resolve_reference_payload({})


if __name__ == "__main__":
    unittest.main()
