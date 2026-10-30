"""Run Solquira Studio on loopback with external adapters and credentials excluded."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Only operating-system/runtime settings, never provider/login credentials.
OS_ENVIRONMENT = ("PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "LANG", "LC_ALL", "PYTHONIOENCODING", "PYTHONUTF8", "VIRTUAL_ENV")


def local_environment(source):
    result = {name: source[name] for name in OS_ENVIRONMENT if name in source}
    result.update({"SOLQUIRA_STUDIO_MODE": "1", "SOLQUIRA_LIVE_ADAPTERS": "0", "SOLQUIRA_MCP_NASA_POWER": "0", "SOLQUIRA_ENABLE_QA_PREVIEW": "0", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return result


def main():
    parser = argparse.ArgumentParser(description="Run the English Solquira Studio locally. No provider or source adapters are activated.")
    parser.add_argument("--port", type=int, default=8036)
    parser.add_argument("--check", action="store_true", help="Show non-sensitive startup settings without starting a server.")
    parser.add_argument("--live-trial", action="store_true", help="Explicitly allow the bounded synthetic-only Nemotron connection trial.")
    parser.add_argument("--trial-calls", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--geodata", action="store_true", help="Allow the bounded public geographic forecast source for this session.")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    settings = {"host": "127.0.0.1", "port": args.port, "studio_mode": True, "external_adapters": False, "geographic_sources": args.geodata, "live_trial_requested": args.live_trial, "credentials_forwarded": False}
    if args.check:
        print(json.dumps(settings, indent=2))
        return 0
    if args.live_trial:
        key_file = ROOT / "scripts/secrets.local.env"
        if not key_file.is_file():
            key_file = ROOT / "scripts/.env"
        command = [sys.executable, str(ROOT / "scripts/studio-live-child.py"), "--port", str(args.port), "--key-file", str(key_file), "--trial-calls", str(args.trial_calls)]
        print("Solquira Studio: authorized bounded Nemotron connection trial on loopback.", flush=True)
    else:
        print("Solquira Studio: http://127.0.0.1:%s - local examples, real external inference disabled." % args.port, flush=True)
        command = [sys.executable, "-m", "uvicorn", "main:app", "--app-dir", str(ROOT / "artifacts/solquira/backend"), "--host", "127.0.0.1", "--port", str(args.port), "--no-access-log"]
    environment = local_environment(os.environ)
    environment["SOLQUIRA_STUDIO_GEODATA"] = "1" if args.geodata else "0"
    return subprocess.call(command, cwd=ROOT, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
