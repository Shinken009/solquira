"""Historical monthly context with generated fixtures only; no live requests."""

import copy
import json
import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import httpx
from pydantic import ValidationError

import environmental_history as history

TARGET_START, TARGET_END = date(2026, 9, 1), date(2026, 9, 30)


def source(start=TARGET_START, end=TARGET_END):
    first = date(1991, 1, 1)
    days = [first+timedelta(days=i) for i in range((end-first).days+1)]
    return {
        "timezone": "GMT", "utc_offset_seconds": 0,
        "latitude": -30.0, "longitude": -70.75,
        "daily_units": {"time": "iso8601", "precipitation_sum": "mm"},
        "daily": {
            "time": [day.isoformat() for day in days],
            "precipitation_sum": [
                1.0 if day.month == start.month and day.year <= 2020
                else .5 if start <= day <= end else 0.0
                for day in days
            ],
        },
    }


class HistoricalTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"SOLQUIRA_STUDIO_GEODATA": "1"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.today = patch.object(history, "_today", return_value=date(2026, 11, 1))
        self.today.start()
        self.addCleanup(self.today.stop)
        history._cache.clear()
        history._requests.clear()
        history._inflight.clear()

    def test_lag_requires_latest_complete_calendar_month(self):
        cases = [
            (date(2026, 11, 1), (date(2026, 9, 1), date(2026, 9, 30))),
            (date(2026, 11, 5), (date(2026, 10, 1), date(2026, 10, 31))),
            (date(2026, 1, 1), (date(2025, 11, 1), date(2025, 11, 30))),
            (date(2024, 3, 5), (date(2024, 2, 1), date(2024, 2, 29))),
        ]
        for today, expected in cases:
            with self.subTest(today=today):
                self.assertEqual(history.target_period(today), expected)

    def test_target_must_not_overlap_baseline(self):
        with self.assertRaises(ValueError):
            history.target_period(date(2021, 1, 1))

    def test_exact_monthly_comparison_reconstructs_thirty_complete_samples(self):
        result = history.summarize_history(source(), TARGET_START, TARGET_END)
        self.assertEqual(result["precipitation_mm"], 15)
        self.assertEqual(result["baseline_mean_mm"], 30)
        self.assertEqual(result["anomaly_mm"], -15)
        self.assertEqual(result["anomaly_percent"], -50)
        self.assertEqual(result["coverage"]["baseline_complete_years"], 30)
        self.assertEqual(result["coverage"]["target_days"], 30)
        self.assertEqual(len(result["baseline_monthly_totals"]), 30)
        self.assertEqual([row["year"] for row in result["baseline_monthly_totals"]], list(range(1991, 2021)))

    def test_calendar_february_uses_actual_days_in_leap_and_nonleap_years(self):
        start, end = date(2024, 2, 1), date(2024, 2, 29)
        result = history.summarize_history(source(start, end), start, end)
        self.assertEqual(result["precipitation_mm"], 14.5)
        self.assertEqual(result["coverage"]["target_days"], 29)
        counts = {row["year"]: row["days"] for row in result["baseline_monthly_totals"]}
        self.assertEqual(counts[2000], 29)
        self.assertEqual(counts[2001], 28)

    def test_positive_and_zero_anomalies_keep_their_sign(self):
        for amount, expected_mm, expected_percent in ((2.0,30.0,100.0),(1.0,0.0,0.0)):
            payload=source()
            for index,stamp in enumerate(payload["daily"]["time"]):
                if "2026-09-01" <= stamp <= "2026-09-30":
                    payload["daily"]["precipitation_sum"][index]=amount
            result=history.summarize_history(payload,TARGET_START,TARGET_END)
            self.assertEqual(result["anomaly_mm"],expected_mm)
            self.assertEqual(result["anomaly_percent"],expected_percent)

    def test_zero_and_near_zero_baselines_do_not_produce_percentages(self):
        for amount in (0, .0001):
            payload = source()
            for index, stamp in enumerate(payload["daily"]["time"]):
                if stamp[:4] <= "2020":
                    payload["daily"]["precipitation_sum"][index] = amount
            result = history.summarize_history(payload, TARGET_START, TARGET_END)
            self.assertIsNone(result["anomaly_percent"])
            self.assertIsNotNone(result["anomaly_mm"])

    def test_partial_months_and_baseline_overlap_are_rejected(self):
        for start, end in [(date(2026,9,2), TARGET_END), (TARGET_START,date(2026,9,29)), (date(2020,9,1),date(2020,9,30))]:
            with self.subTest(start=start,end=end), self.assertRaises(ValueError):
                history.summarize_history(source(), start, end)

    def test_missing_dates_duplicate_dates_wrong_units_timezone_or_arrays_rejected(self):
        mutations = [
            lambda p:p["daily"]["time"].pop(),
            lambda p:p["daily"]["precipitation_sum"].pop(),
            lambda p:p["daily"]["time"].__setitem__(1,"1991-01-01"),
            lambda p:p["daily_units"].update(precipitation_sum="inch"),
            lambda p:p.update(utc_offset_seconds=3600),
            lambda p:p.update(timezone="America/Santiago"),
            lambda p:p.update(daily={}),
        ]
        for mutate in mutations:
            payload=source(); mutate(payload)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                history.summarize_history(payload,TARGET_START,TARGET_END)

    def test_invalid_and_missing_values_are_not_replaced(self):
        for value in (None, True, float("nan"), float("inf"), -1, 2001, "1"):
            payload=source(); payload["daily"]["precipitation_sum"][0]=value
            with self.subTest(value=value), self.assertRaises(ValueError):
                history.summarize_history(payload,TARGET_START,TARGET_END)

    def test_disabled_has_no_network_and_no_anomaly(self):
        with patch.dict(os.environ,{"SOLQUIRA_STUDIO_GEODATA":"0"}),patch.object(history,"_fetch",side_effect=AssertionError("No network")):
            result=history.historical_context({"latitude":-30.,"longitude":-70.75})
        self.assertEqual(result["history"]["state"],"disabled")
        self.assertNotIn("anomaly_mm",result["history"])

    def test_coordinates_are_validated_before_source_access(self):
        for value in ({"latitude":True,"longitude":0.},{"latitude":"1","longitude":0.},{"latitude":float("nan"),"longitude":0.},{"latitude":91.,"longitude":0.},{"latitude":1.,"longitude":2.,"url":"https://untrusted.invalid"}):
            with self.subTest(value=value),patch.object(history,"_fetch",side_effect=AssertionError("No network")),self.assertRaises(ValidationError):
                history.historical_context(value)

    def test_ready_context_has_units_periods_grid_and_provenance(self):
        with patch.object(history,"_fetch",return_value=source()) as fetch:
            result=history.historical_context({"latitude":-30.00001,"longitude":-70.75001})
        fetch.assert_called_once_with(-30.,-70.75,TARGET_START,TARGET_END)
        self.assertEqual(result["history"]["state"],"ready")
        self.assertEqual(result["history"]["model"],"ERA5")
        self.assertEqual(result["history"]["baseline"]["complete_years"],30)
        self.assertEqual(result["history"]["units"],"mm")
        self.assertEqual(result["history"]["kind"],"model_reanalysis")
        self.assertEqual(result["history"]["target_period"],{"start":"2026-09-01","end":"2026-09-30"})
        self.assertTrue(result["history"]["limitations"])

    def test_cache_preserves_retrieval_timestamp_and_returns_independent_copies(self):
        with patch.object(history,"_fetch",return_value=source()) as fetch:
            first=history.historical_context({"latitude":-30.,"longitude":-70.75})
            second=history.historical_context({"latitude":-30.000001,"longitude":-70.750001})
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(first["retrieved_at"],second["retrieved_at"])
        self.assertEqual(second["history"]["state"],"cached")
        second["history"]["baseline"]["complete_years"]=0
        self.assertEqual(first["history"]["baseline"]["complete_years"],30)

    def test_expired_cache_is_not_returned_as_current(self):
        with patch.object(history,"_fetch",return_value=source()) as fetch,patch.object(history,"MAX_REQUESTS_HOUR",2):
            history.historical_context({"latitude":-30.,"longitude":-70.75})
            key=next(iter(history._cache))
            stamp,saved=history._cache[key]
            history._cache[key]=(stamp-history.CACHE_TTL-1,saved)
            result=history.historical_context({"latitude":-30.,"longitude":-70.75})
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(result["history"]["state"],"ready")

    def test_failed_source_has_no_numbers_or_private_exception(self):
        for error in (ValueError("PRIVATE_SOURCE_MARKER"), httpx.ConnectError("PRIVATE_SOURCE_MARKER")):
            with patch.object(history,"_fetch",side_effect=error):
                result=history.historical_context({"latitude":-30.,"longitude":-70.75})
            self.assertEqual(result["history"]["state"],"unavailable")
            self.assertNotIn("anomaly_mm",result["history"])
            self.assertNotIn("PRIVATE_SOURCE_MARKER",json.dumps(result))
            self.assertFalse(history._inflight)

    def test_distant_source_grid_is_rejected(self):
        payload=source();payload["latitude"]=40.
        with patch.object(history,"_fetch",return_value=payload):
            result=history.historical_context({"latitude":-30.,"longitude":-70.75})
        self.assertEqual(result["history"]["state"],"unavailable")
        self.assertNotIn("anomaly_percent",result["history"])

    def test_rate_admission_and_cache_size_are_bounded(self):
        with patch.object(history,"MAX_REQUESTS_HOUR",2),patch.object(history,"MAX_CACHE",1),patch.object(history,"_fetch",return_value=source()) as fetch:
            history.historical_context({"latitude":-30.,"longitude":-70.75})
            history.historical_context({"latitude":-30.1,"longitude":-70.75})
            denied=history.historical_context({"latitude":-30.2,"longitude":-70.75})
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(len(history._cache),1)
        self.assertEqual(denied["history"]["state"],"unavailable")
        self.assertIn("limit",denied["history"]["note"])

    def test_rolling_daily_limit_is_separate_from_hourly_limit(self):
        now = history.time.monotonic()
        history._requests.extend([now-4000, now-8000, now-12000, now-16000])
        with patch.object(history,"_fetch",side_effect=AssertionError("No request beyond daily budget")):
            denied=history.historical_context({"latitude":-30.,"longitude":-70.75})
        self.assertEqual(denied["history"]["state"],"unavailable")
        self.assertIn("limit",denied["history"]["note"])

    def test_expired_admissions_leave_rolling_daily_window(self):
        history._requests.append(history.time.monotonic()-86401)
        with patch.object(history,"_fetch",return_value=source()) as fetch:
            result=history.historical_context({"latitude":-30.,"longitude":-70.75})
        self.assertEqual(result["history"]["state"],"ready")
        self.assertEqual(len(history._requests),1)
        self.assertEqual(fetch.call_count,1)

    def test_duplicate_inflight_context_does_not_trigger_another_request(self):
        seen=[]
        def fetch(*args):
            seen.append(history.historical_context({"latitude":-30.,"longitude":-70.75}))
            return source()
        with patch.object(history,"_fetch",side_effect=fetch) as mocked:
            result=history.historical_context({"latitude":-30.,"longitude":-70.75})
        self.assertEqual(mocked.call_count,1)
        self.assertEqual(result["history"]["state"],"ready")
        self.assertEqual(seen[0]["history"]["state"],"unavailable")
        self.assertEqual(len(history._requests),1)

    def test_transport_uses_fixed_model_utc_bounds_and_no_proxy_inheritance(self):
        real=httpx.Client;seen=[];options=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json=source())
        def client(**kw):
            options.append(kw)
            return real(**kw,transport=httpx.MockTransport(respond))
        with patch.object(history.httpx,"Client",side_effect=client):
            result=history._fetch(-30.,-70.75,TARGET_START,TARGET_END)
        self.assertEqual(result["daily_units"]["precipitation_sum"],"mm")
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0].url.host,"archive-api.open-meteo.com")
        self.assertEqual(seen[0].url.params["models"],"era5")
        self.assertEqual(seen[0].url.params["start_date"],"1991-01-01")
        self.assertEqual(seen[0].url.params["end_date"],"2026-09-30")
        self.assertEqual(seen[0].url.params["timezone"],"UTC")
        self.assertFalse(options[0]["trust_env"])
        self.assertFalse(options[0]["follow_redirects"])

    def test_redirects_oversize_and_nonfinite_json_are_rejected(self):
        real=httpx.Client
        for response in (httpx.Response(302,headers={"location":"https://untrusted.invalid"}),httpx.Response(200,content=b"x"*1_000_001),httpx.Response(200,content=b'{"bad":NaN}')):
            with self.subTest(status=response.status_code),patch.object(history.httpx,"Client",side_effect=lambda **kw:real(**kw,transport=httpx.MockTransport(lambda _:response))),self.assertRaises((httpx.HTTPError,ValueError)):
                history._fetch(-30.,-70.75,TARGET_START,TARGET_END)


if __name__ == "__main__":
    unittest.main()
