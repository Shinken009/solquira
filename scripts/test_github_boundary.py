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

    def test_readme_path_has_same_release_guard(self):
        source = Path(package.__file__).read_text(encoding='utf8')
        guarded = 'assets.regular_path(root / "docs/release/GITHUB-README.md", root)'
        read = 'contents["README.md"] = (root / "docs/release/GITHUB-README.md").read_bytes()'
        self.assertIn(guarded, source); self.assertLess(source.index(guarded), source.index(read))


if __name__ == '__main__': unittest.main()
