"""Offline regressions: compact wording, SSE, canonical references and one reserve."""
import json
import asyncio
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
import main
import studio_api
import studio_service
from dryland_brief import golden_payload, fallback_brief, compose_brief, MODEL
from dryland_progressive import ValidatedFields, parse_compact, compact_messages
from dryland_references import REFERENCE_SITES, reference_payload
from studio_budget import BudgetLedger, BudgetPolicy, PersistentBudgetProvider
from solquira_agent import NebiusProvider


def wording():
    return {'actions': [{'id': 'zai_pits', 'detail': 'Inspect safe pockets for returning rain.'},
                        {'id': 'living_cover', 'detail': 'Retain suitable residues over bare ground.'},
                        {'id': 'biocrust_check', 'detail': 'Protect living crust during your inspection.'}]}


class CompactTests(unittest.TestCase):
    def test_compact_reuses_all_server_properties(self):
        payload = golden_payload()
        brief = parse_compact(json.dumps(wording()), payload)
        original = fallback_brief(payload)
        self.assertEqual(brief.vital_signs, original.vital_signs)
        self.assertEqual(brief.pathways, original.pathways)
        self.assertEqual([a.id for a in brief.actions], [a.id for a in original.actions])
        self.assertTrue(all(not action.done for action in brief.actions))

    def test_partial_never_emits_before_complete_field(self):
        events = []
        stream = ValidatedFields(golden_payload(), lambda *v: events.append(v))
        raw = json.dumps(wording())
        for c in raw[:40]: stream.feed(c)
        self.assertEqual(events, [])
        for c in raw[40:]: stream.feed(c)
        self.assertEqual([e[0] for e in events], ['field'] * 3)

    def test_quantities_never_emitted(self):
        events = []
        stream = ValidatedFields(golden_payload(), lambda *v: events.append(v))
        data = wording(); data['actions'][0]['detail'] = 'Apply 40 kg over the ground.'
        stream.feed(json.dumps(data))
        self.assertEqual(events, [('degraded', {'path': 'brief', 'reason': 'rejected'})])
        with self.assertRaises(ValueError): parse_compact(json.dumps(data), golden_payload())

    def test_duplicate_and_unapproved_actions_rejected(self):
        for data in ( {'actions': [wording()['actions'][0]] * 3},
                     {'actions': [*wording()['actions'][:2], {'id':'half_moons','detail':'Inspect the contour carefully.'}]} ):
            with self.assertRaises(ValueError): parse_compact(json.dumps(data), golden_payload())
        with self.assertRaises(ValueError): parse_compact('{"actions":[],"actions":[]}', golden_payload())

    def test_prompt_has_no_coordinates_or_full_brief(self):
        data = json.loads(compact_messages(golden_payload())[1]['content'])
        self.assertNotIn('lat', data['context'])
        self.assertNotIn('shape_example', data)
        self.assertEqual(data['shape_fields'], ['actions', 'id', 'detail'])


class ProgressiveApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'SOLQUIRA_STUDIO_MODE':'1','SOLQUIRA_STUDIO_ALLOWED_HOSTS':'testserver'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        studio_api.ADMISSION.reset(); self.addCleanup(studio_api.ADMISSION.reset)
        self.client = TestClient(main.app); self.addCleanup(self.client.close)
        csrf = self.client.get('/solquira-api/studio/bootstrap').json()['csrf_token']
        self.headers = {'X-Solquira-CSRF': csrf, 'Accept':'text/event-stream'}
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.ledger = BudgetLedger(Path(self.temp.name) / 'test.sqlite3', BudgetPolicy('stream-test-only', 70000, 140000, 10, 20, 7000))
        self.ledger.initialize()

    def post(self, provider=None, place='ouagadougou'):
        payload = self.client.get('/v1/field-payload?scenario=dry&place='+place, headers=self.headers).json()
        with patch.object(studio_api, 'current_provider', return_value=provider):
            response = self.client.post('/v1/field-brief', headers=self.headers, json=payload)
        self.assertEqual(response.status_code, 200)
        frames = [(lines[0][7:], json.loads(lines[1][6:])) for lines in
                  (s.splitlines() for s in response.text.strip().split('\n\n'))]
        return response, frames

    def provider(self, malformed=False, truncated=False, legacy=False):
        self.requests = []
        data = wording()
        if malformed: data['actions'][1]['detail'] = 'Add 30 mm of water.'
        if legacy:
            data = fallback_brief(golden_payload()).model_dump(mode='json')
            data['actions'][0]['title'] = 'A model changed this title'
            data['actions'].reverse()
        raw = json.dumps(data)
        def transport(request):
            body = json.loads(request.content); self.requests.append(body)
            chunks = []
            for fragment in (raw[:80], raw[80:160], raw[160:]):
                chunks.append('data: '+json.dumps({'choices':[{'index':0,'delta':{'content':fragment},'finish_reason':None}]})+'\n\n')
            chunks += ['data: '+json.dumps({'choices':[{'index':0,'delta':{},'finish_reason':'length' if truncated else 'stop'}]})+'\n\n','data: [DONE]\n\n']
            return httpx.Response(200, text=''.join(chunks), headers={'Content-Type':'text/event-stream'})
        return PersistentBudgetProvider(NebiusProvider(enabled=True,api_key='test-value',model=MODEL,
                                                      transport=httpx.MockTransport(transport),disable_reasoning=True), self.ledger)

    def test_disabled_sse_has_complete_skeleton_and_final_no_reserve(self):
        response, events = self.post()
        self.assertEqual([e[0] for e in events], ['skeleton','degraded','final'])
        self.assertEqual(events[0][1]['brief'], events[-1][1]['brief'])
        self.assertEqual(response.headers['X-Accel-Buffering'], 'no')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.ledger.snapshot()['calls_used'], 0)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_success_stream_is_one_attempt_validated_fields_final(self):
        _, events = self.post(self.provider())
        self.assertEqual([e[0] for e in events], ['skeleton','field','field','field','final'])
        final = events[-1][1]
        self.assertEqual(final['brief']['source'], 'model')
        self.assertEqual(final['receipt']['reserved'], '1')
        self.assertEqual(final['receipt']['modelActions'], 3)
        self.assertEqual(len(final['receipt']['payloadHash']), 64)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.requests[0]['stream'])
        self.assertEqual(self.requests[0]['max_tokens'], 350)
        self.assertEqual(self.ledger.snapshot()['calls_used'], 1)
        self.assertEqual(studio_api.ADMISSION.active, 0)

    def test_rejected_field_final_rolls_back_every_preview(self):
        _, events = self.post(self.provider(malformed=True))
        fields = [e[1] for e in events if e[0]=='field']
        self.assertEqual(len(fields), 1)
        self.assertEqual(events[-1][1]['brief'], events[0][1]['brief'])
        self.assertEqual(events[-1][1]['receipt']['outcome'], 'invalid_output')
        self.assertEqual(self.ledger.snapshot()['calls_used'], 1)

    def test_truncated_stream_never_promotes_to_model(self):
        _, events = self.post(self.provider(truncated=True))
        self.assertEqual(events[-1][1]['brief']['source'], 'fallback')
        self.assertEqual(events[-1][1]['receipt']['reserved'], '1')

    def test_full_legacy_response_cannot_replace_server_owned_fields(self):
        _, events = self.post(self.provider(legacy=True))
        final = events[-1][1]
        self.assertEqual(final['brief'], events[0][1]['brief'])
        self.assertEqual(final['receipt']['outcome'], 'invalid_output')
        self.assertEqual(final['receipt']['modelActions'], 0)
        self.assertEqual(final['receipt']['reserved'], '1')
        self.assertEqual(self.ledger.snapshot()['calls_used'], 1)

    def test_reference_points_change_server_bands(self):
        pairs = set()
        for place in ('ouagadougou','andalusia','east_african_plateau'):
            _, events = self.post(place=place)
            final = events[-1][1]
            pairs.add((final['brief']['vital_signs']['soil_life'],final['brief']['vital_signs']['water_held']))
            self.assertEqual(final['receipt']['place'], place)
            self.assertEqual(final['receipt']['soil'], 'reference-table')
        self.assertEqual(len(pairs), 3)

    def test_sse_csrf_and_forged_payload_fail_before_inference(self):
        payload = golden_payload().model_dump(mode='json')
        self.assertEqual(self.client.post('/v1/field-brief', headers={'Accept':'text/event-stream'}, json=payload).status_code, 403)
        payload['soc_band'] = 'Strong'
        self.assertEqual(self.client.post('/v1/field-brief', headers=self.headers, json=payload).status_code, 409)


