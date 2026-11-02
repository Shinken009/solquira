import type { Point } from "./geo/client";

export const EVIDENCE_IDS = ["forecast_context", "climate_baseline", "land_cover_change", "land_productivity", "soil_organic_carbon", "terrain", "field_observations"] as const;
export type EvidenceId = typeof EVIDENCE_IDS[number];
export type EvidenceState = "available" | "missing" | "unavailable" | "stale" | "not_applicable";
export interface LandEvidenceItem {
  id: EvidenceId;
  label: string;
  state: EvidenceState;
  reason: string;
  nature: "forecast" | "reanalysis" | "satellite" | "modeled_soil" | "terrain" | "self_reported";
  source: { id: string; name: string; version: string | null; method: string; url: string; license_url: string } | null;
  period: { start: string; end: string; time_zone: string } | null;
  retrieved_at: string | null;
  spatial_support: { description: string; resolution_m: number | null; query_precision_degrees: number | null } | null;
  units: string[] | null;
  coverage: { crs: "EPSG:4326"; bbox: [number, number, number, number] } | null;
  quality: "metadata_checked" | "not_assessed";
  uncertainty: string;
  limitations: string[];
  next_observation: string;
}
export interface LandEvidenceInventory {
  schema_version: "solquira.land-evidence/1";
  status: "inventory_only";
  scientific_validation: "not_validated";
  is_medalus_r: false;
  location: Point;
  generated_at: string;
  items: LandEvidenceItem[];
  limitations: string[];
}
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown, min: number, max: number): v is number => typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
const text = (v: unknown): v is string => typeof v === "string" && v.length > 0 && v.length <= 2000;
const texts = (v: unknown): v is string[] => Array.isArray(v) && v.length <= 20 && v.every(text);
const date = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) && !v.startsWith("0000-") && Number.isFinite(Date.parse(`${v}T00:00:00Z`)) && new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v;
const stamp = (v: unknown): v is string => typeof v === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(v) && date(v.slice(0, 10)) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v.slice(0, 10);
const natureById: Record<EvidenceId, LandEvidenceItem["nature"]> = {
  forecast_context: "forecast", climate_baseline: "reanalysis", land_cover_change: "satellite",
  land_productivity: "satellite", soil_organic_carbon: "modeled_soil", terrain: "terrain", field_observations: "self_reported",
};
const safeURL = (v: unknown): v is string => {
  if (!text(v)) return false;
  try { const url = new URL(v); return url.protocol === "https:" && !url.username && !url.password; } catch { return false; }
};
function source(v: unknown): boolean {
  return v === null || object(v) && ["id", "name", "method"].every(key => text(v[key])) && (v.version === null || text(v.version)) && safeURL(v.url) && safeURL(v.license_url);
}
function period(v: unknown): boolean {
  return v === null || object(v) && date(v.start) && date(v.end) && Date.parse(v.start) <= Date.parse(v.end) && v.time_zone === "UTC";
}
function support(v: unknown): boolean {
  return v === null || object(v) && text(v.description) && (v.resolution_m === null || finite(v.resolution_m, Number.MIN_VALUE, 40075000)) && (v.query_precision_degrees === null || finite(v.query_precision_degrees, Number.MIN_VALUE, 360));
}
function covers(v: unknown, point: Point): boolean {
  if (v === null) return true; // No footprint is claimed; null never means global coverage.
  if (!object(v) || v.crs !== "EPSG:4326" || !Array.isArray(v.bbox) || v.bbox.length !== 4) return false;
  const [west, south, east, north] = v.bbox;
  if (!finite(west, -180, 180) || !finite(east, -180, 180) || !finite(south, -90, 90) || !finite(north, -90, 90) || south > north) return false;
  const longitudes = Math.abs(point.longitude) === 180 ? [-180, 180] : [point.longitude];
  return point.latitude >= south && point.latitude <= north && longitudes.some(longitude => west <= east ? longitude >= west && longitude <= east : longitude >= west || longitude <= east);
}
export function parseLandEvidence(value: unknown, point: Point): LandEvidenceInventory {
  const bad = () => new Error("The evidence inventory could not be verified. No land condition has been inferred.");
  if (!object(value) || value.schema_version !== "solquira.land-evidence/1" || value.status !== "inventory_only" || value.scientific_validation !== "not_validated" || value.is_medalus_r !== false
    || !object(value.location) || !finite(value.location.latitude, -90, 90) || !finite(value.location.longitude, -180, 180)
    || value.location.latitude !== point.latitude || value.location.longitude !== point.longitude || !stamp(value.generated_at) || Date.parse(value.generated_at) > Date.now() + 60000
    || !texts(value.limitations) || !Array.isArray(value.items) || value.items.length !== EVIDENCE_IDS.length) throw bad();
  const seen = new Set<string>();
  for (const item of value.items) {
    if (!object(item) || !EVIDENCE_IDS.includes(item.id as EvidenceId) || seen.has(String(item.id)) || !text(item.label) || !text(item.reason)
      || !["available", "missing", "unavailable", "stale", "not_applicable"].includes(String(item.state))
      || item.nature !== natureById[item.id as EvidenceId]
      || !source(item.source) || !period(item.period) || !(item.retrieved_at === null || stamp(item.retrieved_at)) || !support(item.spatial_support)
      || !(item.units === null || texts(item.units)) || !covers(item.coverage, point) || !["metadata_checked", "not_assessed"].includes(String(item.quality))
      || !text(item.uncertainty) || !texts(item.limitations) || !text(item.next_observation)) throw bad();
    // The current inventory has one admitted source: a session-bound forecast.
    // A future adapter needs a reviewed client contract before claiming a new layer.
    if (item.state === "available" && (item.id !== "forecast_context" || item.nature !== "forecast" || item.quality !== "metadata_checked" || item.source === null || item.period === null || item.retrieved_at === null)) throw bad();
    if (item.id === "forecast_context" && item.source !== null) {
      const origin = item.source as Record<string, unknown>;
      if (origin.id !== "open-meteo-forecast" || origin.name !== "Open-Meteo" || origin.method !== "seven-day-model-forecast" || origin.url !== "https://open-meteo.com/en/docs" || origin.license_url !== "https://open-meteo.com/en/terms") throw bad();
    }
    if (item.id === "forecast_context" && item.units !== null && (!Array.isArray(item.units) || item.units.length !== 2 || item.units[0] !== "mm" || item.units[1] !== "°C")) throw bad();
    if (item.state === "available") {
      const retrieved = Date.parse(item.retrieved_at as string), generated = Date.parse(value.generated_at as string);
      const window = item.period as Record<string, string>;
      if (item.units === null || item.spatial_support === null || retrieved > generated + 60000 || generated - retrieved >= 600000
        || Date.parse(window.end) - Date.parse(window.start) !== 6 * 86400000
        || Math.abs(Date.parse(window.start) - Date.parse(value.generated_at.slice(0, 10))) > 86400000) throw bad();
    }
    seen.add(String(item.id));
  }
  return value as unknown as LandEvidenceInventory;
}
export async function fetchLandEvidence(point: Point, contextRef: string | null, csrf: string, signal: AbortSignal): Promise<LandEvidenceInventory> {
  const controller = new AbortController(), cancel = () => controller.abort();
  if (signal.aborted) controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(cancel, 10000);
  try {
    const base = (import.meta.env.VITE_API_URL || "/solquira-api").replace(/\/$/, "");
    const response = await fetch(`${base}/studio/evidence`, { method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: { "Content-Type": "application/json", "X-Solquira-CSRF": csrf }, body: JSON.stringify({ ...point, ...(contextRef ? { context_ref: contextRef } : {}) }) });
    if (!response.ok) throw new Error(response.status === 409 ? "The forecast reference expired or belongs to another point. Refresh the confirmed point’s forecast, then review again."
      : response.status === 429 ? "The workspace request limit was reached. Wait a minute, then review again."
      : "The evidence inventory is temporarily unavailable. No missing evidence has been replaced with a guess.");
    const body = await response.text();
    if (body.length > 64000) throw new Error("The evidence inventory exceeds its reviewed size limit.");
    return parseLandEvidence(JSON.parse(body), point);
  } catch (error) {
    if (signal.aborted) throw error;
    if (controller.signal.aborted) throw new Error("The inventory request took too long. Review again when the service is ready.");
    throw error instanceof Error ? error : new Error("The evidence inventory could not be reached.");
  } finally { clearTimeout(timeout); signal.removeEventListener("abort", cancel); }
}
