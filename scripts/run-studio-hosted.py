"""Explicit single-instance hosted preparation. Never reads .env or starts a trial.

Requires a separately approved persistent ledger before live startup. --check is
read-only; --initialize-budget is the explicit one-time ledger creation action.
TLS must terminate at the configured trusted ingress; do not expose its HTTP
origin publicly. Reservations are conservative admission, not measured billing.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "artifacts/solquira/backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
OS_ENVIRONMENT = (
    "PATH",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "VIRTUAL_ENV",
)
BUDGET_ENV = {
    "budget_id": "SOLQUIRA_BUDGET_ID",
    "daily_micro_usd": "SOLQUIRA_BUDGET_DAILY_MICRO_USD",
    "lifetime_micro_usd": "SOLQUIRA_BUDGET_LIFETIME_MICRO_USD",
    "daily_calls": "SOLQUIRA_BUDGET_DAILY_CALLS",
    "lifetime_calls": "SOLQUIRA_BUDGET_LIFETIME_CALLS",
    "reservation_micro_usd": "SOLQUIRA_BUDGET_RESERVATION_MICRO_USD",
}
SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://gibs.earthdata.nasa.gov; connect-src 'self' https://gibs.earthdata.nasa.gov; worker-src 'self' blob:; font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "geolocation=(self), camera=(), microphone=()",
    "strict-transport-security": "max-age=31536000",
}


class HostedSecurityHeaders:
    """ASGI response policy for UI, API and errors; never trusts request headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def secured(message):
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower().decode("latin1") not in SECURITY_HEADERS
                ]
                headers.extend(
                    (name.encode("ascii"), value.encode("ascii"))
                    for name, value in SECURITY_HEADERS.items()
                )
                message = dict(message, headers=headers)
            await send(message)

        if (
            scope["type"] == "http"
            and scope.get("scheme") != "https"
            and scope.get("path") != "/solquira-api/healthz"
        ):
            body = b'{"detail":"HTTPS is required for the hosted Studio."}'
            await secured(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"cache-control", b"no-store"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, secured if scope["type"] == "http" else send)


def _budget_types():
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from studio_budget import BudgetLedger, BudgetPolicy

    return BudgetLedger, BudgetPolicy


def _integer(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,12}", value):
        raise ValueError("Use explicit positive integer runtime limits.")
    return int(value)


def _hosts(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Configure exact hosted hostname(s).")
    result = []
    for entry in value.split(","):
        host = entry.strip().lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if (
                len(host) > 253
                or not all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                    for label in host.split(".")
                )
                or "." not in host
            ):
                raise ValueError(
                    "Use plain exact hosted names/IPs without schemes, ports or wildcards."
                ) from None
        if host in result:
            raise ValueError("Do not repeat hosted names.")
        result.append(host)
    return ",".join(result)


def _proxies(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Explicitly configure the trusted HTTPS ingress IPs/networks.")
    result = []
    for entry in value.split(","):
        try:
            network = ipaddress.ip_network(entry.strip(), strict=True)
        except ValueError:
            raise ValueError(
                "Trusted proxy entries must be explicit IPs or canonical CIDR networks, never a wildcard."
            ) from None
        if network.prefixlen == 0 or (
            not network.is_private and network.num_addresses != 1
        ):
            raise ValueError("A global forwarded-header trust range is not allowed.")
        result.append(entry.strip())
    return ",".join(result)


@dataclass(frozen=True)
class HostedConfiguration:
    provider: str
    hosts: str
    proxies: str
    data_dir: Path
    port: int
    geodata: str
    policy: object = None
    key: str = field(default="", repr=False)


def configuration(source, *, require_key=True):
    if source.get("SOLQUIRA_STUDIO_HOSTED") != "1":
        raise ValueError("Hosted startup requires SOLQUIRA_STUDIO_HOSTED=1.")
    provider = source.get("SOLQUIRA_STUDIO_PROVIDER")
    if provider not in {"disabled", "nebius"}:
        raise ValueError(
            "Select SOLQUIRA_STUDIO_PROVIDER=disabled or nebius explicitly."
        )
    hosts = _hosts(source.get("SOLQUIRA_STUDIO_ALLOWED_HOSTS"))
    proxies = _proxies(source.get("SOLQUIRA_STUDIO_TRUSTED_PROXY_IPS"))
    raw_data = source.get("SOLQUIRA_DATA_DIR", "")
    data = Path(raw_data)
    if not raw_data or not data.is_absolute() or data.is_symlink() or not data.is_dir():
        raise ValueError(
            "Use an existing dedicated absolute persistent data directory."
        )
    port = _integer(source.get("PORT", "8000"))
    if not 1024 <= port <= 65535:
        raise ValueError("Configure a non-privileged valid TCP port.")
    geodata = source.get("SOLQUIRA_STUDIO_GEODATA", "0")
    if geodata not in {"0", "1"}:
        raise ValueError("Select geographic sources with an explicit 0 or 1.")
    policy = None
    key = ""
    if provider == "nebius":
        _, Policy = _budget_types()
        values = {field: source.get(variable) for field, variable in BUDGET_ENV.items()}
        values = {
            field: value if field == "budget_id" else _integer(value)
            for field, value in values.items()
        }
        policy = Policy(**values)
        if require_key:
            key = source.get("NEBIUS_API_KEY", "")
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 4096
                or any(c.isspace() or ord(c) < 32 for c in key)
            ):
                raise ValueError("Inject one well-formed server-side NEBIUS_API_KEY.")
    return HostedConfiguration(
        provider, hosts, proxies, data, port, geodata, policy, key
    )


