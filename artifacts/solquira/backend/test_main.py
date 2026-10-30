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
            latitude=-30.03,
            longitude=-70.7,
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
