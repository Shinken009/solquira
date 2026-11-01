"""Full-stack packaging boundaries; no Docker, network or credentials."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("hosted_package", Path(__file__).with_name("stage-hosted-release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class HostedPackageTests(unittest.TestCase):
    def fixture(self, root):
        sources = release.package.source_files(root) + [root / "artifacts/solquira/backend/studio_budget.py",
                    root / "scripts/run-studio-hosted.py", root / "deploy/studio/hosted.Dockerfile"]
        for source in sources:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("REVIEWED_FIXTURE", encoding="utf-8")
        front = root / "artifacts/solquira/dist/public"
        (front / "assets").mkdir(parents=True)
        (front / "index.html").write_text("<html>TEST</html>", encoding="utf-8")
        (front / "assets/app.js").write_text("console.log('TEST')", encoding="utf-8")

    def test_full_stack_is_explicit_no_private_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / "scripts/.env").write_text("PRIVATE_DECOY", encoding="utf-8")
            (root / "artifacts/solquira/backend/credentials.py").write_text("PRIVATE_DECOY", encoding="utf-8")
            _, context, manifest = release.stage(root)
            self.assertIn("artifacts/solquira/dist/public/index.html", manifest["files"])
            self.assertIn("scripts/run-studio-hosted.py", manifest["files"])
            self.assertIn("artifacts/solquira/backend/studio_budget.py", manifest["files"])
            self.assertFalse((context / "scripts/.env").exists())
            self.assertFalse(any("credentials" in name for name in manifest["files"]))
            self.assertFalse(manifest["deployed"])

    def test_unexpected_frontend_file_fails_before_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / "artifacts/solquira/dist/public/private.py").write_text("DECOY", encoding="utf-8")
            with self.assertRaises(ValueError):
                release.stage(root)
            self.assertFalse((root / "output").exists())

    def test_existing_landscape_asset_is_explicitly_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            front = root / "artifacts/solquira/dist/public"
            (front / "solquira-rural-landscape.svg").write_text("<svg></svg>", encoding="utf-8")
            _, _, manifest = release.stage(root)
            self.assertIn("artifacts/solquira/dist/public/solquira-rural-landscape.svg", manifest["files"])
            self.assertFalse(release.assets.allowed(Path("unreviewed.svg")))

    def test_output_outside_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            with self.assertRaises(ValueError):
                release.stage(root, root / "outside-output")

    def test_missing_launcher_fails_before_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            (root / "scripts/run-studio-hosted.py").unlink()
            with self.assertRaises((ValueError, FileNotFoundError)):
                release.stage(root)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
