"""Offline field-context admission and grounded-agent contracts. No real providers."""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import main
import studio_api
from studio_api import Admission, COOKIE, CSRF_HEADER
from studio_service import explain_studio
from studio_brief import field_brief, weather_evidence

SCENARIO = {"profile_id": "valle", "water_percent": 100, "previous_crop": "cereal"}


def context(*, rain=0, latitude=37.1, longitude=-4.6):
    now = datetime.now(timezone.utc)
    return {"schema_version": "solquira.location/1", "context_ref": "slq-context-" + "A"*32,
            "location": {"latitude": latitude, "longitude": longitude, "precision_degrees": .001}, "retrieved_at": now.isoformat(),
            "weather": {"state": "ready", "source": "Open-Meteo", "kind": "model_forecast", "time_zone": "UTC",
                        "days": [{"date": (now.date()+timedelta(days=i)).isoformat(), "temperature_max_c": 28, "temperature_min_c": 12,
                                  "precipitation_mm": rain, "reference_et0_mm": 4} for i in range(7)]}, "imagery": {"state": "disabled"}}


class ContextAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.store = Admission(clock=lambda:self.now)
        self.token, _, _ = self.store.bootstrap(None, "test")

    def bind(self, value=None):
        return self.store.bind_context(self.token, self.store.begin_context(self.token), value or context())

    def test_owner_only_and_copied(self):
        ref = self.bind()
        returned = self.store.resolve_context(self.token, ref)
        returned["weather"]["days"].clear()
        self.assertEqual(len(self.store.resolve_context(self.token, ref)["weather"]["days"]), 7)
        other, _, _ = self.store.bootstrap(None, "second")
        for token in (other, None):
            with self.assertRaises(HTTPException) as failure:
                self.store.resolve_context(token, ref)
            self.assertEqual(failure.exception.status_code, 409)

    def test_expired_reference_is_rejected(self):
        ref = self.bind()
        self.now += 601
        with self.assertRaises(HTTPException): self.store.resolve_context(self.token, ref)
        self.assertNotIn("field_context",self.store.sessions[self.token])

    def test_cached_source_age_is_not_reset(self):
        value = context()
        value["retrieved_at"] = (datetime.now(timezone.utc)-timedelta(seconds=590)).isoformat()
        ref = self.bind(value)
        self.now += 11
        with self.assertRaises(HTTPException): self.store.resolve_context(self.token, ref)

    def test_source_older_than_ttl_cannot_be_bound(self):
        value = context()
        value["retrieved_at"] = (datetime.now(timezone.utc)-timedelta(seconds=601)).isoformat()
        self.assertIsNone(self.bind(value))

    def test_new_location_invalidates_previous_even_on_source_failure(self):
        ref = self.bind()
        value = context();value["weather"].update(state="unavailable",days=[])
        self.assertIsNone(self.bind(value))
        with self.assertRaises(HTTPException): self.store.resolve_context(self.token, ref)

    def test_older_request_finishing_late_cannot_replace_current(self):
        old = self.store.begin_context(self.token)
        current = self.store.begin_context(self.token)
        ref = self.store.bind_context(self.token, current, context(latitude=-.5))
        self.assertIsNone(self.store.bind_context(self.token, old, context()))
        self.assertEqual(self.store.resolve_context(self.token, ref)["location"]["latitude"], -.5)

    def test_new_reference_is_unpredictable_and_different(self):
        a, b = self.bind(), self.bind()
        self.assertNotEqual(a,b)
        self.assertGreater(len(a),32)
        with self.assertRaises(HTTPException): self.store.resolve_context(self.token,a)


