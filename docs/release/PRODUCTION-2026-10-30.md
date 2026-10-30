# Production release — 2026-10-30

## Environment

- URL: https://solquira.vercel.app
- Deployment: `dpl_8LuH4ZaJamL9SzkYFPro1gNvaFWn` (target `production`, state `READY`)
- Bundle: `assets/index-ERzqViXM.js`
- Rollback target: `dpl_J9GfoGj932Q7CajKGcVLFZTYHqxd` (`assets/index-Dm31Nm2P.js`)

## What changed

| Area | Change |
|---|---|
| Cesium readiness | The map no longer reports ready before a basemap raster is rendered (`data-raster-state`). `requestImage` resolution is preserved by `rasterLoad.ts` with unit tests. |
| NASA detail | Accessible label with 250 m regional scope, observed date and cloud/availability limits; 44 px target. |
| Mobile density (≤767 px) | Header 199.6 → 51 px; H1 32 px; full-width CTA inside the first screen at 320×568 and 360×640; illustration at 16/9 (288.9 → 184.5 px). Desktop unchanged. |
| Status chip | "Demo · prepared examples" with an explanatory accessible name; the internal transport state is no longer exposed as the primary label. |
| Asset naming | `solquira-rural-landscape.svg` replaces the generated download filename. |
| Deploy config | `vercel.json` is now versioned: API rewrite to the hosted backend, CSP and security headers. |

## Verification performed on production

- 320×568, 360×640, 390×844, 768×1024, 1440×900: no horizontal overflow.
- Header, H1, CTA position and illustration height identical at 768/1440 before and after.
- Map `ready` with `data-raster-state="rendered"`.
- Forecast `cached`; NASA imagery enabled with 35/30/69 GIBS JPEG tiles, all 200.
- Point tap changed coordinates; 2D view worked; location context returned its honest "not one of the prepared reference profiles" note.
- New asset 200, replaced asset 404; image radius 18 px at ≤390 and 24 px at ≥768.
- Zero console errors, page errors or failed requests in the captured runs.
- `tsc --noEmit` exit 0; Vite build success.

## Limits

- Natural Earth is native z0–2 only; NASA MODIS is regional 250 m with cloud, date and availability limits. No parcel-resolution claim.
- Headless Chromium is not a physical-device test. Android/GPU validation remains pending.
- The model-composition path (`/studio/evaluate`) was not re-exercised on production to avoid consuming inference budget.

## Rollback

Promote `dpl_J9GfoGj932Q7CajKGcVLFZTYHqxd` back to the production alias in Vercel. No DNS, database or
backend change is involved.
