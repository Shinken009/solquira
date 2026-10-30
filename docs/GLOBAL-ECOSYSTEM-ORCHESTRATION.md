# Technical Specification — Global Ecosystem Orchestration: Soil, Climate & Resilient Crops

**Target Project:** Solquira — Understand the land. Cultivate the future.  
**Audience:** engineering and product review  
**Scope:** Global planetary drylands (Sahel, Mediterranean, Latin America, Australia, North America, Central/South Asia). Replaces localized assumptions with practitioner-validated global biophysical intelligence.

---

## Current implementation — 29 October 2026

The executable contract is in `artifacts/solquira/backend/dryland_brief.py`, `dryland_progressive.py` and `dryland_references.py`. The full FieldBrief remains the HTTP/UI contract, but it is no longer the model generation contract: Nemotron emits only three validated `{id, detail}` actions. Server rules supply the remaining fields. JSON remains supported; SSE adds skeleton/field/degraded/final. Four declared reference-table inputs follow their exact map presets, not arbitrary coordinates. See the public README for the user-facing contract.

The longer examples below are architectural background, not commands to replace the budgeted runtime or regenerate all card fields. In particular, no unbudgeted AsyncOpenAI client or automatic retry is instantiated by the current route.

## 1. System Vision & The Judge's Scrutiny

Solquira is an open environmental intelligence workspace designed for smallholders and technical advisers combating desertification in water-stressed landscapes worldwide.

### The NVIDIA Judge's #1 Doubt: _"Where is the Agent?"_

A skeptical judge will ask: _"Is this just one completion filling a card?"_
**Our decisive architectural answer:**

1. **Deterministic biophysics first, language last.** Every number a farmer reads is computed or fixed in Python before the model is called. Today the Studio calls exactly one external geoscientific source: **Open-Meteo** (seven-day forecast and reference demand), behind the `--geodata` gate. **ISRIC SoilGrids and FAO EcoCrop/GAEZ are not called by this build**; the field-brief example uses a server-owned fixture (`dryland_brief.golden_payload`). Section 8 states this precisely, because a judge will check the receipt.
2. **Policy Over Observations:** When the example inputs change (e.g., `dry_days_ahead` flips from 1 to 6), the deterministic rules reorder the work: the fallback puts Zai pits first and adds `delay_sowing`, and the pathway list follows the payload's approved catalogue. With a live model, the model supplies three validated action details; Python keeps the ordering and membership is still enforced by `parse_brief()` (no pathway outside the catalogue, `biocrust_check` always present, `delay_sowing` only for a dry week) and the bands are still the server's.
3. **Deterministic Safety Boundary:** The model writes labor and crop language; **it is not allowed to own the numbers**. The server overwrites `soil_life`, `water_held`, `rain_outlook`, `source` and every `done` flag after validation, so a model that lies about the bands still renders the server's band. If the call times out at 3.0 s, the same `FieldBrief` card still renders with `source: "fallback"`.

> **The 15-Second Opening Hook (honest version):**  
> _"A chatbot will tell a farmer in the Sahel to plant maize and 'consult an expert.' Solquira will not. A fixed, server-side field example decides the soil and water bands; Open-Meteo is a separate forecast panel. Nemotron 3 Nano on Nebius only writes the work: dig the zai pits, leave the biocrust, sow sorghum with cowpea. If the model is late, or if it invents a number, the card still loads with the server's bands. The soil advice never hallucinates a millimeter of rain."_

$$\text{Atmosphere (Open-Meteo, gated)} + \text{Server-owned field fixture} \xrightarrow{\text{Deterministic Python bands}} \text{Nemotron Cognitive Synthesis}$$
$$\text{Roadmap, not shipped: ISRIC SoilGrids} + \text{FAO EcoCrop/GAEZ}$$

---

## 2. Nebius Token Factory Runtime Truths (Critical Gotchas)

To avoid 400 Bad Request or 404 Model Not Found errors in deployment:

