"""Every command the public README documents must exist in the package."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "docs/release/GITHUB-README.md"


def published_files():
    spec = importlib.util.spec_from_file_location(
        "stager_for_readme", ROOT / "scripts/stage-github-release.py"
    )
    stager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stager)
    return set(stager.sources(ROOT))


class ReadmeContractTests(unittest.TestCase):
    def test_every_documented_script_is_published_and_present(self):
        text = README.read_text(encoding="utf-8")
        referenced = set(re.findall(r"(scripts/[A-Za-z0-9_.-]+\.(?:py|mjs))", text))
        self.assertTrue(referenced, "the README documents no script at all")
        published = published_files()
        for relative in sorted(referenced):
            with self.subTest(command=relative):
                self.assertTrue((ROOT / relative).is_file(), f"{relative} does not exist")
                self.assertIn(relative, published, f"{relative} exists but is not shipped")


if __name__ == "__main__":
    unittest.main()
