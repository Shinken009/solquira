import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.parcel = main.Parcel(
            name="Test",
            profileId="valle",
            areaHa=1,
            waterM3Ha=2400,
            soil="franco",
            previousCrops=["descanso"],
            priorities=["agua", "diversidad"],
            latitude=10.0,
            longitude=20.0,
        )

    def test_less_water_changes_balance_and_ranking(self):
        normal = main.evaluate(
            main.EvaluationInput(parcel=self.parcel, waterPercent=100)
        )
        dry = main.evaluate(main.EvaluationInput(parcel=self.parcel, waterPercent=40))
        self.assertEqual(len(normal["rotations"]), 3)
        self.assertEqual(len(dry["rotations"]), 3)
        self.assertEqual(normal["baselineLeaderId"], dry["baselineLeaderId"])
        self.assertNotEqual(
            normal["rotations"][0]["waterBalanceM3Ha"],
            dry["rotations"][0]["waterBalanceM3Ha"],
        )
        self.assertTrue(
            any(
                "Supera el supuesto hídrico" in row["compatibility"]
                for row in dry["rotations"]
            )
        )

    def test_no_water_option_is_not_misrepresented_as_viable(self):
        dry = main.evaluate(main.EvaluationInput(parcel=self.parcel, waterPercent=40))
        self.assertTrue(dry["waterScenarioWarning"])
        self.assertEqual(dry["waterAssumptionMatches"], 0)
        self.assertIn("Ninguna secuencia", dry["summary"])

    def test_unknown_soil_and_history_return_insufficient(self):
        parcel = self.parcel.model_copy(
            update={"soil": "desconocido", "previousCrops": []}
        )
        result = main.evaluate(main.EvaluationInput(parcel=parcel, waterPercent=100))
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["rotations"], [])
        self.assertTrue(result["missingFields"])

    def test_save_and_read_parcel_in_temporary_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(main, "DB_PATH", Path(temp_dir) / "test.sqlite3"):
                main.save_parcel(self.parcel)
                self.assertEqual(main.get_parcel(), self.parcel)

    def test_no_default_location_and_global_parcel_coordinates(self):
        self.assertIsNone(main.default_parcel().latitude)
        self.assertIsNone(main.default_parcel().longitude)
        for latitude, longitude in ((45.0, -120.0), (-25.0, 135.0), (0.0, 0.0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                parcel = main.Parcel.model_validate(
                    {**self.parcel.model_dump(), "latitude": latitude, "longitude": longitude}
                )
                self.assertEqual(parcel.latitude, latitude)
                self.assertEqual(parcel.longitude, longitude)
        no_location = main.Parcel.model_validate(
            {**self.parcel.model_dump(), "latitude": None, "longitude": None}
        )
        self.assertIsNone(no_location.latitude)
        for invalid in (90.1, -90.1):
            with self.assertRaises(Exception):
                main.Parcel.model_validate({**self.parcel.model_dump(), "latitude": invalid})
        for invalid in (180.1, -180.1):
            with self.assertRaises(Exception):
                main.Parcel.model_validate({**self.parcel.model_dump(), "longitude": invalid})

    def test_invalid_power_range_is_rejected_before_network(self):
        with self.assertRaises(Exception):
            main.fetch_nasa_power(
                -30,
                -70,
                __import__("datetime").date(2025, 1, 8),
                __import__("datetime").date(2025, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
