"""Stage reviewed full-stack sources and built UI. No secrets, image push or deploy."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package = helper("hosted_source_boundary", "check-studio-package.py")
assets = helper("hosted_asset_boundary", "release_scan.py")


def stage(root=ROOT, output=None):
    root = Path(root).resolve(strict=True)
    output = Path(output or root / "output/hosted-release-2026-10-27").resolve()
    if not output.is_relative_to(root / "output") or output.exists():
        raise ValueError("Select a new output directory under this workspace's output/.")
    frontend = root / "artifacts/solquira/dist/public"
    assets.regular_path(frontend, root)
    inventory = assets.inventory(frontend)
    sources = package.source_files(root) + [root / "artifacts/solquira/backend/studio_budget.py",
                                          root / "scripts/run-studio-hosted.py"]
    dockerfile = root / "deploy/studio/hosted.Dockerfile"
    # Validate every source before creating a candidate directory.
    for source in [*sources, dockerfile]:
        assets.regular_path(source, root)
        if not source.is_file():
            raise ValueError("A required hosted source is missing.")
    context = output / "context"
    for source in sources:
        package.stage_source(source, root, context)
    package.stage_source(dockerfile, root, context)
    shutil.copyfile(context / "deploy/studio/hosted.Dockerfile", context / "Dockerfile")
    for relative, expected in inventory.items():
        source = frontend / relative
        assets.regular_path(source, frontend)
        target = context / "artifacts/solquira/dist/public" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected["sha256"]:
            raise ValueError("The frontend changed during staging.")
    if assets.inventory(frontend) != inventory:
        raise ValueError("The frontend changed during staging.")
    files = {}
    for file in sorted(context.rglob("*")):
        if not file.is_file():
            continue
        data = file.read_bytes()
        if any(pattern.search(data) for pattern in assets.SECRET_PATTERNS):
            raise ValueError("Sensitive-looking content in the hosted release; no approved manifest written.")
        files[file.relative_to(context).as_posix()] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    manifest = {"schema": "solquira.hosted-release/1", "deployed": False, "image_verified": False,
                "provider_activated": False, "single_instance": True, "files": files}
    (output / "manifest.sha256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output, context, manifest


def smoke(context):
    """Exercise the exact staged API and UI, with external sockets blocked."""
    env = package._launcher.local_environment(__import__("os").environ)
    env.update({"SOLQUIRA_STUDIO_GEODATA": "0", "SOLQUIRA_DATA_DIR": str(context.parent / "smoke-data"),
                "SOLQUIRA_SQLITE_PATH": str(context.parent / "smoke-data/solquira.sqlite3"),
                "PYTHONPATH": str(context / "artifacts/solquira/backend")})
    script = package.SMOKE + '\nwith TestClient(main.app, base_url="http://127.0.0.1") as ui:\n    assert ui.get("/").status_code == 200\n    assert "<html" in ui.get("/").text\n    assert ui.get("/scripts/run-studio-hosted.py").status_code == 404\n'
    subprocess.run([sys.executable, "-c", script], cwd=context, env=env, check=True, timeout=60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    output, context, manifest = stage(output=ROOT / args.output if args.output else None)
    if args.smoke:
        smoke(context)
    print(json.dumps({"status": "STAGED_NOT_DEPLOYED", "files": len(manifest["files"]),
                      "context": str(context), "provider_activated": False, "image_verified": False}))
