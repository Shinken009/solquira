"""History remains an explicit admitted source lookup, not an inference path."""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import main
import studio_api


class HistoricalApiTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"SOLQUIRA_STUDIO_MODE": "1"}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        studio_api.ADMISSION.reset()
        self.addCleanup(studio_api.ADMISSION.reset)
        self.network = patch("httpx.HTTPTransport.handle_request", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = TestClient(main.app, base_url="http://127.0.0.1")
        self.addCleanup(self.client.close)

    def token(self):
        return {studio_api.CSRF_HEADER: self.client.get("/solquira-api/studio/bootstrap").json()["csrf_token"]}

    def test_requires_session_and_csrf_before_calling_source(self):
        with patch("environmental_history.historical_context") as source:
            self.assertEqual(self.client.post("/solquira-api/studio/history", json={"latitude": 1., "longitude": 2.}).status_code, 401)
            self.token()
            self.assertEqual(self.client.post("/solquira-api/studio/history", json={"latitude": 1., "longitude": 2.}).status_code, 403)
            source.assert_not_called()

    def test_valid_request_is_forwarded_without_model_access(self):
        headers = self.token()
        with patch("environmental_history.historical_context", return_value={"history": {"state": "disabled"}}) as source, patch.object(studio_api, "current_provider", side_effect=AssertionError("Inference forbidden")):
            response = self.client.post("/solquira-api/studio/history", headers=headers, json={"latitude": -30.03, "longitude": -70.71})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["history"]["state"], "disabled")
        source.assert_called_once_with({"latitude": -30.03, "longitude": -70.71})

    def test_invalid_coordinates_and_extra_fields_never_reach_source(self):
        headers = self.token()
        with patch("environmental_history.historical_context") as source:
            for payload in ({"latitude": 91., "longitude": 2.}, {"latitude": 1.}, {"latitude": 1., "longitude": 2., "url": "https://untrusted.invalid"}):
                self.assertEqual(self.client.post("/solquira-api/studio/history", headers=headers, json=payload).status_code, 422)
            source.assert_not_called()

    def test_untrusted_origin_is_rejected(self):
        headers = self.token() | {"Origin": "https://untrusted.invalid"}
        with patch("environmental_history.historical_context") as source:
            self.assertEqual(self.client.post("/solquira-api/studio/history", headers=headers, json={"latitude": 1., "longitude": 2.}).status_code, 403)
            source.assert_not_called()


if __name__ == "__main__":
    unittest.main()
