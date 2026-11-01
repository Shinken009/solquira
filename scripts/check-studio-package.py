"""Stage a source allowlist and exercise it offline, without reading any secrets.

--build optionally builds a LOCAL Docker image from the temporary safe context.
The backend-only image does not activate a model provider or geographic source.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).resolve().parents[1]
_spec = spec_from_file_location("studio_launcher", ROOT / "scripts/run-studio-local.py")
_launcher = module_from_spec(_spec)
_spec.loader.exec_module(_launcher)


def source_files(root):
    """Only known runtime locations; never recursive-copy the working directory."""
    backend = root / "artifacts/solquira/backend"
    # An added private config.py or credentials.py must not enter a release
    # merely because it shares the runtime directory. New modules need review.
    modules = (
        "assistant_inference",
        "assistant_layers",
        "assistant_pipeline",
        "assistant_prompts",
        "assistant_rate_limit",
        "assistant_routes",
        "assistant_schema",
        "assistant_telegram",
        "assistant_tools",
        "dryland_brief",
        "dryland_progressive",
        "dryland_references",
        "environmental_history",
        "main",
        "mcp_server",
        "power",
        "studio_api",
        "studio_brief",
        "studio_budget",
        "studio_geo",
        "studio_runtime",
        "studio_schema",
        "studio_service",
        "studio_synthesis",
    )
    paths = [backend / (name + ".py") for name in modules]
    paths += [
        backend / "requirements.lock.txt",
        backend / "power_sample.json",
        root / "artifacts/solquira/src/studio/fixtures.json",
        root / "prototypes/solquira-agent/IDENTIDAD.md",
    ]
    paths += [
        root / "prototypes/solquira-agent/src/solquira_agent" / name
        for name in ("__init__.py", "core.py")
    ]
    skills = (
        "solquira-explorar-escenarios",
        "solquira-preparar-decision",
        "solquira-revisar-evidencia",
        "solquira-revisar-suelo-cobertura",
    )
    paths += [
        root / "prototypes/solquira-agent/skills" / name / "SKILL.md" for name in skills
    ]
    return sorted(paths)


def stage_source(source, root, stage):
    """Reject files and ancestor junctions/symlinks escaping the reviewed root."""
    if (
        source.is_symlink()
        or not source.resolve().is_relative_to(root.resolve())
        or not source.is_file()
    ):
        raise ValueError(
            "Package source is missing, linked outside the root or is a symlink"
        )
    target = stage / source.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


SMOKE = r"""
import json, socket
from pathlib import Path
from fastapi.testclient import TestClient
original_connect = socket.socket.connect
def guarded_connect(sock, address):
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
        return original_connect(sock, address)
    raise AssertionError("External network forbidden in package smoke")
socket.socket.connect = guarded_connect
import main
import studio_service
scenario = {"profile_id":"valle", "water_percent":100, "previous_crop":"cereal"}
with TestClient(main.app, base_url="http://127.0.0.1") as client:
    assert client.get("/solquira-api/healthz").status_code == 200
    bootstrap = client.get("/solquira-api/studio/bootstrap")
    assert bootstrap.status_code == 200
    assert not studio_service.studio_status()["available"]
    history = client.post("/solquira-api/studio/history", headers={"X-Solquira-CSRF":bootstrap.json()["csrf_token"]}, json={"latitude":37.1,"longitude":-4.6})
    assert history.status_code == 200 and history.json()["history"]["state"] == "disabled"
    result = studio_service.evaluate_studio(scenario)
    explanation = studio_service.explain_studio(scenario, "What should I check about soil cover?")
    assert explanation["inference_mode"] == "local_fallback"
    assert explanation["trace"]
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    field = {"context_ref":"slq-context-"+"A"*32, "location":{"latitude":37.1,"longitude":-4.6}, "retrieved_at":now.isoformat(),
             "weather":{"state":"ready","source":"Open-Meteo","kind":"model_forecast","time_zone":"UTC","days":[{"date":(now.date()+timedelta(days=i)).isoformat(),
               "precipitation_mm":0,"reference_et0_mm":4,"temperature_min_c":10,"temperature_max_c":25} for i in range(7)]}}
    connected = studio_service.explain_studio(scenario, "What should I check about soil cover?", field_context=field)
    assert len(connected["trace"]) == 6
    assert connected["report"]["field_brief"]["forecast"]["reference_et0_mm"] == 28
print(json.dumps({"package_smoke":"pass", "provider":"disabled", "external_network":"blocked", "tools":len(explanation["trace"])}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build",
        action="store_true",
        help="Also build local solquira-studio:local; never push or run it.",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="solquira-package-") as directory:
        stage = Path(directory)
        files = source_files(ROOT)
        for source in files:
            stage_source(source, ROOT, stage)
        stage_source(ROOT / "scripts/run-studio-hosted.py", ROOT, stage)
        shutil.copyfile(ROOT / "deploy/studio/hosted.Dockerfile", stage / "Dockerfile")
        env = _launcher.local_environment(__import__("os").environ)
        env.update(
            {
                "SOLQUIRA_STUDIO_GEODATA": "0",
                "SOLQUIRA_DATA_DIR": str(stage / "data"),
                "SOLQUIRA_SQLITE_PATH": str(stage / "data/solquira.sqlite3"),
                "PYTHONPATH": str(stage / "artifacts/solquira/backend"),
            }
        )
        subprocess.run(
            [sys.executable, "-c", SMOKE], cwd=stage, env=env, check=True, timeout=60
        )
        print(
            json.dumps(
                {
                    "runtime_files": len(files),
                    "frontend_included": False,
                    "credentials_included": False,
                }
            )
        )
        if args.build:
            subprocess.run(
                ["docker", "build", "--tag", "solquira-studio:local", str(stage)],
                check=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