class StreamingLifetimeTests(unittest.TestCase):
    """Closing the HTTP iterator must not release a still-running paid worker."""

    def exercise(self, disconnect):
        entered, release, ended = threading.Event(), threading.Event(), threading.Event()

        class BlockedProvider:
            model, mode = MODEL, 'provider_stub'
            calls = 0

            def complete(self, messages, tools, *, deadline=None, max_tokens=None, on_delta=None):
                self.calls += 1
                entered.set()
                try:
                    if not release.wait(5):
                        raise RuntimeError('Test worker was not released.')
                    return {'content': json.dumps(wording())}
                finally:
                    ended.set()

        async def run(ledger):
            admission = studio_api.Admission()
            token, session, _ = admission.bootstrap(None, 'test-only')
            admission.acquire(token, session['csrf'])
            lease = studio_api.AdmissionLease(admission)
            raw_provider = BlockedProvider()
            provider = PersistentBudgetProvider(raw_provider, ledger)
            started = time.monotonic()
            response = studio_api._stream_brief(
                lease, golden_payload(), 'dry', provider, started,
                started + (3 if disconnect else 0.2),
            )
            iterator = response.body_iterator
            try:
                first = await anext(iterator)
                self.assertTrue(first.startswith('event: skeleton\n'))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                lease.finish_request()
                self.assertEqual(admission.active, 1)
                if disconnect:
                    await iterator.aclose()
                else:
                    frames = []
                    async def collect():
                        async for frame in iterator:
                            frames.append(frame)
                    await asyncio.wait_for(collect(), timeout=1)
                    self.assertLess(time.monotonic() - started, 1)
                    final = json.loads(frames[-1].split('data: ', 1)[1])
                    self.assertEqual(final['brief']['source'], 'fallback')
                    self.assertEqual(final['receipt']['outcome'], 'timeout')
                    self.assertEqual(final['receipt']['attempt'], 'in_flight')
                    self.assertEqual(final['receipt']['reserved'], '1')
                self.assertFalse(ended.is_set())
                self.assertEqual(admission.active, 1)
                self.assertEqual(raw_provider.calls, 1)
                self.assertEqual(ledger.snapshot()['calls_used'], 1)
            finally:
                release.set()
                await iterator.aclose()
                self.assertTrue(await asyncio.to_thread(ended.wait, 2))
                for _ in range(100):
                    if admission.active == 0:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(admission.active, 0)
                self.assertEqual(ledger.snapshot()['calls_used'], 1)

        with tempfile.TemporaryDirectory() as directory:
            ledger = BudgetLedger(Path(directory) / 'test.sqlite3',
                                  BudgetPolicy('lifetime-stream-test', 70000, 140000, 10, 20, 7000))
            ledger.initialize()
            asyncio.run(run(ledger))

    def test_disconnect_keeps_admission_until_worker_ends(self):
        self.exercise(disconnect=True)

    def test_deadline_final_keeps_admission_until_worker_ends(self):
        self.exercise(disconnect=False)


PARAPHRASES = {
    'zai_pits': 'Mark pocket sites before disturbing soil.',
    'half_moons': 'Trace contour flow before runoff concentrates.',
    'living_cover': 'Keep suitable residues over bare ground.',
    'mulch': 'Retain suitable residues over bare ground.',
    'biocrust_check': 'Inspect living crust and avoid trampling.',
    'delay_sowing': 'Check for returning rain and moist soil.',
}


def paraphrase_set(payload, overrides=None):
    baseline = fallback_brief(payload)
    chosen = [a for a in baseline.actions if a.id.value == 'biocrust_check'][:1]
    chosen += [a for a in baseline.actions if a.id.value != 'biocrust_check'][:2]
    details = dict(PARAPHRASES)
    details.update(overrides or {})
    return {'actions': [{'id': a.id.value, 'detail': details[a.id.value]}
                        for a in chosen]}


