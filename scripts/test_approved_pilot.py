"""Public release boundary after removal of the private approved-pilot runner."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), ROOT / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class PublicPilotBoundaryTests(unittest.TestCase):
    def test_private_pilot_is_not_restored(self):
        self.assertFalse((ROOT / 'scripts/run-studio-approved-pilot.py').exists())

    def test_private_pilot_is_not_published(self):
        self.assertNotIn('scripts/run-studio-approved-pilot.py', load('stage-github-release.py').sources(ROOT))

    def test_default_launcher_does_not_forward_model_credentials(self):
        environment = load('run-studio-local.py').local_environment({'NEBIUS_API_KEY':'synthetic-decoy','SOLQUIRA_STUDIO_PROVIDER':'nebius'})
        self.assertNotIn('NEBIUS_API_KEY', environment)
        self.assertNotIn('SOLQUIRA_STUDIO_PROVIDER', environment)
        self.assertEqual(environment['SOLQUIRA_LIVE_ADAPTERS'], '0')

if __name__ == '__main__':
    unittest.main()
