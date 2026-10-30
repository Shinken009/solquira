"""No real networks or coordinates: source parsing, opt-in and cache contracts."""
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from pydantic import ValidationError
import studio_geo as geo


def source():
    today = datetime.now(timezone.utc).date()
    return {"timezone":"GMT", "utc_offset_seconds":0, "latitude":-30.0,"longitude":-70.0,
            "daily_units":{"time":"iso8601","temperature_2m_max":"°C","temperature_2m_min":"°C","precipitation_sum":"mm","et0_fao_evapotranspiration":"mm"},
            "daily":{"time":[(today+timedelta(days=i)).isoformat() for i in range(7)],"temperature_2m_max":[25.0]*7,"temperature_2m_min":[12.0]*7,"precipitation_sum":[0.3]*7,"et0_fao_evapotranspiration":[3.2]*7}}


class GeoTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ, {"SOLQUIRA_STUDIO_GEODATA":"1"},clear=True);self.env.start();self.addCleanup(self.env.stop)
        geo._cache.clear();geo._requests.clear()

    def test_disabled_has_no_network_and_no_synthetic_forecast(self):
        with patch.dict(os.environ,{"SOLQUIRA_STUDIO_GEODATA":"0"}), patch.object(geo,"_fetch",side_effect=AssertionError("No network")):
            result=geo.location_context({"latitude":45.,"longitude":5.})
        self.assertEqual(result["weather"]["state"],"disabled")
        self.assertEqual(result["weather"]["days"],[])
        self.assertEqual(result["imagery"]["state"],"disabled")

    def test_strict_coordinate_validation_before_network(self):
        for value in ({"latitude":True,"longitude":0.},{"latitude":float('nan'),"longitude":0.},{"latitude":91.,"longitude":0.},{"latitude":"1","longitude":0.},{"latitude":1.,"longitude":2.,"url":"https://untrusted.invalid"}):
            with self.subTest(value=value),patch.object(geo,"_fetch",side_effect=AssertionError("No network")),self.assertRaises(ValidationError):geo.location_context(value)

    def test_current_seven_day_forecast_and_units(self):
        result=geo.parse_weather(source())
        self.assertEqual(result["kind"],"model_forecast")
        self.assertEqual(result["time_zone"],"UTC")
        self.assertEqual(len(result["days"]),7)
        self.assertEqual(result["days"][0]["reference_et0_mm"],3.2)

    def test_nasa_projection_and_polar_coverage_are_explicit(self):
        with patch.object(geo,'_fetch',return_value=geo.parse_weather(source())):
            middle=geo.location_context({'latitude':37.1,'longitude':-4.6})
            polar=geo.location_context({'latitude':89.,'longitude':0.})
        image=middle['imagery']
        self.assertEqual(image['projection'],'EPSG:3857')
        self.assertEqual(image['tile_matrix_set'],'GoogleMapsCompatible_Level9')
        self.assertIn('/epsg3857/',image['url_template'])
        self.assertEqual(image['max_level'],9)
        self.assertEqual(polar['imagery']['state'],'disabled')

    def test_invalid_units_lengths_and_offsets_rejected(self):
        for mutate in (lambda d:d['daily_units'].update(precipitation_sum='inch'), lambda d:d['daily']['time'].pop(),lambda d:d.update(utc_offset_seconds=3600),lambda d:d.update(timezone='America/Santiago')):
            payload=source();mutate(payload)
            with self.assertRaises(ValueError):geo.parse_weather(payload)

    def test_unknown_nonfinite_and_impossible_values_rejected(self):
        for value in (None,True,float('nan'),float('inf'),-1,3000):
            payload=source();payload['daily']['precipitation_sum'][0]=value
            with self.subTest(value=value),self.assertRaises(ValueError):geo.parse_weather(payload)
        payload=source();payload['daily']['temperature_2m_min'][0]=30
        with self.assertRaises(ValueError):geo.parse_weather(payload)

    def test_stale_or_duplicate_dates_are_not_current_forecasts(self):
        payload=source();payload['daily']['time'][1]=payload['daily']['time'][0]
        with self.assertRaises(ValueError):geo.parse_weather(payload)
        payload=source();payload['daily']['time']=[(datetime.now(timezone.utc).date()-timedelta(days=20-i)).isoformat() for i in range(7)]
        with self.assertRaises(ValueError):geo.parse_weather(payload)

    def test_rounding_cache_identity_and_original_timestamp(self):
        with patch.object(geo,'_fetch',return_value=geo.parse_weather(source())) as fetch:
            first=geo.location_context({'latitude':-30.123456,'longitude':-70.654321})
            second=geo.location_context({'latitude':-30.12346,'longitude':-70.65432})
        fetch.assert_called_once_with(-30.123,-70.654)
        self.assertEqual(second['weather']['state'],'cached')
        self.assertEqual(second['retrieved_at'],first['retrieved_at'])
        self.assertEqual(first['weather']['state'],'ready')
        second['weather']['days'][0]['precipitation_mm']=999
        self.assertNotEqual(first['weather']['days'][0]['precipitation_mm'],999)

    def test_cache_and_source_request_budgets_are_bounded(self):
        with patch.object(geo,'_fetch',return_value=geo.parse_weather(source())) as fetch,patch.object(geo,'MAX_CACHE',2),patch.object(geo,'MAX_REQUESTS_HOUR',3):
            for latitude in (1.,2.,3.):geo.location_context({'latitude':latitude,'longitude':4.})
            denied=geo.location_context({'latitude':5.,'longitude':4.})
        self.assertEqual(len(geo._cache),2)
        self.assertEqual(fetch.call_count,3)
        self.assertEqual(denied['weather']['state'],'unavailable')

    def test_failure_does_not_echo_source_exception_or_invent_data(self):
        with patch.object(geo,'_fetch',side_effect=ValueError('PRIVATE_SOURCE_MARKER')):
            value=geo.location_context({'latitude':1.,'longitude':2.})
        self.assertEqual(value['weather']['days'],[])
        self.assertNotIn('PRIVATE_SOURCE_MARKER',json.dumps(value))

    def test_transport_is_fixed_bounded_and_redirects_are_disabled(self):
        real=httpx.Client
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,json=source())
        with patch.object(geo.httpx,'Client',side_effect=lambda **kw:real(**kw,transport=httpx.MockTransport(respond))):
            result=geo._fetch(1.,2.)
        self.assertEqual(len(seen),1)
        self.assertEqual(seen[0].url.host,'api.open-meteo.com')
        self.assertEqual(seen[0].url.params['timezone'],'UTC')
        self.assertEqual(result['state'],'ready')

    def test_transport_rejects_oversize_and_redirects(self):
        real=httpx.Client
        for response in (httpx.Response(302,headers={'location':'https://untrusted.invalid'}),httpx.Response(200,content=b'x'*65537)):
            with self.subTest(status=response.status_code),patch.object(geo.httpx,'Client',side_effect=lambda **kw:real(**kw,transport=httpx.MockTransport(lambda _:response))):
                with self.assertRaises((httpx.HTTPError,ValueError)):geo._fetch(1.,2.)
