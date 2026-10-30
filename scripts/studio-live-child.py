"""Explicit synthetic-only, bounded Studio connection trial. No ambient secrets."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_key(path):
    path = Path(path).resolve()
    allowed = {ROOT / "scripts/.env", ROOT / "scripts/secrets.local.env"}
    if not path.is_relative_to(ROOT.resolve()) or path not in {p.resolve() for p in allowed} or not path.is_file():
        raise ValueError("Use the authorized local Studio secret file.")
    found = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("NEBIUS_API_KEY="):
            found.append(line.split("=", 1)[1].strip().strip("\"'"))
    if len(found) != 1 or not found[0] or any(c.isspace() or ord(c) < 32 for c in found[0]):
        raise ValueError("A single well-formed NEBIUS_API_KEY is required.")
    return found[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--trial-calls", type=int, default=5)
    args = parser.parse_args()
    if os.environ.get("SOLQUIRA_STUDIO_MODE") != "1" or os.environ.get("SOLQUIRA_LIVE_ADAPTERS") != "0":
        raise ValueError("Start the live child only through the isolated Studio launcher.")
    if not 1024 <= args.port <= 65535:
        raise ValueError("Invalid local port.")
    key = read_key(args.key_file)
    sys.path.insert(0, str(ROOT / "artifacts/solquira/backend"))
    import studio_service  # Makes the framework-neutral provider module available.
    from solquira_agent.core import NebiusProvider
    from studio_runtime import ConnectionTrial, install_trial, NANO_MODEL
    import uvicorn
    trial = ConnectionTrial(NebiusProvider(enabled=True, api_key=key, model=NANO_MODEL, disable_reasoning=True), calls=args.trial_calls)
    install_trial(trial)
    print("Studio connection trial: Nemotron Nano, at most %s model requests, 20 minutes, synthetic inputs only." % args.trial_calls, flush=True)
    try:
        uvicorn.run("main:app", host="127.0.0.1", port=args.port, access_log=False)
    finally:
        output = ROOT / "output/live-connection-2026-10-25"
        output.mkdir(parents=True, exist_ok=True)
        (output / "provider-receipts.json").write_text(json.dumps({"model": NANO_MODEL, "calls": trial.receipts()}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("The connection trial could not start. No credentials or raw provider output were logged.", file=sys.stderr)
        raise SystemExit(1) from None
