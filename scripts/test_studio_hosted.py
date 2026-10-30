"""Hosted configuration/headers with synthetic values; no server/model/network."""
import asyncio
from dataclasses import replace
import importlib.util
import io
import json
import os
import socket
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("solquira_hosted_launcher", Path(__file__).with_name("run-studio-hosted.py"))
launcher = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = launcher
spec.loader.exec_module(launcher)


class HostedTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = {"SOLQUIRA_STUDIO_HOSTED": "1", "SOLQUIRA_STUDIO_PROVIDER": "disabled",
                       "SOLQUIRA_STUDIO_ALLOWED_HOSTS": "solquira.example", "SOLQUIRA_STUDIO_TRUSTED_PROXY_IPS": "10.0.0.4",
                       "SOLQUIRA_DATA_DIR": self.directory.name}
        original_connect = socket.socket.connect
        def local_only(sock, address):
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original_connect(sock, address)
            raise AssertionError("External network forbidden in hosted tests")
        self.network = patch("socket.socket.connect", new=local_only)
        self.network.start()
        self.addCleanup(self.network.stop)

    def with_budget(self):
        return self.source | {"SOLQUIRA_STUDIO_PROVIDER": "nebius", "SOLQUIRA_BUDGET_ID": "test-hosted-budget",
                              "SOLQUIRA_BUDGET_DAILY_MICRO_USD": "20000", "SOLQUIRA_BUDGET_LIFETIME_MICRO_USD": "40000",
                              "SOLQUIRA_BUDGET_DAILY_CALLS": "2", "SOLQUIRA_BUDGET_LIFETIME_CALLS": "4",
                              "SOLQUIRA_BUDGET_RESERVATION_MICRO_USD": "7000", "NEBIUS_API_KEY": "synthetic-only-marker"}

    def test_explicit_opt_in_provider_hosts_ingress_and_directory_required(self):
        for name in ("SOLQUIRA_STUDIO_HOSTED", "SOLQUIRA_STUDIO_PROVIDER", "SOLQUIRA_STUDIO_ALLOWED_HOSTS", "SOLQUIRA_STUDIO_TRUSTED_PROXY_IPS", "SOLQUIRA_DATA_DIR"):
            source = dict(self.source); source.pop(name)
            with self.subTest(missing=name), self.assertRaises(ValueError): launcher.configuration(source)
        self.assertIsNone(launcher.prepare_provider(launcher.configuration(self.source)))

    def test_host_names_are_exact_and_no_global_proxy_trust_is_allowed(self):
        for hosts in ("*", "https://solquira.example", "solquira.example:443", "name/path", "a..example", "solquira.example,", "localhost"):
            with self.subTest(hosts=hosts), self.assertRaises(ValueError):
                launcher.configuration(self.source | {"SOLQUIRA_STUDIO_ALLOWED_HOSTS": hosts})
        for proxy in ("*", "0.0.0.0/0", "::/0", "8.8.8.0/24", "https://10.0.0.4", "10.0.0.4:123", ""):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                launcher.configuration(self.source | {"SOLQUIRA_STUDIO_TRUSTED_PROXY_IPS": proxy})
        self.assertEqual(launcher._proxies("10.0.0.0/24,127.0.0.1"), "10.0.0.0/24,127.0.0.1")

    def test_nebius_requires_every_explicit_budget_and_a_key(self):
        source = self.with_budget()
        for variable in (*launcher.BUDGET_ENV.values(), "NEBIUS_API_KEY"):
            missing = dict(source); missing.pop(variable)
            with self.subTest(missing=variable), self.assertRaises(ValueError): launcher.configuration(missing)
        for value in ("1.2", "true", "-1", "0", " 2", "02"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                launcher.configuration(source | {"SOLQUIRA_BUDGET_DAILY_CALLS": value})

    def test_nebius_missing_ledger_precedes_provider_constructor(self):
        config = launcher.configuration(self.with_budget())
        with patch("builtins.open", side_effect=AssertionError("No credential file may be read")):
            with self.assertRaises(ValueError):
                launcher.prepare_provider(config, provider_factory=lambda **_: self.fail("Provider constructed before ledger validation"))
        self.assertFalse((config.data_dir / "studio-budget.sqlite3").exists())

    def test_environment_discards_legacy_adapters_credentials_proxies_and_provider_key(self):
        source = self.with_budget() | {"PATH": "runtime", "TELEGRAM_BOT_TOKEN": "synthetic-telegram", "SOLQUIRA_API_KEY": "synthetic-old-api",
                                      "HTTPS_PROXY": "https://secret.invalid", "SOLQUIRA_LIVE_ADAPTERS": "1", "PYTHONPATH": "untrusted"}
        config = launcher.configuration(source)
        environment = launcher.hosted_environment(config, source)
        for name in ("TELEGRAM_BOT_TOKEN", "SOLQUIRA_API_KEY", "NEBIUS_API_KEY", "HTTPS_PROXY", "PYTHONPATH"):
            self.assertNotIn(name, environment)
        self.assertEqual(environment["SOLQUIRA_STUDIO_MODE"], "1")
        self.assertEqual(environment["SOLQUIRA_LIVE_ADAPTERS"], "0")
        self.assertEqual(environment["SOLQUIRA_STUDIO_ALLOWED_ORIGINS"], "https://solquira.example")
        self.assertEqual(environment["PATH"], "runtime")

    def test_check_and_initialization_never_read_key_or_construct_provider(self):
        source = self.with_budget(); source.pop("NEBIUS_API_KEY")
        with patch.dict(os.environ, source, clear=True), patch.object(launcher, "launch", side_effect=AssertionError("No server")), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(launcher.main(["--initialize-budget"]), 0)
            before = (Path(self.directory.name) / "studio-budget.sqlite3").read_bytes()
            self.assertEqual(launcher.main(["--check"]), 0)
            self.assertNotIn("synthetic-only-marker", output.getvalue())
            self.assertIn('"server_started": false', output.getvalue())
            self.assertEqual((Path(self.directory.name) / "studio-budget.sqlite3").read_bytes(), before)

    def test_provider_stub_requires_preexisting_ledger_and_fixed_model(self):
        source = self.with_budget()
        config = launcher.configuration(source)
        Ledger, _ = launcher._budget_types()
        Ledger(config.data_dir / "studio-budget.sqlite3", config.policy).initialize()
        captured = []
        class Fake:
            mode = "provider_stub"
            def __init__(self, **kwargs):
                captured.append(kwargs)
                self.model = kwargs["model"]
            def complete(self, *args, **kwargs): return {"content": "{}"}
        provider = launcher.prepare_provider(config, provider_factory=Fake)
        self.assertEqual(provider.mode, "provider_stub")
        self.assertTrue(captured[0]["disable_reasoning"])
        self.assertEqual(captured[0]["model"], "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
        self.assertEqual(provider.ledger.snapshot()["calls_used"], 0)

    def test_launch_rejects_missing_frontend_before_provider_or_server(self):
        config = launcher.configuration(self.source)
        with patch.object(launcher, "ROOT", Path(self.directory.name)), patch.object(launcher, "prepare_provider", side_effect=AssertionError("No activation")):
            with self.assertRaises(ValueError): launcher.launch(config)

    def test_launch_uses_one_worker_security_wrapper_and_only_explicit_proxy_ips(self):
        config = launcher.configuration(self.source)
        root = Path(self.directory.name)
        frontend = root / "artifacts/solquira/dist/public/index.html"
        frontend.parent.mkdir(parents=True)
        frontend.write_text("<html></html>", encoding="utf-8")
        run = Mock()
        launcher._budget_types()
        with patch.object(launcher, "ROOT", root), patch.dict(os.environ, self.source | {"TELEGRAM_BOT_TOKEN": "synthetic-removed"}, clear=True), patch.dict(sys.modules, {"main": SimpleNamespace(app=object()), "uvicorn": SimpleNamespace(run=run)}), patch("studio_runtime._provider", None):
            launcher.launch(config)
            self.assertNotIn("TELEGRAM_BOT_TOKEN", os.environ)
        run.assert_called_once()
        self.assertIsInstance(run.call_args.args[0], launcher.HostedSecurityHeaders)
        self.assertEqual(run.call_args.kwargs["workers"], 1)
        self.assertEqual(run.call_args.kwargs["forwarded_allow_ips"], "10.0.0.4")
        self.assertTrue(run.call_args.kwargs["proxy_headers"])
        self.assertFalse(run.call_args.kwargs["access_log"])

    def test_configuration_repr_does_not_display_provider_secret(self):
        config = launcher.configuration(self.with_budget())
        self.assertNotIn("synthetic-only-marker", repr(config))

    def test_https_bootstrap_gets_secure_cookie_headers_and_studio_isolation(self):
        launcher._budget_types()
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, launcher.hosted_environment(launcher.configuration(self.source), {}), clear=True):
            from main import app
            with TestClient(launcher.HostedSecurityHeaders(app), base_url="https://solquira.example") as client:
                response = client.get("/solquira-api/studio/bootstrap")
                self.assertEqual(response.status_code, 200)
                self.assertIn("Secure", response.headers["set-cookie"])
                self.assertIn("HttpOnly", response.headers["set-cookie"])
                self.assertIn("SameSite=strict", response.headers["set-cookie"])
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["x-frame-options"], "DENY")
                self.assertEqual(client.get("/docs").status_code, 404)
                self.assertEqual(client.get("/solquira-api/parcela").status_code, 404)
                self.assertEqual(client.get("/solquira-api/studio/bootstrap", headers={"Host": "attacker.invalid"}).status_code, 403)

    def http_messages(self, *, client="10.0.0.4", scheme="http", forwarded="https", path="/"):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"x-frame-options", b"SAMEORIGIN")]})
            await send({"type": "http.response.body", "body": b"ok"})
        wrapped = ProxyHeadersMiddleware(launcher.HostedSecurityHeaders(app), trusted_hosts=launcher._proxies(self.source["SOLQUIRA_STUDIO_TRUSTED_PROXY_IPS"]))
        messages = []
        async def run():
            async def receive(): return {"type": "http.request", "body": b""}
            async def send(message): messages.append(message)
            await wrapped({"type": "http", "scheme": scheme, "path": path, "client": (client, 1234),
                           "headers": [(b"x-forwarded-proto", forwarded.encode())]}, receive, send)
        asyncio.run(run())
        return messages

    def test_security_headers_preserve_cesium_nasa_and_deny_frames(self):
        messages = self.http_messages()
        self.assertEqual(messages[0]["status"], 200)
        headers = {name.decode(): value.decode() for name, value in messages[0]["headers"]}
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("worker-src 'self' blob:", headers["content-security-policy"])
        self.assertIn("'wasm-unsafe-eval'", headers["content-security-policy"])
        self.assertIn("https://gibs.earthdata.nasa.gov", headers["content-security-policy"])
        self.assertNotIn("https:", headers["content-security-policy"].replace("https://gibs.earthdata.nasa.gov", ""))

    def test_forged_forwarded_https_from_untrusted_ip_is_rejected(self):
        self.assertEqual(self.http_messages(client="203.0.113.9")[0]["status"], 503)
        self.assertEqual(self.http_messages(client="10.0.0.5")[0]["status"], 503)
        self.assertEqual(self.http_messages(forwarded="http")[0]["status"], 503)
        self.assertEqual(self.http_messages(scheme="https", client="203.0.113.9")[0]["status"], 200)
        self.assertEqual(self.http_messages(client="203.0.113.9", path="/solquira-api/healthz")[0]["status"], 200)


if __name__ == "__main__":
    unittest.main()