1. **Exact Model Identifier:**
   - Catalog ID is strictly case-sensitive: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`  
     _(Do NOT use `nvidia/nemotron-3-nano` which causes a 404)._
2. **Endpoint & Host Routing:**
   - Nano is served from `eu-north1`.
   - Base URL: `https://api.tokenfactory.nebius.com/v1`
3. **Latency & Token Capping:**
   - Nano throughput is $\approx 60\text{ tok/s}$. A 500-token brief plus cold-start will miss a 3.0s timeout.
   - **The brief asks for `max_tokens=350`; the studio agent uses the reserved 700.** The cap is a per-call argument in `1..700`, not a global setting. Pre-warm the endpoint before recording the demo.
4. **JSON Schema Mode:**
   - If Nebius schema mode returns 400 on nested Pydantic schemas, fall back to `response_format={"type": "json_object"}` and let `FieldBrief.model_validate_json()` act as the strict validation barrier.

---

## 3. Shipped Pydantic contracts (`dryland_brief.py`)

FastAPI response and payload models enforcing zero-parsing, strict typed JSON:

```python
from __future__ import annotations
from enum import Enum
import math
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class SoilLife(str, Enum):
    strong = "Strong"
    building = "Building"
    weak = "Weak"


class WaterHeld(str, Enum):
    good = "Good"
    fair = "Fair"
    low = "Low"


class LaborLevel(str, Enum):
    low = "low"        # one person, hand tools, < half day
    medium = "medium"  # household, one day
    high = "high"      # group labor or multi-day


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


class VitalSigns(BaseModel):
    soil_life: SoilLife
    water_held: WaterHeld
    rain_outlook: str = Field(min_length=8, max_length=72, description="Short phrase for next 7 days. No mm numbers.")


class ActionItem(BaseModel):
    id: ActionId
    title: str = Field(min_length=4, max_length=48)
    detail: str = Field(min_length=8, max_length=120)
    labor: LaborLevel
    done: bool = False


class CropPathway(BaseModel):
    id: PathwayId
    name: str = Field(min_length=3, max_length=40)
    why: str = Field(min_length=8, max_length=110)
    water_note: str = Field(min_length=8, max_length=80)


class FieldBrief(BaseModel):
    """Shape consumed directly by React mobile UI. No free-form numbers."""
    source: Literal["model", "fallback"] = "model"
    vital_signs: VitalSigns
    actions: list[ActionItem] = Field(min_length=3, max_length=5)
    pathways: list[CropPathway] = Field(min_length=3, max_length=3)

    @field_validator("actions")
    @classmethod
    def unique_actions(cls, items: list[ActionItem]) -> list[ActionItem]:
        ids = [a.id for a in items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate action id")
        return items

    @field_validator("pathways")
    @classmethod
    def unique_pathways(cls, items: list[CropPathway]) -> list[CropPathway]:
        ids = [p.id for p in items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate pathway id")
        return items


class FieldPayload(BaseModel):
    """Server-built. The model must not invent or restate measurements."""
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    soc_band: SoilLife
    awc_band: WaterHeld
    rain_phrase: str = Field(min_length=8, max_length=72)
    dry_days_ahead: int = Field(ge=0, le=7)
    fao_crops: list[PathwayId] = Field(default_factory=list)


def band_soc(soc_g_per_kg: float | None) -> SoilLife:
    # A missing measurement is refused, never banded: an unobserved field must
    # not render as "Building" soil. The shipped code raises ValueError.
    if soc_g_per_kg is None or not math.isfinite(soc_g_per_kg) or soc_g_per_kg < 0:
        raise ValueError("A finite, nonnegative supported measurement is required.")
    if soc_g_per_kg >= 15:
        return SoilLife.strong
    if soc_g_per_kg >= 8:
        return SoilLife.building
    return SoilLife.weak


def band_awc(awc_mm: float | None) -> WaterHeld:
    # Same rule: no default band for missing water capacity.
    if awc_mm is None or not math.isfinite(awc_mm) or awc_mm < 0:
        raise ValueError("A finite, nonnegative supported measurement is required.")
    if awc_mm >= 120:
        return WaterHeld.good
    if awc_mm >= 60:
        return WaterHeld.fair
    return WaterHeld.low
```

