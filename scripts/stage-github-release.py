"""Create a reviewed, editable source archive. No Git history, credentials or upload.

The full working tree is deliberately NOT the publication unit. Sources are
allowlisted, scanned, hashed and ZIP-round-trip verified before a receipt exists.
"""

from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Internal gates name the forbidden tokens and the internal workflow on purpose;
# they stay in the development tree and never enter a package.
INTERNAL_TOOLING = {
    "scripts/check-public-surface.py",
    "scripts/test_public_surface.py",
    "scripts/run-release-gates.py",
    "scripts/run-studio-approved-pilot.py",
}


def helper(filename):
    spec = importlib.util.spec_from_file_location(
        filename.replace("-", "_"), ROOT / "scripts" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sources(root):
    package = helper("check-studio-package.py")
    selected = {p.relative_to(root).as_posix() for p in package.source_files(root)}
    selected.update(
        [
            ".gitignore",
            "README.md",
            ".vercelignore",
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "tsconfig.json",
            "tsconfig.base.json",
            "deploy/vercel/vercel.template.json",
            "artifacts/solquira/package.json",
            "artifacts/solquira/tsconfig.json",
            "artifacts/solquira/vite.config.ts",
            "artifacts/solquira/index.html",
            "artifacts/solquira/backend/studio_budget.py",
            "artifacts/solquira/backend/requirements.txt",
            "scripts/package.json",
            "scripts/tsconfig.json",
            "scripts/secrets.local.env.example",
        ]
    )
    scripts = (
        "check-fixtures.py",
        "run-studio-local.py",
        "run-studio-approved-pilot.py",
        "studio-live-child.py",
        "run-studio-hosted.py",
        "check-studio-package.py",
        "stage-hosted-release.py",
        "release_scan.py",
        "stage-github-release.py",
        "qa-dryland-brief.mjs",
        "qa-environment-navigation.mjs",
        "qa-territorial-journey.mjs",
        "qa-studio.mjs",
        "qa-solquira-experience.mjs",
        "qa-solquira-record-agent.mjs",
        "qa-studio-recovery.mjs",
        "qa-field-agent.mjs",
        "qa-geospatial.mjs",
        "qa-land-care-record.mjs",
    )
    selected.update("scripts/" + name for name in scripts)
    # Only code/config extensions in reviewed source roots. No recursive root copy.
    roots = (
        "artifacts/solquira/src",
        "artifacts/solquira/public",
        "lib",
        "scripts/src",
        "prototypes/solquira-agent/tests",
        "deploy/studio",
    )
    suffixes = {
        ".ts",
        ".tsx",
        ".js",
        ".css",
        ".json",
        ".yaml",
        ".yml",
        ".svg",
        ".txt",
        ".py",
        ".md",
    }
    for name in roots:
        for path in (root / name).rglob("*"):
            relative = path.relative_to(root)
            if any(
                part.startswith(".")
                or part in {"node_modules", "dist", "__pycache__", ".git"}
                for part in relative.parts
            ):
                continue
            if re.search(
                r"(?:secret|credential|private_config|token_store)", path.name, re.I
            ):
                continue
            if path.is_file() and (
                path.suffix in suffixes or path.name.endswith("Dockerfile")
            ):
                # The non-hosted image lacks TLS, CSP and Studio mode; publish only
                # deploy/studio/hosted.Dockerfile.
                if relative.as_posix() == "deploy/studio/Dockerfile":
                    continue
                selected.add(relative.as_posix())
    selected.update(
        p.relative_to(root).as_posix()
        for p in (root / "artifacts/solquira/backend").glob("test_*.py")
    )
    selected.update(
        p.relative_to(root).as_posix() for p in (root / "scripts").glob("test_*.py")
    )
    return sorted(selected - INTERNAL_TOOLING)


def stage(root=ROOT, output=None):
    root = Path(root).resolve(strict=True)
    output = Path(
        output or root / "output/solquira-github-phase-b-2026-10-28"
    ).resolve()
    if not output.is_relative_to(root / "output") or output.exists():
        raise ValueError(
            "Use a new directory beneath workspace output/. Existing artifacts are preserved."
        )
    assets = helper("release_scan.py")
    contents = {}
    for relative in sources(root):
        path = root / relative
        assets.regular_path(path, root)
        if not path.is_file():
            raise ValueError("Required source missing: " + relative)
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in assets.SECRET_PATTERNS):
            raise ValueError(
                "Review sensitive-looking source before packaging: " + relative
            )
        # Account emails and credential-bearing URLs must not enter public source.
        # Quotes and commas are excluded so the match cannot span adjacent string
        # literals (e.g. a test asserting that "user@host" is rejected).
        if re.search(rb"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@\"\',]+:[^\s/@\"\',]+@", data):
            raise ValueError("Review credential-like URL: " + relative)
        contents[relative] = data
    for name in ("LICENSE", "NOTICE"):
        if (root / name).is_file():
            assets.regular_path(root / name, root)
            contents[name] = (root / name).read_bytes()
    for name, data in contents.items():
        if any(pattern.search(data) for pattern in assets.SECRET_PATTERNS):
            raise ValueError("Review public document before packaging: " + name)
    tree = output / "source"
    for relative, data in contents.items():
        target = tree / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {
        name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(contents.items())
    }
    archive = output / "solquira-source.zip"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(
                "solquira/" + name, date_time=(2026, 10, 28, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, data)
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        assert set(zipped.namelist()) == {"solquira/" + name for name in contents}
        for name, expected in manifest.items():
            assert (
                hashlib.sha256(zipped.read("solquira/" + name)).hexdigest()
                == expected["sha256"]
            )
        assert not zipped.comment and all(
            not info.comment for info in zipped.infolist()
        )
    receipt = {
        "schema": "solquira.github-source/1",
        "uploaded": False,
        "history_included": False,
        "secrets_included": False,
        "secret_scan": {
            "patterns": len(assets.SECRET_PATTERNS),
            "files_scanned": len(contents),
        },
        "archive_verified": True,
        "license_file_present": "LICENSE" in contents,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "files": manifest,
    }
    (output / "manifest.sha256.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "archive": str(archive),
                "files": len(manifest),
                "sha256": receipt["archive_sha256"],
                "uploaded": False,
                "license_file_present": receipt["license_file_present"],
            }
        )
    )
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    stage(output=ROOT / args.output if args.output else None)
