"""Source package boundary tests; no Docker, network or credential reads."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("studio_package", Path(__file__).with_name("check-studio-package.py"))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageTests(unittest.TestCase):
    def test_real_runtime_dependencies_are_selected(self):
        names = {p.relative_to(package.ROOT).as_posix() for p in package.source_files(package.ROOT)}
        for name in ("artifacts/solquira/backend/studio_api.py",
                     "artifacts/solquira/backend/studio_brief.py",
                     "artifacts/solquira/backend/studio_geo.py",
                     "artifacts/solquira/src/studio/fixtures.json",
                     "prototypes/solquira-agent/src/solquira_agent/core.py",
                     "prototypes/solquira-agent/IDENTIDAD.md"):
            self.assertIn(name, names)
        self.assertEqual(sum(n.endswith("/SKILL.md") for n in names), 4)
        self.assertFalse(any(Path(n).name.startswith("test_") for n in names))

    def test_secret_data_and_build_outputs_are_not_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("scripts/.env", "artifacts/solquira/backend/.env",
                         "artifacts/solquira/backend/credentials.json",
                         "artifacts/solquira/backend/credentials.py",
                         "prototypes/solquira-agent/src/solquira_agent/private_config.py",
                         "artifacts/solquira/backend/.data/private.sqlite3",
                         "artifacts/solquira/dist/public/private.txt",
                         "prototypes/solquira-agent/src/solquira_agent/__pycache__/secret.pyc",
                         "artifacts/solquira/backend/test_private.py"):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("FAKE_DECOY_NOT_A_SECRET", encoding="utf-8")
            # Mandatory explicit paths may not exist in this fixture, but none
            # of the decoy content may enter the selected package.
            self.assertFalse(any(p.exists() for p in package.source_files(root)))

    def test_resolved_ancestor_escape_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "main.py"
            source.write_text("PUBLIC_TEST_FIXTURE", encoding="utf-8")
            original = Path.resolve
            def resolve(value, *args, **kwargs):
                return root.parent / "outside-fixture.py" if value == source else original(value, *args, **kwargs)
            with patch.object(Path, "resolve", new=resolve), patch.object(package.shutil, "copyfile") as copied:
                with self.assertRaises(ValueError):
                    package.stage_source(source, root, root / "stage")
                copied.assert_not_called()

    def test_individual_symlink_is_rejected_before_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "main.py"
            source.write_text("PUBLIC_TEST_FIXTURE", encoding="utf-8")
            with patch.object(Path, "is_symlink", return_value=True), patch.object(package.shutil, "copyfile") as copied:
                with self.assertRaises(ValueError):
                    package.stage_source(source, root, root / "stage")
                copied.assert_not_called()


if __name__ == "__main__":
    unittest.main()