### 3.1 Guards the shipped models enforce beyond this sketch

`dryland_brief.Contract` sets `extra="forbid"`, `allow_inf_nan=False` and `validate_assignment=True`, plus:

- `words_only()` rejects digits, spelled-out numbers (English and Spanish), unit words (`percent`, `hectare`, `kg`, `mm`, `litre`, `ppm`), agrochemical classes and common active ingredients or brands (`pesticide`, `herbicide`, `glyphosate`, `roundup`, `plaguicida`…), and any URL, inside `rain_outlook`, action `title`/`detail` and pathway `name`/`why`/`water_note`. It is a **bounded word list, not semantic truth detection**: an unlisted brand or a paraphrase can still pass, which is why the UI also labels the composition's origin.
- `FieldPayload` requires `lat`/`lon`/`dry_days_ahead` as strict JSON types (a string or bool is refused) and refuses an `fao_crops` filter of one or two items (an unfilterable candidate list): either no filter or at least three unique pathways.
- `parse_brief()` rejects duplicate JSON keys, non-finite constants, a pathway outside `payload.fao_crops`, an action set without `biocrust_check`, and `delay_sowing` when the payload does not describe a dry week.
- `resolve_payload()` refuses any payload that is not byte-for-byte one of the server's two examples, so a browser can never substitute its own measurements.

Evidence: `artifacts/solquira/backend/test_dryland_brief.py` (29 cases) and the API audit of 29 October 2026.

---

## 4. System Prompt for NVIDIA Nemotron 3 Nano (<210 words)

```python
SYSTEM_PROMPT = """You are a Dryland Field Agronomist for smallholders. Reply with one JSON object matching the schema. No prose, no markdown.

You receive a deterministic example payload computed in Python. It is not a measured SoilGrids or FAO observation and Open-Meteo supplies a separate forecast context. Trust the payload. Do not recompute, round, invent, or quote any number (mm, g/kg, %, hectares, yields, prices).

Map fields exactly:
- vital_signs.soil_life = payload.soc_band
- vital_signs.water_held = payload.awc_band
- vital_signs.rain_outlook = payload.rain_phrase, unchanged or shortened, still with no numbers

actions: 3 to 5 items, unique ids, labor-aware, ordered by urgency.
Prefer zai_pits or half_moons when water_held is Low or dry_days_ahead is high.
Prefer living_cover or mulch when soil_life is Weak or Building.
Always include biocrust_check.
Use delay_sowing only if rain_phrase says a dry week.
Titles and details are short field instructions. No chemicals, no brands, no disclaimers.

pathways: exactly 3, unique ids, chosen ONLY from payload.fao_crops when that list is non-empty. Never fill a remaining slot from outside the list, and never repeat an id; if the list is empty, choose any three of sorghum_cowpea, millet_cowpea, quinoa, opuntia, amaranth.
why and water_note state the water trade-off in words only (thirsty / sparing / once established). No yield claims.

source must be "model".
Never say you are an AI. Never add caveats, citations, or "consult an expert"."""
```

The shipped prompt (`dryland_brief.SYSTEM_PROMPT`) is authoritative and uses the same wording about the payload's provenance. `parse_brief()` enforces the pathway rule regardless of what the model writes.

---

## 5. FastAPI Route & Robust Client Invocation

The shipped route lives in `artifacts/solquira/backend/studio_api.py` (`field_router`) and differs from the sketch below in three ways that matter:

1. **Admission before inference.** `POST /v1/field-brief` requires the Studio's opaque session cookie and `X-Solquira-CSRF` header, passes the shared rate/concurrency admission, and runs the composition in a shielded worker so an aborted HTTP request cannot free its lease early.
2. **Two deadlines, one budget.** The route awaits with `asyncio.wait_for` at `3.0 s − 0.1 s` (serialization margin); `compose_brief` makes exactly one pre-budgeted attempt with no retry, and the persistent ledger reserves before the request starts.
3. **Per-call generation cap.** `NebiusProvider.complete(..., max_tokens=...)` defaults to the reserved 700-output envelope and accepts anything `1..700`; the brief asks for `350`. The cap is a call argument, not a provider setting.

