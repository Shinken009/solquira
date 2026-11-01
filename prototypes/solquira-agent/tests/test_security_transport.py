"""Provider egress regression with synthetic credentials and an in-memory transport."""
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "src"))
from solquira_agent import AgentError, NebiusProvider
import httpx


class ProviderEgressTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("External sockets forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_redirect_never_forwards_authorization_to_another_host(self):
        requests = []
        def handle(request):
            requests.append(request)
            return httpx.Response(307, headers={"location": "https://attacker.invalid/capture"}, text="PRIVATE_REMOTE_BODY")
        provider = NebiusProvider(enabled=True, api_key="SYNTHETIC_TEST_KEY", model="nvidia/nemotron-test",
                                  transport=httpx.MockTransport(handle))
        with self.assertRaises(AgentError) as failure:
            provider.complete([{"role":"user","content":"Example only"}], [])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].url.host, "api.tokenfactory.nebius.com")
        self.assertEqual(failure.exception.code, "provider_error")
        for marker in ("SYNTHETIC_TEST_KEY", "PRIVATE_REMOTE_BODY", "attacker.invalid"):
            self.assertNotIn(marker, str(failure.exception))

    def test_ambient_proxy_is_not_trusted_by_provider_client(self):
        calls = []
        original = httpx.Client
        def factory(*args, **kwargs):
            calls.append(kwargs)
            return original(*args, **kwargs)
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices":[{"message":{"content":"test-only"}}]}))
        provider = NebiusProvider(enabled=True, api_key="SYNTHETIC_TEST_KEY", model="nvidia/nemotron-test", transport=transport)
        with patch.dict(os.environ, {"HTTPS_PROXY":"http://untrusted.invalid:8080"}), patch.object(httpx, "Client", side_effect=factory):
            self.assertEqual(provider.complete([], [])["content"], "test-only")
        self.assertIs(calls[0]["trust_env"], False)
        self.assertIs(calls[0]["follow_redirects"], False)
        self.assertEqual(provider.mode, "provider_stub")


    def test_usage_and_http_status_reset_before_timeout(self):
        calls = []
        def handle(request):
            calls.append(request)
            if len(calls) == 2:
                raise httpx.ReadTimeout("Synthetic timeout")
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "test-only"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52},
            })
        provider = NebiusProvider(enabled=True, api_key="SYNTHETIC_TEST_KEY", model="nvidia/nemotron-test",
                                  transport=httpx.MockTransport(handle))
        provider.complete([], [])
        self.assertEqual(provider.last_usage["completion_tokens"], 12)
        self.assertEqual(provider.last_http_status, 200)
        with self.assertRaises(AgentError) as failure:
            provider.complete([], [])
        self.assertEqual(failure.exception.code, "timeout")
        self.assertEqual(provider.last_usage, {})
        self.assertIsNone(provider.last_http_status)
        self.assertEqual(len(calls), 2)

    def test_stream_without_usage_does_not_inherit_previous_counts(self):
        calls = []
        def handle(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "test-only"}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 12},
                })
            return httpx.Response(200, text=(
                'data: {"choices":[{"index":0,"delta":{"content":"{}"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n'
            ), headers={"Content-Type": "text/event-stream"})
        provider = NebiusProvider(enabled=True, api_key="SYNTHETIC_TEST_KEY", model="nvidia/nemotron-test",
                                  transport=httpx.MockTransport(handle))
        provider.complete([], [])
        self.assertEqual(provider.last_usage, {"completion_tokens": 12})
        deltas = []
        self.assertEqual(provider.complete([], [], on_delta=deltas.append), {"content": "{}"})
        self.assertEqual(deltas, ["{}"])
        self.assertEqual(provider.last_usage, {})
        self.assertEqual(provider.last_http_status, 200)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