def hosted_environment(config, source):
    environment = {name: source[name] for name in OS_ENVIRONMENT if name in source}
    environment.update(
        {
            "SOLQUIRA_STUDIO_MODE": "1",
            "SOLQUIRA_LIVE_ADAPTERS": "0",
            "SOLQUIRA_MCP_NASA_POWER": "0",
            "SOLQUIRA_ENABLE_QA_PREVIEW": "0",
            "SOLQUIRA_STUDIO_GEODATA": config.geodata,
            "SOLQUIRA_STUDIO_ALLOWED_HOSTS": config.hosts,
            "SOLQUIRA_STUDIO_ALLOWED_ORIGINS": (
                source.get("SOLQUIRA_STUDIO_ALLOWED_ORIGINS", "").strip()
                or ",".join(
                    "https://" + ("[" + host + "]" if ":" in host else host)
                    for host in config.hosts.split(",")
                )
            ),
            "SOLQUIRA_DATA_DIR": str(config.data_dir),
            "SOLQUIRA_SQLITE_PATH": str(config.data_dir / "solquira.sqlite3"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    # The key is passed to the provider constructor, never ambient environment.
    # No legacy token, proxy credential, user path or adapter setting survives.
    return environment


def prepare_provider(config, *, provider_factory=None):
    if config.provider == "disabled":
        return None
    Ledger, _ = _budget_types()
    ledger = Ledger(config.data_dir / "studio-budget.sqlite3", config.policy)
    ledger.snapshot()  # Fail before constructing a provider if missing/corrupt.
    import studio_service  # Loads the reviewed agent package path.
    from solquira_agent import NebiusProvider
    from studio_budget import PersistentBudgetProvider
    from studio_runtime import NANO_MODEL

    factory = provider_factory or NebiusProvider
    return PersistentBudgetProvider(
        factory(
            enabled=True, api_key=config.key, model=NANO_MODEL, disable_reasoning=True
        ),
        ledger,
    )


def launch(config):
    if not (ROOT / "artifacts/solquira/dist/public/index.html").is_file():
        raise ValueError(
            "The connected frontend build is missing from the hosted package."
        )
    provider = prepare_provider(config)
    environment = hosted_environment(config, os.environ)
    os.environ.clear()
    os.environ.update(environment)
    _budget_types()
    from studio_runtime import current_provider, install_hosted

    if current_provider() is not None:
        raise ValueError(
            "Start a fresh single-instance process; no runtime provider is replaced."
        )
    if provider is not None:
        install_hosted(provider)
    from main import app
    import uvicorn

    uvicorn.run(
        HostedSecurityHeaders(app),
        host="0.0.0.0",
        port=config.port,
        workers=1,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=config.proxies,
        server_header=False,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and existing ledger without starting inference/server.",
    )
    actions.add_argument(
        "--initialize-budget",
        action="store_true",
        help="Explicit first creation only; never replace an existing ledger.",
    )
    args = parser.parse_args(argv)
    config = configuration(
        os.environ, require_key=not (args.initialize_budget or args.check)
    )
    if args.initialize_budget:
        if config.provider != "nebius":
            raise ValueError(
                "An explicitly selected provider and approved policy are required for ledger initialization."
            )
        Ledger, _ = _budget_types()
        Ledger(config.data_dir / "studio-budget.sqlite3", config.policy).initialize()
        print(
            "Persistent admission ledger initialized; no provider or server was started."
        )
    elif args.check:
        if config.policy is not None:
            Ledger, _ = _budget_types()
            Ledger(config.data_dir / "studio-budget.sqlite3", config.policy).snapshot()
        print(
            json.dumps(
                {
                    "configuration": "valid",
                    "single_instance": True,
                    "provider": config.provider,
                    "persistent_ledger": config.policy is not None,
                    "trial_installed": False,
                    "server_started": False,
                    "model_called": False,
                }
            )
        )
    else:
        launch(config)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Hosted Studio could not start. Check explicit host, ingress, persistent budget and server-secret configuration; no secret or raw error was logged.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
