# Solquira — Understand the land. Cultivate the future.

**Open-source environmental field intelligence for dryland communities.**

Solquira helps communities, land stewards, growers and advisers explore landscape context,
inspect weather and evidence, and prepare informed field observations. It was created in response
to desertification and land degradation, with agriculture as one application rather than its
entire scope. Short-term forecasts and synthetic examples are **not** a diagnosis of
desertification, measured environmental impact or a validated long-term prediction.

Built for the **Nebius × NVIDIA Global AI Hackathon**, **Best Apps and Agents** track.
This repository documents the implemented code, local execution, tests, data provenance
and scientific limits. Planned integrations are identified separately from working features.

## The experience

The workspace has four keyboard-accessible areas: **Territorio (Territory),
Anticipación (Anticipation), Orientación (Guidance), and Evidencia (Evidence)**.
Switching areas preserves the map, selected place, forecast, scenario, results,
session and land record. Source requests occur through user actions, not navigation.


1. **Choose your place.** Mark the earth in Cesium or explicitly request device GPS, review
   the marker, then confirm. Typed coordinates are an accessible alternative, not a requirement.
   Without permission the map remains neutral. GPS and manually selected points stay distinct;
   no late GPS reply replaces a newer choice. Confirmation requests the seven-day forecast when
   geographic sources are enabled. The original selection is retained; only the request is rounded.
2. **Tell us what matters.** A short conditional journey asks about land use, the concern or
   exploration goal, a reported sign, duration and practical barriers. It asks four or six questions
   including the place, without pretending these answers are instrumental measurements.
3. **Prepare one manageable check.** Local deterministic guidance helps structure a field visit,
   not prescribe sowing, irrigation or treatment. A person can review a suggested draft in the
   existing land record; nothing is saved to Kobo or shared automatically.
4. **Read the environmental context.** Inspect P1 forecast indicators; request ERA5 history only
   when wanted. Dates, resolution, provenance and unavailable states stay visible.
5. **Keep examples separate.** Four synthetic teaching landscapes, crop-path comparisons and
   optional reference briefs remain available. They are not measurements of the selected place.
   The optional AI wording remains an explicit action protected by the existing budget.

The map uses an OpenStreetMap reference layer, bundled Natural Earth fallback and optional
NASA GIBS imagery. Map selection does not establish a parcel boundary or survey accuracy.
Kobo import, shared observation storage and team authorization are not implemented in this release.

## Environmental anticipation and historical context

Seven-day indicators are computed in Python from the validated Open-Meteo forecast:
precipitation and reference ET₀ totals (mm), their arithmetic difference (mm),
minimum/maximum temperature (°C), days below **1 mm/day**, longest consecutive
low-precipitation run, and contiguous episodes at or above **1 mm/day**. Episode
concentration is the largest episode's share of seven-day precipitation, not an
intensity or flood-risk estimate. Zero precipitation produces no percentage share.
These declared descriptive thresholds are not crop-specific stress thresholds.
Source, UTC dates, retrieval time, units, method, missing observations and limitations
accompany the indicators. Precipitation includes snow water equivalent. The
precipitation−ET₀ difference is **not soil moisture, a root-zone water balance or
irrigation need**; one dry forecast week does not diagnose desertification.

An optional **explicit historical lookup** compares the latest fully available
calendar month with the same month in **1991–2020**, using fixed **ERA5** reanalysis
from Open-Meteo. It respects the five-day source lag and requires all target days
and thirty complete baseline months. Results include monthly totals, absolute mm
anomaly, percentage anomaly (unavailable below a 0.01 mm reference), grid location,
coverage and source. This is approximately 0.25° modeled reanalysis, not a measured
parcel, a seasonal forecast, SPI or a degradation diagnosis. Missing data remain
unavailable. Requests are bounded, cached for 24 hours, and limited per process to one uncached lookup/hour and four/day. Long requests count as multiple source API units, not one call; changing workspace
areas does not fetch decades of data. Geographic sources must be enabled.

The free Open-Meteo service is for noncommercial use with attribution; commercial
operation requires reviewing its terms separately. No subscription is activated.
Quantitative vegetation/NDVI/NDMI trends are **not implemented**: rendered GIBS
true-color JPG tiles do not supply the required numeric bands, cloud/quality masks
or seasonal comparisons. The current imagery and map functionality are preserved.

## Bounded orchestration

**Python owns the calculations and policy; NVIDIA Nemotron improves the language.**

The model uses the exact Token Factory catalogue ID
`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` at `https://api.tokenfactory.nebius.com/v1/`.
It receives coordinate-free reference context and a small set of approved action identifiers.
Its compact response contains exactly three action details, not a complete regenerated card.

The `/v1/field-brief` endpoint supports JSON and server-sent events:

```text
complete Python skeleton → validated action fields → authoritative final card
```

- Python retains soil/water bands, action order, titles, labor labels and pathways.
- Pydantic and policy validation reject invalid identifiers, duplicate fields, quantities,
  links and unsupported text patterns before final acceptance.
- The brief uses one request, a 350-output-token cap and a three-second route deadline.
  The separate forecast-linked agent has a 700-output-token cap.
- A rejected, truncated or late composition ends with the complete field-rules card in the
  same JSON shape. An interrupted stream restores the initial card; checklist edits wait for final.
- Session/CSRF controls and bounded admission protect the Studio API; hosted inference additionally requires a persistent budget ledger.
  Credentials stay on the server. No model call occurs just by opening the page.

## Evidence layers

