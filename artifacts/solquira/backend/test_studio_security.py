"""Offline security regressions: untrusted hosts, parsing and admission boundaries."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import main
import studio_api
import studio_runtime
from test_studio_runtime import Fake


SCENARIO = {"profile_id":"valle","water_percent":100,"previous_crop":"cereal"}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{"SOLQUIRA_STUDIO_MODE":"1"},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        studio_api.ADMISSION.reset();self.addCleanup(studio_api.ADMISSION.reset)
        self.network=patch("httpx.HTTPTransport.handle_request",side_effect=AssertionError("External network forbidden"))
        self.network.start();self.addCleanup(self.network.stop)
        self.client=TestClient(main.app,base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def test_attacker_hostname_cannot_bootstrap_even_when_origin_matches(self):
        with TestClient(main.app,base_url="http://rebind.attacker.invalid") as client:
            for headers in ({},{"Origin":"http://rebind.attacker.invalid"}):
                response=client.get("/solquira-api/studio/bootstrap",headers=headers)
                self.assertEqual(response.status_code,403)
                self.assertNotIn("set-cookie",response.headers)
        self.assertEqual(len(studio_api.ADMISSION.sessions),0)

    def test_untrusted_host_cannot_use_an_existing_session(self):
        csrf=self.client.get("/solquira-api/studio/bootstrap").json()["csrf_token"]
        response=self.client.post("/solquira-api/studio/evaluate",json=SCENARIO,
          headers={"Host":"rebind.attacker.invalid","Origin":"http://rebind.attacker.invalid",studio_api.CSRF_HEADER:csrf})
        self.assertEqual(response.status_code,403)
        self.assertEqual(studio_api.ADMISSION.active,0)

    def test_loopback_hosts_work_without_ambient_configuration(self):
        # Starlette's installed TestClient cannot parse an IPv6 base_url;
        # inject its valid Host header while keeping the in-process URL IPv4.
        for host in ("127.0.0.1:8036","localhost:8036","[::1]:8036"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/solquira-api/studio/bootstrap",headers={"Host":host}).status_code,200)

    def test_public_host_requires_exact_explicit_configuration(self):
        with patch.dict(os.environ,{"SOLQUIRA_STUDIO_ALLOWED_HOSTS":"studio.example"}):
            with TestClient(main.app,base_url="https://studio.example") as client:
                response=client.get("/solquira-api/studio/bootstrap")
                self.assertEqual(response.status_code,200)
                self.assertIn("Secure",response.headers["set-cookie"])
            with TestClient(main.app,base_url="https://sub.studio.example") as client:
                self.assertEqual(client.get("/solquira-api/studio/bootstrap").status_code,403)

    def test_origin_allowlist_alone_does_not_authorize_host(self):
        with patch.dict(os.environ,{"SOLQUIRA_STUDIO_ALLOWED_ORIGINS":"https://attacker.invalid"}),TestClient(main.app,base_url="https://attacker.invalid") as client:
            self.assertEqual(client.get("/solquira-api/studio/bootstrap",headers={"Origin":"https://attacker.invalid"}).status_code,403)

    def test_invalid_host_configuration_fails_closed_without_echoing_it(self):
        for value in ("*","*.example","https://studio.example","studio.example:443","user@studio.example","studio.example/path","bad..example"):
            with self.subTest(value=value),patch.dict(os.environ,{"SOLQUIRA_STUDIO_ALLOWED_HOSTS":value}):
                response=self.client.get("/solquira-api/studio/bootstrap")
                self.assertEqual(response.status_code,503)
                self.assertNotIn(value,response.text)

    def test_malformed_host_headers_fail_before_session_creation(self):
        for host in ("localhost:bad","localhost:","localhost:0","localhost:70000","user@localhost","localhost/path","localhost,attacker.invalid"):
            with self.subTest(host=host):
                response=self.client.get("/solquira-api/studio/bootstrap",headers={"Host":host})
                self.assertEqual(response.status_code,400)
        self.assertFalse(studio_api.ADMISSION.sessions)

    def test_duplicate_host_or_origin_headers_are_rejected(self):
        for headers in ([('Host','127.0.0.1'),('Host','attacker.invalid')],
                        [('Origin','http://127.0.0.1'),('Origin','http://attacker.invalid')]):
            with self.subTest(headers=headers):
                response=self.client.get("/solquira-api/studio/bootstrap",headers=headers)
                self.assertEqual(response.status_code,400)

    def test_duplicate_json_fields_are_rejected_before_engine(self):
        csrf=self.client.get("/solquira-api/studio/bootstrap").json()["csrf_token"]
        payload='{"profile_id":"valle","water_percent":40,"water_percent":100,"previous_crop":"cereal"}'
        with patch.object(studio_api,"_service") as service:
            response=self.client.post("/solquira-api/studio/evaluate",content=payload,
                headers={"Content-Type":"application/json",studio_api.CSRF_HEADER:csrf})
        self.assertEqual(response.status_code,422)
        service.assert_not_called()
        self.assertEqual(studio_api.ADMISSION.active,0)

    def test_trial_limits_require_integers_not_boolean_or_fractional_values(self):
        for kwargs in ({"calls":True},{"calls":1.5},{"seconds":True},{"seconds":1.5}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                studio_runtime.ConnectionTrial(Fake(),**kwargs)

    def test_forged_provider_in_request_cannot_be_activated(self):
        csrf=self.client.get("/solquira-api/studio/bootstrap").json()["csrf_token"]
        with patch.object(studio_api,"current_provider") as provider:
            response=self.client.post("/solquira-api/studio/agent",json={"scenario":SCENARIO,"query":"water","provider":"nebius","model":"private"},headers={studio_api.CSRF_HEADER:csrf})
        self.assertEqual(response.status_code,422)
        provider.assert_not_called()
        self.assertNotIn('"private"',response.text)

    def test_forwarded_headers_do_not_authorize_an_untrusted_host(self):
        response=self.client.get("/solquira-api/studio/bootstrap",headers={"Host":"attacker.invalid","X-Forwarded-Host":"127.0.0.1","Forwarded":"host=127.0.0.1;proto=http"})
        self.assertEqual(response.status_code,403)


if __name__=="__main__":unittest.main()
