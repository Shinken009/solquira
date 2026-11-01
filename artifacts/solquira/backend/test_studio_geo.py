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
        self.assertEqual(result["anticipation"]["state"], "unavailable")
        self.assertNotIn("precipitation_total_mm", result["anticipation"])

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
        self.assertEqual(first['anticipation'], second['anticipation'])
        self.assertEqual(first['anticipation']['retrieved_at'], first['retrieved_at'])
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
        self.assertEqual(value['anticipation']['state'],'unavailable')

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


class AnticipationTests(unittest.TestCase):
    """Descriptive forecast fixtures, not observed environmental validation."""

    def indicators(self, precipitation, et0=None):
        payload = source()
        payload['daily']['precipitation_sum'] = precipitation
        if et0 is not None:
            payload['daily']['et0_fao_evapotranspiration'] = et0
        return geo.forecast_anticipation(geo.parse_weather(payload), datetime.now(timezone.utc).isoformat())

    def test_episode_and_concentration_calculations_are_distinct(self):
        result = self.indicators([2,3,0,4,0,1,1])
        self.assertEqual(result['state'],'ready')
        self.assertEqual(result['precipitation_total_mm'],11)
        self.assertEqual(result['reference_et0_total_mm'],22.4)
        self.assertEqual(result['precipitation_minus_et0_mm'],-11.4)
        self.assertEqual(result['low_precipitation_days'],2)
        self.assertEqual(result['longest_low_precipitation_run_days'],1)
        self.assertEqual(result['precipitation_days'],5)
        self.assertEqual(result['precipitation_episode_count'],3)
        self.assertEqual(result['largest_episode_total_mm'],5)
        self.assertEqual(result['largest_episode_share_percent'],45.5)
        self.assertEqual(result['maximum_daily_precipitation_mm'],4)
        self.assertEqual(result['wettest_day_share_percent'],36.4)
        self.assertEqual(result['wettest_day_date'],(datetime.now(timezone.utc).date()+timedelta(days=3)).isoformat())

    def test_zero_precipitation_and_zero_et0_never_divide_or_invent_an_episode(self):
        result = self.indicators([0]*7,[0]*7)
        self.assertEqual(result['precipitation_minus_et0_mm'],0)
        self.assertEqual(result['low_precipitation_days'],7)
        self.assertEqual(result['longest_low_precipitation_run_days'],7)
        self.assertEqual(result['precipitation_episode_count'],0)
        self.assertIsNone(result['wettest_day_date'])
        self.assertIsNone(result['wettest_day_share_percent'])
        self.assertIsNone(result['largest_episode_share_percent'])

    def test_trace_precipitation_is_in_total_but_not_a_wet_episode(self):
        result = self.indicators([.1]*7)
        self.assertEqual(result['precipitation_total_mm'],.7)
        self.assertEqual(result['low_precipitation_days'],7)
        self.assertEqual(result['precipitation_days'],0)
        self.assertEqual(result['precipitation_episode_count'],0)
        self.assertEqual(result['largest_episode_total_mm'],0)
        self.assertEqual(result['wettest_day_share_percent'],14.3)
        self.assertIsNone(result['largest_episode_share_percent'])

    def test_one_millimetre_boundary_is_declared_and_inclusive_for_wet_days(self):
        result = self.indicators([.999,1,1.001,0,.3,1,0])
        self.assertEqual(result['thresholds']['low_precipitation_mm_per_day'],1)
        self.assertEqual(result['thresholds']['wet_episode_min_mm_per_day'],1)
        self.assertEqual(result['low_precipitation_days'],4)
        self.assertEqual(result['longest_low_precipitation_run_days'],2)
        self.assertEqual(result['precipitation_episode_count'],2)

    def test_one_episode_at_window_edges_and_earliest_tied_wettest_day(self):
        result = self.indicators([1]*7)
        self.assertEqual(result['precipitation_episode_count'],1)
        self.assertEqual(result['largest_episode_total_mm'],7)
        self.assertEqual(result['largest_episode_share_percent'],100)
        self.assertEqual(result['longest_low_precipitation_run_days'],0)
        self.assertEqual(result['wettest_day_date'],result['period_start'])

    def test_temperature_extremes_and_complete_provenance(self):
        weather = geo.parse_weather(source())
        weather['days'][0]['temperature_min_c']=-2
        weather['days'][-1]['temperature_max_c']=37
        stamp=datetime.now(timezone.utc).isoformat()
        result=geo.forecast_anticipation(weather,stamp)
        self.assertEqual(result['temperature_min_c'],-2)
        self.assertEqual(result['temperature_max_c'],37)
        self.assertEqual(result['period_start'],weather['days'][0]['date'])
        self.assertEqual(result['period_end'],weather['days'][-1]['date'])
        self.assertEqual(result['retrieved_at'],stamp)
        self.assertEqual(result['time_zone'],'UTC')
        self.assertEqual(result['source'],'Open-Meteo')
        self.assertEqual(result['units']['temperature'],'°C')
        self.assertEqual(result['units']['precipitation_minus_et0'],'mm')
        limits=' '.join(result['limitations']).lower()
        for boundary in ('not soil moisture','desertification','snow','hourly intensity','truncated'):
            self.assertIn(boundary,limits)
        self.assertTrue(result['missing_inputs'])

    def test_incomplete_or_invalid_context_has_no_partial_indicators(self):
        for mutate in (lambda w:w['days'].pop(), lambda w:w.update(state='unavailable'),
                       lambda w:w.update(time_zone='local'), lambda w:w.update(kind='observation'),
                       lambda w:w['days'][1].update(date=w['days'][0]['date']),
                       lambda w:w['days'][0].update(temperature_min_c=40),
                       lambda w:w['days'][0].update(precipitation_mm=None),
                       lambda w:w['days'][0].update(reference_et0_mm=True),
                       lambda w:w['days'][0].update(precipitation_mm=float('nan'))):
            weather=geo.parse_weather(source());mutate(weather)
            result=geo.forecast_anticipation(weather,datetime.now(timezone.utc).isoformat())
            self.assertEqual(result['state'],'unavailable')
            self.assertNotIn('precipitation_total_mm',result)
            self.assertIsNone(result['period_start'])

    def test_invalid_or_ambiguous_retrieval_time_is_not_relabelled_utc(self):
        for stamp in (None, 'PRIVATE_MARKER', '2026-11-01', '2026-11-01T00:00:00+03:00'):
            result=geo.forecast_anticipation(geo.parse_weather(source()),stamp)
            self.assertEqual(result['state'],'unavailable')
            self.assertIsNone(result['retrieved_at'])
            self.assertNotIn('PRIVATE_MARKER',json.dumps(result))