```python
from fastapi import APIRouter
from openai import AsyncOpenAI
from dryland_brief import SYSTEM_PROMPT, FieldBrief, FieldPayload, fallback_brief

router = APIRouter()
client = AsyncOpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1",
    api_key="",  # Injected from server environment
)
# Strictly verified case-sensitive catalog ID on Nebius Token Factory
MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
TIMEOUT_S = 3.0


@router.post("/v1/field-brief", response_model=FieldBrief)
async def get_field_brief(payload: FieldPayload) -> FieldBrief:
    try:
        resp = await client.chat.completions.create(
            model=MODEL,
            temperature=0,
            max_tokens=350,  # Capped at 350 to safely beat 3.0s timeout at ~60 tok/s
            timeout=TIMEOUT_S,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": payload.model_dump_json()},
            ],
        )
        raw = resp.choices[0].message.content or ""
        brief = FieldBrief.model_validate_json(raw)

        # Security: Bands are strictly owned by server payload, not model
        brief.vital_signs.soil_life = payload.soc_band
        brief.vital_signs.water_held = payload.awc_band
        brief.source = "model"
        return brief
    except Exception:
        # Graceful degradation on cold-start or timeout
        return fallback_brief(payload)
```

**Receipt headers.** `GET /v1/field-payload` carries `X-Solquira-Evidence` and `X-Solquira-Scenario`.
`POST /v1/field-brief` adds `X-Solquira-Outcome`, `X-Solquira-Inference`, `X-Solquira-Model`,
`X-Solquira-Latency-Ms`, `X-Solquira-Max-Tokens: 350` and `X-Solquira-Deadline-Ms: 3000`.
The browser client validates the brief response against the payload it posted (bands, structure,
source/outcome agreement, model id) and fails closed on a mismatch; the payload response headers are
recorded but not cross-checked, and the card's "350 output tokens · 3-second model deadline" line is
static copy, not read from the header.

---

## 6. Deterministic Fallback (`fallback_brief`)

```python
def fallback_brief(payload: FieldPayload) -> FieldBrief:
    # payload.soc_band owns the soil band; the Former draft said payload.soil_life,
    # a field that does not exist on FieldPayload (AttributeError at runtime).
    dry = payload.awc_band == WaterHeld.low or payload.dry_days_ahead >= 4
    weak = payload.soc_band == SoilLife.weak
    return FieldBrief(
        source="fallback",
        vital_signs=VitalSigns(
            soil_life=payload.soc_band,
            water_held=payload.awc_band,
            rain_outlook=payload.rain_phrase[:72],
        ),
        actions=[
            ActionItem(
                id=ActionId.zai_pits if dry else ActionId.half_moons,
                title="Dig water pockets before the next rain",
                detail="Small pits or half-moons on the contour. Add a handful of manure if you have it.",
                labor=LaborLevel.medium if dry else LaborLevel.low,
            ),
            ActionItem(
                id=ActionId.living_cover if weak else ActionId.mulch,
                title="Cover bare soil this week",
                detail="Sow a thin cereal-legume cover or spread pruning residues so wind and sun do not strip the surface.",
                labor=LaborLevel.low,
            ),
            ActionItem(
                id=ActionId.biocrust_check,
                title="Look for living crust",
                detail="Dark or lichen patches mean do not scrape or drive there. Leave them. They hold the soil.",
                labor=LaborLevel.low,
            ),
        ],
        pathways=[
            CropPathway(
                id=PathwayId.sorghum_cowpea,
                name="Sorghum + cowpea",
                why="Proven pair when rains are short and uneven.",
                water_note="Sparing once established. Sow into the pits.",
            ),
            CropPathway(
                id=PathwayId.quinoa,
                name="Quinoa",
                why="Holds on dry, salty ground if the variety matches the site.",
                water_note="Sparing. Avoid waterlogged pits.",
            ),
            CropPathway(
                id=PathwayId.opuntia,
                name="Opuntia rows",
                why="Living fence and fodder that keeps working in a failed season.",
                water_note="Very sparing after the first rooting.",
            ),
        ],
    )
```