class GroundedWordingTests(unittest.TestCase):
    """Trusted Python meaning reaches the wording model; unsafe wording falls back."""

    def test_every_reference_case_carries_approved_meaning(self):
        for place in REFERENCE_SITES:
            for scenario in ('dry', 'rain'):
                payload = reference_payload(place, scenario)
                baseline = fallback_brief(payload)
                messages = compact_messages(payload)
                decoded = json.loads(messages[1]['content'])
                self.assertEqual(set(decoded['context']),
                                 {'soc_band', 'awc_band', 'rain_phrase', 'dry_days_ahead'})
                self.assertEqual([a['id'] for a in decoded['allowed_actions']],
                                 [a.id.value for a in baseline.actions])
                for record, action in zip(decoded['allowed_actions'], baseline.actions):
                    self.assertEqual(set(record), {'id', 'title', 'detail'})
                    self.assertEqual(record['title'], action.title)
                    self.assertEqual(record['detail'], action.detail)
                content = messages[1]['content']
                self.assertNotIn('lat', content)
                self.assertNotIn('lon', content)
                self.assertLess(len(content.encode('utf-8')), 2200)

    def test_valid_paraphrases_keep_python_owned_fields(self):
        for place in REFERENCE_SITES:
            for scenario in ('dry', 'rain'):
                payload = reference_payload(place, scenario)
                original = fallback_brief(payload)
                data = paraphrase_set(payload)
                self.assertEqual(len(data['actions']), 3)
                brief = parse_compact(json.dumps(data), payload)
                self.assertEqual(brief.source, 'model')
                self.assertEqual([a.id for a in brief.actions],
                                 [a.id for a in original.actions])
                self.assertEqual([a.labor for a in brief.actions],
                                 [a.labor for a in original.actions])
                self.assertEqual([a.title for a in brief.actions],
                                 [a.title for a in original.actions])
                self.assertEqual(brief.vital_signs, original.vital_signs)
                self.assertEqual(brief.pathways, original.pathways)
                self.assertTrue(all(not action.done for action in brief.actions))
                changed = {item['id'] for item in data['actions']}
                for action in brief.actions:
                    expected = PARAPHRASES[action.id.value] if action.id.value in changed \
                        else next(a.detail for a in original.actions if a.id == action.id)
                    self.assertEqual(action.detail, expected)

    def test_observed_temperature_and_threshold_claims_reject(self):
        cases = (('ouagadougou', 'dry', 'delay_sowing', 'Wait until soil warms enough'),
                 ('east_african_plateau', 'dry', 'delay_sowing',
                  'Wait until soil moisture improves'))
        for place, scenario, action_id, detail in cases:
            payload = reference_payload(place, scenario)
            with self.assertRaises(ValueError):
                parse_compact(json.dumps(paraphrase_set(payload, {action_id: detail})), payload)

    def test_observed_planning_to_fieldwork_escalation_rejects(self):
        cases = (('ouagadougou', 'dry', 'zai_pits', 'Dig shallow pits, fill with mulch'),
                 ('andalusia', 'rain', 'mulch', 'Spread mulch to shield crust'),
                 ('andalusia', 'rain', 'half_moons', 'Create half-moon depressions'))
        for place, scenario, action_id, detail in cases:
            payload = reference_payload(place, scenario)
            with self.assertRaises(ValueError):
                parse_compact(json.dumps(paraphrase_set(payload, {action_id: detail})), payload)

    def test_stream_preview_rolls_back_before_unsafe_detail(self):
        payload = reference_payload('ouagadougou', 'dry')
        events = []
        stream = ValidatedFields(payload, lambda *v: events.append(v))
        stream.feed(json.dumps(paraphrase_set(
            payload, {'zai_pits': 'Dig shallow pits, fill with mulch'})))
        self.assertEqual(events[-1], ('degraded', {'path': 'brief', 'reason': 'rejected'}))
        self.assertTrue(stream.closed)


if __name__ == '__main__': unittest.main()

