export interface Point { latitude: number; longitude: number }
export interface AttachedContext { reference: string; location: Point; retrievedAt: string }
export interface Day { date: string; temperature_max_c: number; temperature_min_c: number; precipitation_mm: number; reference_et0_mm: number }
export interface Anticipation {
  schema_version: "solquira.anticipation/1";
  state: "ready" | "unavailable";
  source: string;
  source_url: string;
  kind: string;
  time_zone: string;
  retrieved_at: string | null;
  period_start: string | null;
  period_end: string | null;
  method: string;
  units: Record<string, string>;
  thresholds: {
    low_precipitation_mm_per_day: number;
    wet_episode_min_mm_per_day: number;
  };
  missing_inputs: string[];
  limitations: string[];
  precipitation_total_mm?: number;
  reference_et0_total_mm?: number;
  precipitation_minus_et0_mm?: number;
  low_precipitation_days?: number;
  longest_low_precipitation_run_days?: number;
  precipitation_days?: number;
  precipitation_episode_count?: number;
  maximum_daily_precipitation_mm?: number;
  wettest_day_date?: string | null;
  wettest_day_share_percent?: number | null;
  largest_episode_total_mm?: number;
  largest_episode_share_percent?: number | null;
  temperature_min_c?: number;
  temperature_max_c?: number;
}
export interface Imagery { state: 'ready_to_load' | 'disabled'; source: string; date: string; layer: string; url_template: string; max_level: number }
export interface FieldContextData {
  context_ref?: string | null;
  schema_version: string; location: Point; retrieved_at: string;
  weather: { state: 'ready' | 'cached' | 'unavailable' | 'disabled'; source: string; kind: string; time_zone: string; days: Day[]; note?: string };
  imagery: Imagery;
  anticipation?: Anticipation;
}
const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const finite = (v: unknown, min: number, max: number): v is number => typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;
const strings = (v: unknown) =>
  Array.isArray(v) &&
  v.length <= 30 &&
  v.every((x) => typeof x === "string" && x.length <= 1500);
