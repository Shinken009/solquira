"""Typed, bounded dryland brief; calculations and example provenance stay in Python.

The release fixture is not a SoilGrids observation or a live FAO crop query.
No coordinate, credential, private field note or raw provider error is logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from studio_runtime import NANO_MODEL

MODEL = NANO_MODEL
TIMEOUT_S = 3.0
MAX_TOKENS = 350


class SoilLife(str, Enum):
    strong = "Strong"
    building = "Building"
    weak = "Weak"


class WaterHeld(str, Enum):
    good = "Good"
    fair = "Fair"
    low = "Low"


class LaborLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ActionId(str, Enum):
    zai_pits = "zai_pits"
    half_moons = "half_moons"
    living_cover = "living_cover"
    mulch = "mulch"
    biocrust_check = "biocrust_check"
    delay_sowing = "delay_sowing"


class PathwayId(str, Enum):
    sorghum_cowpea = "sorghum_cowpea"
    millet_cowpea = "millet_cowpea"
    quinoa = "quinoa"
    opuntia = "opuntia"
    amaranth = "amaranth"


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_assignment=True
    )


_NUMBER_WORDS = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|dozen|hundred|thousand"
    r"|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|cien|mil"
)
_UNIT_WORDS = (
    r"percent|percentages?|hectares?|ha|kilograms?|kgs?|grams?|milligrams?|mg"
    r"|millimet(?:er|re)s?|mm|centimet(?:er|re)s?|cm|litres?|liters?|ml|ppm|quintals?"
)
_CHEMICAL_WORDS = (
    r"pesticides?|herbicides?|fungicides?|insecticides?|plaguicidas?|herbicidas?"
    r"|fungicidas?|insecticidas?|glyphosate|glifosato|atrazine|atrazina|paraquat"
    r"|roundup|dicamba|chlorpyrifos|imidacloprid|mancozeb|malathion|2,4-d"
)
# Bounded redaction list: spelled-out numbers, units and agrochemical names in
# English and Spanish, plus links and control characters. It is a word list, not
# semantic truth detection: an unlisted brand name can still slip through.
_TEXT_GUARD = re.compile(
    rf"\b(?:{_NUMBER_WORDS}|{_UNIT_WORDS}|{_CHEMICAL_WORDS})\b|https?://|www\.|[%<>{{}}]",
    re.I,
)


def words_only(value: str) -> str:
    # Keep numerical prescriptions, units, agrochemical names and links out of
    # the redaction surface.
    if any(c.isdigit() or ord(c) < 32 for c in value) or _TEXT_GUARD.search(value):
        raise ValueError(
            "Use concise words without measurements, chemical prescriptions or links."
        )
    return value


class VitalSigns(Contract):
    soil_life: SoilLife
    water_held: WaterHeld
    rain_outlook: str = Field(min_length=8, max_length=72)
    _words = field_validator("rain_outlook")(words_only)


class ActionItem(Contract):
    id: ActionId
    title: str = Field(min_length=4, max_length=48)
    detail: str = Field(min_length=8, max_length=80)
    labor: LaborLevel
    done: bool = Field(default=False, strict=True)
    _words = field_validator("title", "detail")(words_only)


class CropPathway(Contract):
    id: PathwayId
    name: str = Field(min_length=3, max_length=40)
    why: str = Field(min_length=8, max_length=70)
    water_note: str = Field(min_length=8, max_length=60)
    _words = field_validator("name", "why", "water_note")(words_only)


class FieldBrief(Contract):
    source: Literal["model", "fallback"] = "model"
    vital_signs: VitalSigns
    actions: list[ActionItem] = Field(min_length=3, max_length=5)
    pathways: list[CropPathway] = Field(min_length=3, max_length=3)

    @field_validator("actions", "pathways")
    @classmethod
    def unique_ids(cls, items):
        if len({item.id for item in items}) != len(items):
            raise ValueError("Use unique item identities.")
        return items


class FieldPayload(Contract):
    """Server-owned envelope, posted back unchanged; never trusted from the browser."""

    lat: float = Field(ge=-90, le=90, strict=True)
    lon: float = Field(ge=-180, le=180, strict=True)
    soc_band: SoilLife
    awc_band: WaterHeld
    rain_phrase: str = Field(min_length=8, max_length=72)
    dry_days_ahead: int = Field(ge=0, le=7, strict=True)
    fao_crops: list[PathwayId] = Field(default_factory=list, max_length=5)
    _words = field_validator("rain_phrase")(words_only)

    @field_validator("fao_crops")
    @classmethod
    def sufficient_candidates(cls, values):
        if len(values) != len(set(values)) or (values and len(values) < 3):
            raise ValueError(
                "Supply no filter or at least three unique approved pathways."
            )
        return values


def _finite_nonnegative(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("A finite, nonnegative supported measurement is required.")
    return value


def band_soc(soc_g_per_kg: float | None) -> SoilLife:
    value = _finite_nonnegative(soc_g_per_kg)
    return (
        SoilLife.strong
        if value >= 15
        else SoilLife.building
        if value >= 8
        else SoilLife.weak
    )


def band_awc(awc_mm: float | None) -> WaterHeld:
    value = _finite_nonnegative(awc_mm)
    return (
        WaterHeld.good
        if value >= 120
        else WaterHeld.fair
        if value >= 60
        else WaterHeld.low
    )


def available_water_capacity(
    field_capacity: float, wilting_point: float, depth_mm: float
) -> float:
    """Volumetric fraction difference × depth; not a SoilGrids pedotransfer model."""
    fc, wp, depth = map(_finite_nonnegative, (field_capacity, wilting_point, depth_mm))
    if not 0 <= wp <= fc <= 1 or depth == 0:
        raise ValueError("Check soil water fractions and profile depth.")
    return (fc - wp) * depth


def golden_payload(scenario: str = "dry", place: str = "ouagadougou") -> FieldPayload:
    from dryland_references import reference_payload
    return reference_payload(place, scenario)


def _legacy_golden_payload(scenario: str = "dry") -> FieldPayload:
    if scenario not in {"dry", "rain"}:
        raise ValueError("Choose the dry or rain example.")
    # Fixed, reproducible example values; no unverified network source labels.
    return FieldPayload(
        lat=12.371,
        lon=-1.519,
        soc_band=band_soc(6.0),
        awc_band=band_awc(available_water_capacity(0.23, 0.11, 600)),
        rain_phrase="Dry week ahead — prepare water pockets"
        if scenario == "dry"
        else "Rain may return — inspect water pathways",
        dry_days_ahead=6 if scenario == "dry" else 1,
        fao_crops=[
            PathwayId.sorghum_cowpea,
            PathwayId.millet_cowpea,
            PathwayId.amaranth,
        ],
    )


def resolve_payload(payload: FieldPayload) -> tuple[str, FieldPayload]:
    from dryland_references import resolve_reference_payload
    _place, scenario, canonical = resolve_reference_payload(payload)
    return scenario, canonical


_PATHWAYS = {
    PathwayId.sorghum_cowpea: (
        "Sorghum + cowpea",
        "Explore cereal and legume diversity.",
        "Sparing after establishment.",
    ),
    PathwayId.millet_cowpea: (
        "Millet + cowpea",
        "Explore a short-season cereal-legume pairing.",
        "Protect moisture during establishment.",
    ),
    PathwayId.quinoa: (
        "Quinoa",
        "Compare locally adapted varieties.",
        "Avoid waterlogged ground.",
    ),
    PathwayId.opuntia: (
        "Opuntia rows",
        "Explore a perennial boundary pathway.",
        "Rooting needs care before dry-season resilience.",
    ),
    PathwayId.amaranth: (
        "Amaranth",
        "Compare a diverse grain and leaf pathway.",
        "Protect moisture while seedlings establish.",
    ),
}


def fallback_brief(payload: FieldPayload) -> FieldBrief:
    dry = payload.awc_band == WaterHeld.low or payload.dry_days_ahead >= 4
    weak = payload.soc_band == SoilLife.weak
    cover = ActionItem(
        id=ActionId.living_cover if weak else ActionId.mulch,
        title="Protect bare soil",
        detail="Inspect existing cover and retain suitable plant residues.",
        labor=LaborLevel.low,
    )
    crust = ActionItem(
        id=ActionId.biocrust_check,
        title="Look for living crust",
        detail="Keep dark or lichen-covered surface patches undisturbed.",
        labor=LaborLevel.low,
    )
    water = ActionItem(
        id=ActionId.zai_pits if dry else ActionId.half_moons,
        title="Plan water pockets" if dry else "Inspect contour pathways",
        detail="Mark candidate water-catching pockets before disturbing soil."
        if dry
        else "Trace where rain can soak in without concentrating runoff.",
        labor=LaborLevel.medium,
    )
    actions = [water, cover, crust] if dry else [cover, crust, water]
    if "dry week" in payload.rain_phrase.lower():
        actions.insert(
            1,
            ActionItem(
                id=ActionId.delay_sowing,
                title="Wait for a planting window",
                detail="Check for returning rain and moist soil before sowing.",
                labor=LaborLevel.low,
            ),
        )
    candidates = payload.fao_crops or list(PathwayId)
    return FieldBrief(
        source="fallback",
        vital_signs=VitalSigns(
            soil_life=payload.soc_band,
            water_held=payload.awc_band,
            rain_outlook=payload.rain_phrase,
        ),
        actions=actions,
        pathways=[
            CropPathway(
                id=key,
                name=_PATHWAYS[key][0],
                why=_PATHWAYS[key][1],
                water_note=_PATHWAYS[key][2],
            )
            for key in candidates[:3]
        ],
    )


SYSTEM_PROMPT = """You are a Dryland Field Agronomist for smallholders. Reply with one compact JSON object matching the schema. No prose or markdown.
The server provides a deterministic example payload, not measured SoilGrids or FAO observations. Do not recompute or quote numbers, units, yields, prices, chemicals, or brands.
Copy payload.soc_band into vital_signs.soil_life, payload.awc_band into water_held, and payload.rain_phrase into rain_outlook.
actions: three to five unique ids from zai_pits, half_moons, living_cover, mulch, biocrust_check, delay_sowing. Always include biocrust_check. Prefer zai_pits first for low water or many dry days. Prefer living_cover for weak soil. Use delay_sowing only for a dry week. Each has id,title,detail,labor (low|medium|high),done:false. Keep titles and details very short.
pathways: exactly three unique ids selected ONLY from payload.fao_crops when nonempty; otherwise from sorghum_cowpea,millet_cowpea,quinoa,opuntia,amaranth. Each has id,name,why,water_note. Keep the reason and water trade-off to a few words each, with no measurements.
source must be model. No caveats, citations, or identity statements. Stay within the output limit."""


def parse_brief(raw: str, payload: FieldPayload) -> FieldBrief:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate model fields.")
            result[key] = value
        return result

    if not isinstance(raw, str) or len(raw) > 16000:
        raise ValueError("Invalid model result.")
    data = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("Non-finite output.")
        ),
    )
    brief = FieldBrief.model_validate(data)
    allowed = set(payload.fao_crops or list(PathwayId))
    if any(item.id not in allowed for item in brief.pathways):
        raise ValueError("A pathway escaped the approved catalog.")
    action_ids = {item.id for item in brief.actions}
    if ActionId.biocrust_check not in action_ids or (
        ActionId.delay_sowing in action_ids
        and "dry week" not in payload.rain_phrase.lower()
    ):
        raise ValueError("An action escaped the evidence policy.")
    # A syntactically valid model never owns observations or checked state.
    brief.vital_signs.soil_life = payload.soc_band
    brief.vital_signs.water_held = payload.awc_band
    brief.vital_signs.rain_outlook = payload.rain_phrase
    brief.source = "model"
    for item in brief.actions:
        item.done = False
    return brief


@dataclass(frozen=True)
class Attempt:
    """What the receipt may honestly say about one composition.

    `state` is derived from observed transitions, never assumed: a rejected
    answer (paid, then failed validation) is not the same fact as no request.
    """

    mode: str
    transitions: tuple[str, ...] = ()
    reserved: bool | None = False

    @property
    def state(self) -> str:
        if "validated" in self.transitions:
            return "returned"
        if "rejected" in self.transitions:
            return "rejected"
        if "route_timeout" in self.transitions:
            return "in_flight"
        if self.reserved:
            return "reserved"
        return "none"

    def header_values(self) -> dict[str, str]:
        reserved = {True: "1", False: "0"}.get(self.reserved, "unknown")
        return {
            "X-Solquira-Attempt": self.state,
            "X-Solquira-Reserved": reserved,
            "X-Solquira-Transitions": ",".join(self.transitions) or "none",
        }


def attempt_mode(provider) -> str:
    """The mode a composition may use, so a receipt never denies an attempt.

    `compose_brief` only calls a persistent budget provider; anything else is
    reported as disabled without a request being made.
    """
    from studio_budget import PersistentBudgetProvider

    if isinstance(provider, PersistentBudgetProvider) and provider.model == MODEL:
        return provider.mode
    return "disabled"


def compose_brief(payload: FieldPayload, provider, *, deadline: float, on_event=None, observed_trace=None):
    """One pre-budgeted attempt, no retries; callers bound total awaiting time too."""
    fallback = fallback_brief(payload)
    mode = attempt_mode(provider)
    if mode == "disabled":
        return fallback, "disabled", Attempt("disabled")
    if time.monotonic() >= deadline:
        # The provider was available but never reached: no call was made.
        return fallback, "timeout", Attempt("disabled", ("deadline_closed",), False)
    from dryland_progressive import compact_messages, parse_compact, ValidatedFields
    messages = compact_messages(payload)
    fields = ValidatedFields(payload, on_event) if on_event else None
    trace = observed_trace if observed_trace is not None else []
    try:
        message = provider.complete(
            messages, [], deadline=deadline, max_tokens=MAX_TOKENS, trace=trace,
            **({"on_delta": fields.feed} if fields else {})
        )
        transitions = tuple(trace)
        if time.monotonic() >= deadline:
            return fallback, "timeout", Attempt(mode, transitions, True)
        if not isinstance(message, dict) or message.get("tool_calls"):
            return (
                fallback,
                "invalid_output",
                Attempt(mode, transitions + ("rejected",), True),
            )
        content = message.get("content")
        # The HTTP response remains a full FieldBrief, but inference may only
        # supply three action details. Never widen that contract on a legacy
        # model response: Python still owns titles, order, labor and pathways.
        brief = parse_compact(content, payload)
        return brief, "success", Attempt(mode, transitions + ("validated",), True)
    except ValueError as exc:
        # Provider failures subclass ValueError; only a validation failure is a
        # rejected answer, anything carrying a code is an attempt that went wrong.
        code = getattr(exc, "code", None)
        if code:
            safe = (
                code
                if code
                in {"timeout", "budget_limit", "budget_unavailable", "provider_error"}
                else "invalid_output"
            )
            return (
                fallback,
                safe,
                Attempt(mode, tuple(trace) + (f"failed:{safe}",), "reserved" in trace),
            )
        return (
            fallback,
            "invalid_output",
            Attempt(mode, tuple(trace) + ("rejected",), bool(trace)),
        )
    except Exception as exc:
        code = getattr(exc, "code", "invalid_output")
        safe_code = (
            code
            if code
            in {"timeout", "budget_limit", "budget_unavailable", "provider_error"}
            else "invalid_output"
        )
        reserved = "reserved" in trace
        transitions = tuple(trace) + (f"failed:{safe_code}",)
        return fallback, safe_code, Attempt(mode, transitions, reserved)
