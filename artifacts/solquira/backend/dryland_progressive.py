"""Compact model wording and validated field delivery. No repaired JSON is evidence."""
from __future__ import annotations

import json
import re
from pydantic import Field, field_validator
from dryland_brief import ActionId, ActionItem, Contract, FieldBrief, FieldPayload, fallback_brief, words_only


class ActionWording(Contract):
    id: ActionId
    detail: str = Field(min_length=8, max_length=80)
    _words = field_validator('detail')(words_only)


class ModelWording(Contract):
    actions: list[ActionWording] = Field(min_length=3, max_length=3)

    @field_validator('actions')
    @classmethod
    def unique(cls, values):
        if len({item.id for item in values}) != len(values):
            raise ValueError('Duplicate action.')
        if ActionId.biocrust_check not in {item.id for item in values}:
            raise ValueError('Keep the surface protection check.')
        return values


COMPACT_PROMPT = """Write concise field-check wording for a dryland grower. Return only JSON.
Schema: {"actions":[{"id":"action_id","detail":"short field instruction"}]}.
Return exactly three distinct actions from allowed_actions. Include biocrust_check.
Each detail paraphrases only its supplied approved action meaning; never add another practice.
Keep planning as planning and inspection as inspection; never turn them into digging, construction or other field work.
Preserve prerequisites exactly: the sowing-wait action needs returning rain AND an observed soil condition.
Never invent soil temperature, dates, thresholds, measured moisture readings, pests, materials, quantities or promised benefits.
Each detail uses at most eight words and eighty characters. Aim below one hundred ninety output tokens.
Do not output titles, done, labor, bands, coordinates, crop pathways or source: Python owns those.
No digits, spelled-out quantities, units, chemicals, brands, links, yields or promises.
Describe inspections and preparation, not a claim of measured suitability. Protect living crust.
Use no outside knowledge; the supplied reference example is the only evidence.
The context is an explicitly declared reference example, not a SoilGrids or FAO observation.
No prose outside JSON. No markdown."""


# Bounded guard for the two observed content defects. It is a small word list,
# not semantic truth detection: an unlisted condition or practice can still slip
# through, so the prompt remains the primary control.
_UNSUPPORTED_CONDITION = re.compile(
    r'\b(?:temperature|warm(?:er|s|th|ing)?|heat(?:ed|ing)?|frost|cold(?:er)?'
    r'|improv(?:e|es|ed|ing|ement)|slightly|enough|threshold)\b',
    re.I,
)
_ESCALATING_WORK = re.compile(
    r'\b(?:dig|dug|excavat\w*|construct\w*|build|install\w*|create|plough|plow'
    r'|till\w*|spread|apply|fill\w*|earthwork\w*|grad(?:e|ing))\b',
    re.I,
)


def grounded_detail(detail: str, baseline: ActionItem) -> str:
    """Reject an unmeasured condition or work the baseline did not authorise."""
    if _UNSUPPORTED_CONDITION.search(detail):
        raise ValueError('Unsupported measured condition.')
    approved = f'{baseline.title} {baseline.detail}'.lower()
    for verb in _ESCALATING_WORK.findall(detail):
        if verb.lower() not in approved:
            raise ValueError('Do not turn planning or inspection into field work.')
    return detail


def strict_loads(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Duplicate field.')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite.')))


def compact_messages(payload: FieldPayload):
    baseline = fallback_brief(payload)
    return [
        {'role': 'system', 'content': COMPACT_PROMPT},
        {'role': 'user', 'content': json.dumps({
            'context': payload.model_dump(mode='json', exclude={'lat', 'lon', 'fao_crops'}),
            # Trusted Python meaning, not just ids: the wording model must
            # keep approved prerequisites and practices.
            'allowed_actions': [
                {'id': action.id.value, 'title': action.title, 'detail': action.detail}
                for action in baseline.actions
            ],
            'shape_fields': ['actions', 'id', 'detail'],
        }, ensure_ascii=False, separators=(',', ':'))},
    ]


def parse_compact(raw: str, payload: FieldPayload) -> FieldBrief:
    if not isinstance(raw, str) or len(raw) > 16000:
        raise ValueError('Invalid model result.')
    wording = ModelWording.model_validate(strict_loads(raw))
    baseline = fallback_brief(payload)
    approved = {item.id for item in baseline.actions}
    if any(item.id not in approved for item in wording.actions):
        raise ValueError('Action is outside the reference policy.')
    details = {item.id: item.detail for item in wording.actions}
    for action in baseline.actions:
        if action.id in details:
            grounded_detail(details[action.id], action)
    for action in baseline.actions:
        if action.id in details:
            action.detail = details[action.id]
    baseline.source = 'model'
    return baseline


class ValidatedFields:
    """Read only complete JSON action objects; never repair partial values.

    These events are previews. The complete strict parser can still reject a
    later duplicate key, truncated array or trailing content; final then wins.
    """
    def __init__(self, payload, emit):
        self.payload, self.emit = payload, emit
        self.buffer, self.offset, self.started, self.closed = '', 0, False, False
        self.seen = set()
        self.baseline = fallback_brief(payload)
        self.decoder = json.JSONDecoder()

    def feed(self, delta):
        if self.closed:
            return
        if not isinstance(delta, str) or len(self.buffer) + len(delta) > 16000:
            self.closed = True
            return
        self.buffer += delta
        if not self.started:
            match = re.match(r'^\s*\{\s*"actions"\s*:\s*\[', self.buffer)
            if not match:
                return
            self.offset, self.started = match.end(), True
        while len(self.seen) < 3:
            while self.offset < len(self.buffer) and self.buffer[self.offset] in ' \r\n\t,':
                self.offset += 1
            if self.offset >= len(self.buffer):
                return
            if self.buffer[self.offset] == ']':
                self.closed = True
                return
            try:
                _, end = self.decoder.raw_decode(self.buffer, self.offset)
            except ValueError:
                return  # Incomplete object: no event and no guessed data.
            raw = self.buffer[self.offset:end]
            self.offset = end
            try:
                item = ActionWording.model_validate(strict_loads(raw))
                if item.id in self.seen:
                    raise ValueError('Duplicate action.')
                index = next(i for i, action in enumerate(self.baseline.actions) if action.id == item.id)
                grounded_detail(item.detail, self.baseline.actions[index])
                self.seen.add(item.id)
                action = self.baseline.actions[index].model_copy(update={'detail': item.detail})
                self.emit('field', {'path': f'actions.{index}', 'value': action.model_dump(mode='json'),
                                    'validated': True, 'wording': 'model'})
            except (ValueError, StopIteration):
                self.emit('degraded', {'path': 'brief', 'reason': 'rejected'})
                self.closed = True
                return