function verifyAnticipation(v: unknown, days: Day[], retrievedAt: string) {
  if (
    !object(v) ||
    v.schema_version !== "solquira.anticipation/1" ||
    !["ready", "unavailable"].includes(String(v.state)) ||
    v.source !== "Open-Meteo" ||
    v.source_url !== "https://open-meteo.com/en/docs" ||
    v.kind !== "model_forecast" ||
    v.time_zone !== "UTC" ||
    !(
      v.retrieved_at === retrievedAt ||
      (v.state === "unavailable" && v.retrieved_at === null)
    ) ||
    v.method !== "seven-day-descriptive-v1" ||
    !strings(v.missing_inputs) ||
    !strings(v.limitations) ||
    !object(v.units) ||
    !object(v.thresholds) ||
    v.thresholds.low_precipitation_mm_per_day !== 1 ||
    v.thresholds.wet_episode_min_mm_per_day !== 1
  )
    throw new Error("The anticipation method could not be verified.");
  const units = v.units as Record<string, unknown>;
  if (units.precipitation !== "mm" || units.reference_et0 !== "mm" || units.precipitation_minus_et0 !== "mm"
    || units.temperature !== "°C" || units.duration !== "days" || units.share !== "%")
    throw new Error("The anticipation units could not be verified.");
  if (v.state === "unavailable") return;
  if (
    days.length !== 7 ||
    v.period_start !== days[0].date ||
    v.period_end !== days[6].date ||
    ![
      "precipitation_total_mm",
      "reference_et0_total_mm",
      "maximum_daily_precipitation_mm",
      "largest_episode_total_mm",
    ].every((k) => finite(v[k], 0, 14000)) ||
    !finite(v.precipitation_minus_et0_mm, -14000, 14000) ||
    ![
      "low_precipitation_days",
      "longest_low_precipitation_run_days",
      "precipitation_days",
      "precipitation_episode_count",
    ].every((k) => finite(v[k], 0, 7) && Number.isInteger(v[k])) ||
    !["wettest_day_share_percent", "largest_episode_share_percent"].every(
      (k) => v[k] === null || finite(v[k], 0, 100),
    ) ||
    !(
      v.wettest_day_date === null ||
      days.some((d) => d.date === v.wettest_day_date)
    ) ||
    !finite(v.temperature_min_c, -100, 80) ||
    !finite(v.temperature_max_c, -100, 80) ||
    v.temperature_min_c > v.temperature_max_c
  )
    throw new Error("An anticipation indicator could not be verified.");
}
export const roundedPoint = (p: Point): Point => ({ latitude: Math.round(p.latitude * 1000) / 1000, longitude: Math.round(p.longitude * 1000) / 1000 });
export const samePoint = (a: Point, b: Point) => a.latitude === b.latitude && a.longitude === b.longitude;
export async function fetchFieldContext(point: Point, csrf: string, signal: AbortSignal): Promise<FieldContextData> {
  const controller = new AbortController(), cancel = () => controller.abort();
  if (signal.aborted) controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(cancel, 15000);
  try {
    const base = (import.meta.env.VITE_API_URL || '/solquira-api').replace(/\/$/, '');
    const response = await fetch(`${base}/studio/location`, { method: 'POST', credentials: 'same-origin', signal: controller.signal,
      headers: { 'Content-Type': 'application/json', 'X-Solquira-CSRF': csrf }, body: JSON.stringify(point) });
    if (!response.ok) throw new Error(response.status === 429 ? 'The location request limit has been reached. Try again later.' : 'The location service could not complete this request. Retry without changing your example.');
    const v: unknown = await response.json();
    if (!object(v) || v.schema_version !== 'solquira.location/1' || !object(v.location) || !finite(v.location.latitude, -90, 90) || !finite(v.location.longitude, -180, 180)
      || v.location.precision_degrees !== .001 || !samePoint(v.location as unknown as Point, point) || typeof v.retrieved_at !== 'string' || !Number.isFinite(Date.parse(v.retrieved_at)) || !object(v.weather) || !object(v.imagery)) throw new Error('The returned location could not be verified.');
    const w = v.weather, i = v.imagery;
    if (v.context_ref !== undefined && v.context_ref !== null && (typeof v.context_ref !== 'string' || !/^slq-context-[A-Za-z0-9_-]{24,80}$/.test(v.context_ref))) throw new Error('The context reference could not be verified.');
    if (!['ready','cached','unavailable','disabled'].includes(String(w.state)) || w.source !== 'Open-Meteo' || w.kind !== 'model_forecast' || w.time_zone !== 'UTC' || !Array.isArray(w.days)) throw new Error('The forecast source could not be verified.');
    if (w.days.length !== (['ready','cached'].includes(String(w.state)) ? 7 : 0)) throw new Error('The forecast is incomplete.');
    if (w.note !== undefined && (typeof w.note !== 'string' || w.note.length > 1000)) throw new Error('The source status could not be verified.');
    let previousDay: number | undefined;
    for (const day of w.days) {
      if (!object(day) || typeof day.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(day.date) || !finite(day.temperature_min_c,-100,80) || !finite(day.temperature_max_c,-100,80) || day.temperature_min_c > day.temperature_max_c || !finite(day.precipitation_mm,0,2000) || !finite(day.reference_et0_mm,0,100)) throw new Error('A forecast value could not be verified.');
      const stamp=Date.parse(`${day.date}T00:00:00Z`);
      if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0,10) !== day.date || previousDay !== undefined && stamp-previousDay !== 86400000) throw new Error('The forecast dates could not be verified.');
      previousDay=stamp;
    }
    if (i.source !== 'NASA GIBS' || !['ready_to_load','disabled'].includes(String(i.state)) || i.layer !== 'MODIS_Terra_CorrectedReflectance_TrueColor' || i.projection !== 'EPSG:3857' || i.tile_matrix_set !== 'GoogleMapsCompatible_Level9' || typeof i.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(i.date) || i.max_level !== 9 || i.url_template !== `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${i.date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`) throw new Error('The satellite layer could not be verified.');
    if (v.anticipation !== undefined) verifyAnticipation(v.anticipation, w.days as Day[], v.retrieved_at);
    return v as unknown as FieldContextData;
  } catch (error) {
    if (signal.aborted) throw error;
    if (controller.signal.aborted) throw new Error('The location request took too long. Retry when the service is ready.');
    throw error instanceof Error ? error : new Error('The location service could not be reached.');
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel); }
}
