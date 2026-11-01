import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('github_boundary', Path(__file__).with_name('stage-github-release.py'))
package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)


class GithubBoundaryTests(unittest.TestCase):
    def test_inventory_uses_selected_root_and_excludes_private_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('lib/sample/src/clean.ts', 'lib/sample/src/credentials.json',
                         'lib/sample/src/.hidden/value.json', 'lib/sample/src/private_config.py'):
                p = root / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('DECOY', encoding='utf8')
            selected = package.sources(root)
            self.assertIn('lib/sample/src/clean.ts', selected)
            self.assertNotIn('lib/sample/src/credentials.json', selected)
            self.assertNotIn('lib/sample/src/.hidden/value.json', selected)
            self.assertNotIn('lib/sample/src/private_config.py', selected)
            self.assertIn('artifacts/solquira/backend/dryland_progressive.py', selected)

    def test_public_readme_is_selected_without_internal_docs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = package.sources(root)
            self.assertIn("README.md", selected)
            self.assertNotIn("docs/GLOBAL-ECOSYSTEM-ORCHESTRATION.md", selected)
            self.assertNotIn("docs/brand/SOLQUIRA.md", selected)
        source = Path(package.__file__).read_text(encoding="utf8")
        self.assertNotIn("docs/release/GITHUB-README.md", source)


if __name__ == '__main__': unittest.main()