class GroundedBriefTests(unittest.TestCase):
    def test_anticipation_is_server_calculated_not_reused_from_supplied_summary(self):
        value=context(rain=.3)
        value['anticipation']={'precipitation_total_mm':999999,'private':'PRIVATE_MARKER'}
        evidence=weather_evidence(value)
        anticipation=evidence['anticipation']
        self.assertEqual(anticipation['precipitation_total_mm'],2.1)
        self.assertEqual(anticipation['low_precipitation_days'],7)
        self.assertEqual(anticipation['longest_low_precipitation_run_days'],7)
        self.assertNotIn('PRIVATE_MARKER',json.dumps(evidence))
        self.assertNotIn('latitude',json.dumps(anticipation))
        self.assertNotIn('context_ref',json.dumps(anticipation))

    def test_anticipation_rejects_malformed_date_before_guidance(self):
        value=context();value['weather']['days'][0]['date']='not-a-day'
        with self.assertRaises(ValueError):
            weather_evidence(value)

    def test_field_brief_explains_low_precipitation_window_without_diagnosis(self):
        brief=field_brief(context(rain=.3),'water')
        self.assertIn('7 days are below',brief['summary'])
        self.assertIn('not a diagnosis of drought or desertification',brief['summary'])
        self.assertEqual(brief['forecast']['anticipation']['precipitation_episode_count'],0)

    def test_different_forecasts_change_actions_not_crop_math(self):
        dry = explain_studio(SCENARIO,"What about water?",field_context=context())
        wet = explain_studio(SCENARIO,"What about water?",field_context=context(rain=10,latitude=-.5))
        a,b = dry["report"]["field_brief"],wet["report"]["field_brief"]
        self.assertNotEqual(a["pattern"],b["pattern"])
        self.assertNotEqual(a["actions"][0]["title"],b["actions"][0]["title"])
        self.assertNotEqual(a["location"],b["location"])
        self.assertEqual(dry["report"]["plans"],wet["report"]["plans"])
        self.assertFalse(a["external_actions_executed"])
        self.assertEqual(len(dry["trace"]),6)

    def test_ground_cover_query_prioritizes_ground_cover(self):
        report = explain_studio(SCENARIO,"What should I check about soil cover?",field_context=context())["report"]
        self.assertEqual(report["field_brief"]["actions"][0]["id"],"ground-cover")

    def test_evidence_query_prioritizes_source_check(self):
        brief=field_brief(context(),"evidence")
        self.assertEqual(brief["actions"][0]["id"],"verify-forecast")
        ids={s["id"] for s in brief["sources"]}
        self.assertTrue(all(set(a["evidence_ids"])<=ids for a in brief["actions"]))

    def test_insufficient_inputs_are_requested_not_a_dose(self):
        brief=explain_studio(SCENARIO,"How much water should I apply?",field_context=context())["report"]["field_brief"]
        self.assertIn("Crop and growth stage",brief["missing_inputs"])
        self.assertIn("irrigation efficiency",brief["actions"][-1]["detail"])

    def test_default_question_does_not_include_context(self):
        result=explain_studio(SCENARIO,"Explain water")
        self.assertNotIn("field_brief",result["report"])
        self.assertFalse(any(t["tool"]=="read_landscape_context" for t in result["trace"]))

    def test_no_coordinates_or_user_payload_in_model_evidence(self):
        data=context()
        data["unexpected"]="PRIVATE_UNTRUSTED_VALUE"
        payload=json.dumps(weather_evidence(data))
        for text in ("latitude","longitude","context_ref","PRIVATE_UNTRUSTED_VALUE","37.1","-4.6"):
            self.assertNotIn(text,payload)

    def test_stub_receives_actual_geo_tool_before_response(self):
        test=self
        class Provider:
            mode="provider_stub";model="test-only"
            def complete(self,messages,tools,**kwargs):
                test.assertEqual(tools, [])  # Grounded evidence is complete: final composition only.
                results=[json.loads(m["content"]) for m in messages if m["role"]=="tool"]
                forecast=next(x for x in results if x.get("source")=="Open-Meteo")
                test.assertEqual(forecast["rain_mm"],21)
                test.assertNotIn("location",forecast)
                comparison=next(x for x in results if "comparison_id" in x)
                return {"content":json.dumps({"comparison_id":comparison["comparison_id"],"focus":"resilience", "synthesis": {
                    "statement_ids": ["forecast-dry"], "check_ids": ["observe-cover"],
                    "question_id": "crop-stage", "follow_up_id": "bring-observations"}})}
        result=explain_studio(SCENARIO,"What about ground cover?",provider=Provider(),field_context=context(rain=3))
        self.assertEqual(result["outcome"],"success")
        self.assertEqual(result["inference_mode"],"provider_stub")
        self.assertEqual(result["report"]["field_brief"]["focus"],"resilience")
        self.assertEqual(len(result["trace"]),6)

    def test_provider_failure_preserves_source_brief_and_labels(self):
        class Provider:
            mode="provider_stub";model="test-only"
            def complete(self,*args,**kwargs):raise TimeoutError("PRIVATE_FAILURE")
        result=explain_studio(SCENARIO,"water",provider=Provider(),field_context=context())
        self.assertEqual(result["inference_mode"],"local_fallback")
        self.assertEqual(result["report"]["field_brief"]["forecast"]["rain_mm"],0)
        self.assertNotIn("PRIVATE_FAILURE",json.dumps(result))


class ContextApiTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{"SOLQUIRA_STUDIO_MODE":"1","SOLQUIRA_STUDIO_GEODATA":"1","SOLQUIRA_STUDIO_ALLOWED_HOSTS":"testserver"},clear=True)
        self.env.start();self.addCleanup(self.env.stop)
        studio_api.ADMISSION.reset();self.addCleanup(studio_api.ADMISSION.reset)
        self.network=patch("httpx.HTTPTransport.handle_request",side_effect=AssertionError("No external network"))
        self.network.start();self.addCleanup(self.network.stop)
        self.client=TestClient(main.app);self.addCleanup(self.client.close)
        self.csrf=self.client.get("/solquira-api/studio/bootstrap").json()["csrf_token"]

    def post(self,path,body):
        return self.client.post("/solquira-api/studio/"+path,json=body,headers={CSRF_HEADER:self.csrf})

    def load(self):
        with patch("studio_geo.location_context",return_value=context()):
            return self.post("location",{"latitude":37.1,"longitude":-4.6}).json()["context_ref"]

    def test_location_ref_is_bound_then_used_without_client_weather(self):
        ref=self.load()
        response=self.post("agent",{"scenario":SCENARIO,"query":"soil cover","context_ref":ref})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["report"]["field_brief"]["context_ref"],ref)
        self.assertEqual(response.headers["cache-control"],"no-store")

    def test_location_api_exposes_deterministic_anticipation_without_a_model(self):
        from test_studio_geo import source
        from studio_geo import parse_weather
        with patch('studio_geo._fetch',return_value=parse_weather(source())), patch('studio_api.current_provider') as provider:
            response=self.post('location',{'latitude':37.1,'longitude':-4.6})
        self.assertEqual(response.status_code,200)
        anticipation=response.json()['anticipation']
        self.assertEqual(anticipation['state'],'ready')
        self.assertEqual(anticipation['precipitation_total_mm'],2.1)
        self.assertEqual(anticipation['low_precipitation_days'],7)
        self.assertEqual(anticipation['units']['precipitation'],'mm')
        provider.assert_not_called()

    def test_unknown_and_cross_session_refs_are_rejected_before_provider(self):
        ref=self.load()
        self.client.cookies.clear()
        self.csrf=self.client.get("/solquira-api/studio/bootstrap").json()["csrf_token"]
        with patch("studio_api.current_provider") as provider:
            response=self.post("agent",{"scenario":SCENARIO,"query":"water","context_ref":ref})
            self.assertEqual(response.status_code,409)
            provider.assert_not_called()

    def test_unavailable_forecast_returns_no_attachable_context(self):
        value=context();value["weather"].update(state="unavailable",days=[])
        with patch("studio_geo.location_context",return_value=value):
            response=self.post("location",{"latitude":37.1,"longitude":-4.6})
        self.assertIsNone(response.json()["context_ref"])

    def test_forged_weather_not_accepted_or_echoed(self):
        response=self.post("agent",{"scenario":SCENARIO,"query":"water","weather":{"private":"DO_NOT_ECHO"}})
        self.assertEqual(response.status_code,422)
        self.assertNotIn("DO_NOT_ECHO",response.text)

    def test_context_change_during_agent_execution_rejects_old_result(self):
        ref=self.load()
        original=studio_api._service().explain_studio
        def change(*args,**kwargs):
            studio_api.ADMISSION.begin_context(self.client.cookies.get(COOKIE))
            return original(*args,**kwargs)
        with patch("studio_service.explain_studio",side_effect=change):
            response=self.post("agent",{"scenario":SCENARIO,"query":"water","context_ref":ref})
        self.assertEqual(response.status_code,409)


if __name__=="__main__":unittest.main()
