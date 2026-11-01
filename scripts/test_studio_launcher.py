"""Pure checks: no credentials are read and no server is launched."""
import importlib.util
import unittest
from pathlib import Path
spec = importlib.util.spec_from_file_location("solquira_local_launcher", Path(__file__).with_name("run-studio-local.py"))
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LocalLauncherTests(unittest.TestCase):
    def test_provider_and_runtime_credentials_not_forwarded(self):
        source = {"PATH": "runtime", "NEBIUS_API_KEY": "test-marker", "TELEGRAM_BOT_TOKEN": "test-marker", "SOLQUIRA_API_KEY": "test-marker", "RASA_LICENSE": "test-marker", "SOLQUIRA_LIVE_ADAPTERS": "1", "SOLQUIRA_STUDIO_ALLOWED_ORIGINS": "https://untrusted.invalid"}
        result = launcher.local_environment(source)
        for name in ("NEBIUS_API_KEY", "TELEGRAM_BOT_TOKEN", "SOLQUIRA_API_KEY", "RASA_LICENSE", "SOLQUIRA_STUDIO_ALLOWED_ORIGINS"):
            self.assertNotIn(name, result)
        self.assertEqual(result["SOLQUIRA_LIVE_ADAPTERS"], "0")
        self.assertEqual(result["SOLQUIRA_STUDIO_MODE"], "1")
        self.assertEqual(result["PATH"], "runtime")
        self.assertEqual(source["SOLQUIRA_LIVE_ADAPTERS"], "1")

    def test_local_adapters_fail_closed_without_env(self):
        result = launcher.local_environment({})
        self.assertEqual(result["SOLQUIRA_MCP_NASA_POWER"], "0")
        self.assertEqual(result["SOLQUIRA_ENABLE_QA_PREVIEW"], "0")
        self.assertEqual(result["PYTHONUTF8"], "1")


if __name__ == "__main__":
    unittest.main()
