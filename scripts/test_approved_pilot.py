import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("approved_pilot", Path(__file__).with_name("run-studio-approved-pilot.py"))
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


class ApprovedPilotTests(unittest.TestCase):
    def test_approved_policy_is_fixed_and_conservative(self):
        policy = pilot.policy()
        self.assertEqual(policy.lifetime_micro_usd, 5000000)
        self.assertEqual(policy.daily_micro_usd, 1000000)
        self.assertEqual(policy.lifetime_calls * policy.reservation_micro_usd, 4200000)

    def test_initialize_and_check_do_not_load_secrets_or_start_provider(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pilot, "DATA", Path(directory)), patch.object(pilot, "helper") as helper, patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(pilot.main(["--initialize-budget"]), 0)
            self.assertEqual(pilot.main(["--check"]), 0)
            helper.assert_not_called()

    def test_check_does_not_create_missing_ledger(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(pilot, "DATA", Path(directory)):
            with self.assertRaises(ValueError):
                pilot.main(["--check"])
            self.assertFalse((Path(directory) / "studio-budget.sqlite3").exists())

    def test_receipts_only_counts_status_and_timing(self):
        class Provider:
            model, mode = "synthetic-model", "provider_stub"
            last_usage, last_http_status = {"prompt_tokens": 12}, 200
            def complete(self, *args, **kwargs):
                return {"content": "PRIVATE_REPLY_DECOY"}
        provider = pilot.AuditedProvider(Provider())
        provider.complete([{"content": "PRIVATE_PROMPT_DECOY"}], [])
        serialized = json.dumps(provider.receipts)
        self.assertNotIn("PRIVATE_", serialized)
        self.assertIn('"prompt_tokens": 12', serialized)


if __name__ == "__main__":
    unittest.main()
