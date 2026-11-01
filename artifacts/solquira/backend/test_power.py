import copy
import json
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock
import main
import power


class PowerTests(unittest.TestCase):
    def setUp(self):
        power._CACHE.clear()
        self.raw = json.loads(
            Path(__file__).with_name("power_sample.json").read_text(encoding="utf-8")
        )

    def query(self):
        return power.fetch_nasa_power(
            -30.031, -70.708, date(2025, 1, 1), date(2025, 1, 7)
        )

    def mocked(self, data):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(data).encode()
        response.__enter__.return_value = response
        return patch.object(power.urllib.request, "urlopen", return_value=response)

    def test_valid_response_and_cache(self):
        with self.mocked(self.raw) as transport:
            a = self.query()
            b = self.query()
            self.assertEqual(a["status"], "ok")
            self.assertEqual(a["units"]["T2M"], "C")
            self.assertEqual(len(a["values"]["T2M"]), 7)
            self.assertTrue(b["cacheHit"])
            self.assertEqual(a["fetchedAt"], b["fetchedAt"])
            self.assertEqual(transport.call_count, 1)
            b["values"]["T2M"].clear()
            self.assertEqual(len(self.query()["values"]["T2M"]), 7)

    def test_fill_value_is_not_a_measurement(self):
        self.raw["properties"]["parameter"]["T2M"]["20250101"] = -999
        with self.mocked(self.raw):
            response = self.query()
            self.assertIsNone(response["values"]["T2M"]["20250101"])
            self.assertEqual(response["missingValueCount"], 1)

    def test_wrong_unit_period_location_and_incomplete_series(self):
        for case in ["unit", "period", "location", "coverage", "nan", "negative_rain"]:
            power._CACHE.clear()
            raw = copy.deepcopy(self.raw)
            if case == "unit":
                raw["parameters"]["T2M"]["units"] = "F"
            if case == "period":
                raw["header"]["start"] = "20240101"
            if case == "location":
                raw["geometry"]["coordinates"][0] = 0
            if case == "coverage":
                raw["properties"]["parameter"]["T2M"].pop("20250101")
            if case == "nan":
                raw["properties"]["parameter"]["T2M"]["20250101"] = float("nan")
            if case == "negative_rain":
                raw["properties"]["parameter"]["PRECTOTCORR"]["20250101"] = -1
            with self.subTest(case=case), self.mocked(raw):
                self.assertEqual(self.query()["status"], "invalid_response")

    def test_network_failure_never_becomes_synthetic_success(self):
        with patch.object(
            power.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ) as transport:
            for _ in range(2):
                response = self.query()
                self.assertEqual(response["status"], "unavailable")
                self.assertNotIn("values", response)
            self.assertEqual(transport.call_count, 2)

    def test_exactly_31_days_allowed_but_32_rejected(self):
        with patch.object(power.urllib.request, "urlopen", side_effect=TimeoutError):
            self.assertEqual(
                power.fetch_nasa_power(
                    -30.031, -70.708, date(2025, 1, 1), date(2025, 1, 31)
                )["status"],
                "unavailable",
            )
            with self.assertRaises(main.HTTPException):
                power.fetch_nasa_power(
                    -30.031, -70.708, date(2025, 1, 1), date(2025, 2, 1)
                )

    def test_cache_expiry(self):
        with self.mocked(self.raw) as transport:
            self.query()
            for key, (_, result) in list(power._CACHE.items()):
                power._CACHE[key] = (0, result)
            self.query()
            self.assertEqual(transport.call_count, 2)


class HardConstraintTests(unittest.TestCase):
    def evaluate(self, **updates):
        p = main.default_parcel().model_copy(update=updates)
        return main.evaluate(main.EvaluationInput(parcel=p, waterPercent=100))

    def test_immediate_repeat_is_excluded_even_with_high_water(self):
        result = self.evaluate(waterM3Ha=10000, previousCrops=["cubierta"])
        blocked = next(r for r in result["rotations"] if r["id"] == "rotation-a")
        self.assertFalse(blocked["eligibleUnderDemoRules"])
        self.assertFalse(blocked["rotationAllowed"])
        self.assertNotEqual(result["scenarioLeaderId"], "rotation-a")

    def test_no_eligible_leader_at_zero_water(self):
        result = self.evaluate(waterM3Ha=0)
        self.assertIsNone(result["scenarioLeaderId"])
        self.assertEqual(result["demoRuleMatches"], 0)
        self.assertTrue(
            all(not r["eligibleUnderDemoRules"] for r in result["rotations"])
        )

    def test_missing_priorities_or_unknown_crop_abstains(self):
        for updates in [{"priorities": []}, {"previousCrops": ["trigo"]}]:
            result = self.evaluate(**updates)
            self.assertEqual(result["status"], "insufficient_data")
            self.assertEqual(result["rotations"], [])

    def test_deterministic_and_no_agronomic_viability_claim(self):
        self.assertEqual(self.evaluate(), self.evaluate())
        for r in self.evaluate()["rotations"]:
            self.assertEqual(r["agronomicViability"], "not_assessed")
            self.assertAlmostEqual(sum(r["scoreWeights"].values()), 1, places=5)
