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

1. **Start with a place.** Allow location to center the map on you, select a reference
   landscape or enter coordinates. The optional Cesium view combines a detailed
   OpenStreetMap reference layer with a bundled Natural Earth fallback and optional
   NASA GIBS imagery. With `--geodata`, the server loads a seven-day Open-Meteo
   forecast: rainfall, reference ET₀ and temperature, in UTC.
2. **Explore a field question.** The forecast can be attached to a question in the context panel.
   This forecast-linked workflow is separate from the reference scenarios below.
3. **Build a field brief.** Four map-linked reference profiles provide distinct soil-life and
   water-held bands. Choose a dry-week or returning-rain scenario, then build a complete card
   with a labor-aware checklist and three crop pathways.
4. **See useful wording arrive.** Python supplies the complete initial brief. When inference is
   enabled, Nemotron writes three short action details; only complete validated fields render.
5. **Take the next step.** Mark the checks to bring to a field visit. Open the receipt to see the
   evidence type, model, input fingerprint, budget reservation and execution outcome.

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
node scripts/qa-dryland-brief.mjs http://127.0.0.1:8036
```

Browser QA expects the default local, inference-disabled server. It checks the card, scenario
changes, reference selection, source labels, contrast and layout without contacting a provider.
The fixture check compares the stored examples with the current Python engine and its hash.

## Sources and credit

| Component                  | Provider                                                | Attribution / terms                         |
| -------------------------- | ------------------------------------------------------- | ------------------------------------------- |
| Forecast and reference ET₀ | [Open-Meteo](https://open-meteo.com/)                   | CC BY 4.0                                   |
| MODIS imagery              | [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/) | Imagery courtesy NASA EOSDIS GIBS / LP DAAC |
| Basemap geometry           | [Natural Earth](https://www.naturalearthdata.com/)      | Public domain                               |
| Globe rendering            | [CesiumJS](https://cesium.com/)                         | Apache-2.0                                  |
| Interface icons            | [Lucide](https://lucide.dev/)                           | ISC                                         |

## License

Apache License 2.0 — see `LICENSE`. Third-party attributions are listed in `NOTICE`.