| Layer                                    | Current implementation                                              |
| ---------------------------------------- | ------------------------------------------------------------------- |
| Open-Meteo seven-day forecast            | Optional live source; coordinates rounded to 0.001°                 |
| ERA5 monthly precipitation context      | Explicit optional lookup; fixed 1991–2020 same-month baseline          |
| NASA GIBS satellite imagery              | Optional geographic imagery layer                                   |
| Soil and water reference profiles        | Four synthetic teaching inputs, calculated in Python                |
| Crop pathways and comparison rules       | Explicit demonstration catalogue and deterministic rules            |
| Nemotron action wording                  | Server-side Token Factory integration, enabled only by the operator |
| SoilGrids / FAO quantitative acquisition | Future integration; not called by this build                        |

The four reference profiles are deliberately contrasting examples, not regional soil measurements:

| Map reference                      | Soil life | Water held |
| ---------------------------------- | --------- | ---------- |
| Sahel / Ouagadougou                | Weak      | Fair       |
| Chilean dryland valley / Coquimbo  | Weak      | Low        |
| Mediterranean farmland / Andalusia | Building  | Fair       |
| East African plateau               | Strong    | Good       |

Selecting another point clears the previous brief. Arbitrary coordinates do not inherit the
nearest reference profile. The live forecast panel remains separate from the brief's two
weather scenarios. Local agronomic validation and impact evaluation are next-stage work.

## Run locally

Requirements: Python 3.12+ (the hosted Docker image uses Python 3.12), Node 22+, pnpm 11.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r artifacts/solquira/backend/requirements.txt
pnpm.cmd install --frozen-lockfile
pnpm.cmd --filter @workspace/solquira build
python scripts/run-studio-local.py --geodata
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r artifacts/solquira/backend/requirements.txt
pnpm install --frozen-lockfile
pnpm --filter @workspace/solquira build
python scripts/run-studio-local.py --geodata
```

Open <http://127.0.0.1:8036>. The default launcher does not read provider credentials or enable
inference. It serves Python field rules; `--geodata` separately enables the forecast connection.
Without that option, the reference brief still works locally, while live geographic data does not.
This is a local server workflow, not an installed offline application.

### Enabling inference

The model integration is included in the source. The operator supplies the server-side key,
trusted ingress settings and an existing approved persistent budget. The deployment launcher is
`scripts/run-studio-hosted.py`; configuration validation is available through its `--check` mode.
Do not expose the loopback launcher as a public service or place a key in a frontend variable.

Runtime credentials, ledger contents and deployment-specific settings are not stored in this
repository. The deterministic reference workflow does not require provider credentials.
The model and spending boundaries can be inspected directly in
`artifacts/solquira/backend/dryland_brief.py`,
`artifacts/solquira/backend/studio_runtime.py` and
`artifacts/solquira/backend/studio_budget.py`.

## API surface in Studio mode

| Route                                                  | Purpose                                                                 |
| ------------------------------------------------------ | ----------------------------------------------------------------------- |
| `GET /solquira-api/healthz`                            | Liveness                                                                |
| `GET /solquira-api/studio/bootstrap`                   | Establish opaque session cookie and CSRF token; expose effective limits |
| `POST /solquira-api/studio/evaluate`                   | Deterministic crop-path comparison                                      |
| `POST /solquira-api/studio/history`                    | Explicit bounded ERA5 historical precipitation comparison               |
| `POST /solquira-api/studio/location`                   | Optional Open-Meteo context for rounded coordinates                     |
| `POST /solquira-api/studio/agent`                      | Tool-grounded answer over the attached context                          |
| `GET /v1/field-payload?scenario=dry&place=ouagadougou` | Canonical reference payload (session + CSRF)                            |
| `POST /v1/field-brief`                                 | Typed brief; JSON by default, SSE with `Accept: text/event-stream`      |

Bootstrap establishes the session; protected operations require its cookie and
`X-Solquira-CSRF` token. Health is public. The legacy `/api/**` surface is closed in Studio mode.

## Verify

Run from the repository root with the virtual environment active:

```powershell
python -m unittest discover -s artifacts/solquira/backend -p "test_*.py"
pnpm.cmd run typecheck
python scripts/check-fixtures.py
node scripts/qa-environment-navigation.mjs http://127.0.0.1:8036
node scripts/qa-territorial-journey.mjs http://127.0.0.1:8036
```

Browser QA expects the default local, inference-disabled server. It checks the card, scenario
changes, reference selection, source labels, contrast and layout without contacting a provider.
The fixture check compares the stored examples with the current Python engine and its hash.

## Sources and credit

| Component                  | Provider                                                | Attribution / terms                         |
| -------------------------- | ------------------------------------------------------- | ------------------------------------------- |
| Forecast and reference ET₀ | [Open-Meteo](https://open-meteo.com/)                   | CC BY 4.0                                   |
| Historical precipitation | [Open-Meteo ERA5 archive](https://open-meteo.com/en/docs/historical-weather-api) | [Noncommercial API terms](https://open-meteo.com/en/terms), CC BY 4.0 |
| MODIS imagery              | [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/) | Imagery courtesy NASA EOSDIS GIBS / LP DAAC |
| Basemap geometry           | [Natural Earth](https://www.naturalearthdata.com/)      | Public domain                               |
| Globe rendering            | [CesiumJS](https://cesium.com/)                         | Apache-2.0                                  |
| Interface icons            | [Lucide](https://lucide.dev/)                           | ISC                                         |

## License

Apache License 2.0 — see `LICENSE`. Third-party attributions are listed in `NOTICE`.