Shipped differences from this sketch (`dryland_brief.fallback_brief`): the water action is inserted at position 2 when `rain_phrase` describes a dry week (`delay_sowing`), the pathway list is `payload.fao_crops[:3]` so a filter change changes the card, and every string stays inside `words_only()` (no digits, no units). The fallback is the _same_ JSON structure as a model answer — `test_fallback_and_model_share_one_json_structure` asserts that with a recursive key/type comparison, which is what lets the UI render either without a branch.

---

## 7. Mobile UX Contract (GSMA AgriTech 2025 & FAO SoilFER Standards)

Designed for a 390px viewport, high sunlight readability, and $\ge 7:1$ contrast:

1. **Card 1: Field Vital Signs Hero Card (140–160px tall):**
   - _Left third:_ **Soil Life** (Strong / Building / Weak based on SOC).
   - _Center third:_ **Water Store** (Good / Fair / Low based on AWC).
   - _Right third:_ **Rain Outlook** (7-day vernacular phrase, e.g., _"Rain expected in 2–3 days"_ or _"Dry week ahead — prepare zai"_).
   - Tap to expand reveals the exact soil and forecast inputs without cluttering the default glance. In this build that means the server fixture's bands and, when `--geodata` is on, Open-Meteo values — **not** SoilGrids numbers.
2. **Card 2: Action Checklist with Visual State:**
   - 3 to 5 imperative actions (large icons, $\ge 48\text{ px}$ circular touch checkboxes).
   - Labor badge (_low, medium, high_).
3. **Card 3: 3 Resilient Multi-Species Pathways:**
   - Contrasting water notes and soil benefits (Sorghum+Cowpea, Quinoa, Opuntia, Amaranth).

---

## 8. Implementation status (verified 2026-10-28)

| Claim in this document                    | State in this build                                                                    | Evidence                                                               |
| ----------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Pydantic contracts of §3                  | Shipped, stricter (`extra=forbid`, no numbers in text, strict JSON types)              | `artifacts/solquira/backend/dryland_brief.py`, `test_dryland_brief.py` |
| `POST /v1/field-brief`                    | Shipped with session + CSRF admission, 3.0 s deadline, fallback on every failure path  | `studio_api.py` (`field_router`), `test_dryland_brief.FieldApiTests`   |
| `max_tokens=350`                          | Shipped per call; provider default is the reserved 700 envelope                        | `dryland_brief.MAX_TOKENS`, `test_dryland_brief.GenerationCapTests`    |
| Server overwrites bands/`source`/`done`   | Shipped and tested                                                                     | `dryland_brief.parse_brief`                                            |
| Open-Meteo seven-day forecast             | Shipped, gated by `--geodata` / `SOLQUIRA_STUDIO_GEODATA`                              | `studio_geo.py`                                                        |
| ISRIC SoilGrids (SOC, texture)            | **Not implemented.** No request exists in this build                                   | `rg -i soilgrids` → docstrings only                                    |
| FAO EcoCrop/GAEZ crop rules               | **Not implemented.** `fao_crops` is a fixed catalogue of five pathway ids              | `dryland_brief.PathwayId`                                              |
| Nebius Token Factory live latency < 3.0 s | Measured 29 Oct 2026: 1849/1939/2149 ms (n=3) and 2746 ms in the API audit. Not an SLA | Local measurement, 29-oct-2026                                         |
| Public GitHub upload                      | **Not performed.** The staging script writes a local, hash-verified archive only       | `scripts/stage-github-release.py` (`uploaded: false`)                  |

SoilGrids and GAEZ are roadmap only: transport, unit conversions, AWC arithmetic with coarse fragments, uncertainty propagation and licence terms are specified in a separate integration note that is not part of this package. The current build serves a server-owned example and the receipt names the source that produced each band.
