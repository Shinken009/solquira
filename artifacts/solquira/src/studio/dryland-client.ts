export const NANO_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B";
export type DrylandScenario = "dry" | "rain";
// One label per place, shared by the map selector and the brief receipt. The four
// coordinates are prepared reference profiles, never a measurement of the user's land.
export const REFERENCE_PLACES = [
  {
    id: "ouagadougou",
    label: "Sahel reference",
    latitude: 12.371,
    longitude: -1.519,
  },
  {
    id: "andalusia",
    label: "Mediterranean farmland",
    latitude: 37.1,
    longitude: -4.6,
  },
  {
    id: "east_african_plateau",
    label: "East African plateau",
    latitude: -0.5,
    longitude: 36.9,
  },
] as const;
export const referencePlaceAt = (
  point: { latitude: number; longitude: number } | null,
) =>
  point
    ? REFERENCE_PLACES.find(
        (item) =>
          item.latitude === point.latitude &&
          item.longitude === point.longitude,
      )
    : undefined;
export type DrylandPlace = (typeof REFERENCE_PLACES)[number]["id"];
export type DrylandProgress = {
  result: DrylandResult;
  phase: "skeleton" | "field" | "final" | "interrupted";
  updatedAction?: string;
};
type SoilLife = "Strong" | "Building" | "Weak";
type WaterHeld = "Good" | "Fair" | "Low";
type ActionId =
  | "zai_pits"
  | "half_moons"
  | "living_cover"
  | "mulch"
  | "biocrust_check"
  | "delay_sowing";
type PathwayId =
  "sorghum_cowpea" | "millet_cowpea" | "quinoa" | "opuntia" | "amaranth";
export interface DrylandPayload {
  lat: number;
  lon: number;
  soc_band: SoilLife;
  awc_band: WaterHeld;
  rain_phrase: string;
  dry_days_ahead: number;
  fao_crops: PathwayId[];
}
export interface DrylandBrief {
  source: "model" | "fallback";
  vital_signs: {
    soil_life: SoilLife;
    water_held: WaterHeld;
    rain_outlook: string;
  };
  actions: {
    id: ActionId;
    title: string;
    detail: string;
    labor: "low" | "medium" | "high";
    done: boolean;
  }[];
  pathways: { id: PathwayId; name: string; why: string; water_note: string }[];
}
export interface DrylandReceipt {
  evidence: "synthetic-golden-path";
  scenario: DrylandScenario;
  outcome:
    | "success"
    | "disabled"
    | "timeout"
    | "invalid_output"
    | "budget_limit"
    | "budget_unavailable"
    | "provider_error";
  inference: "nebius_live" | "provider_stub" | "disabled";
  model: string | null;
  latencyMs: number;
  attempt: "none" | "reserved" | "rejected" | "returned" | "in_flight";
  reserved: "0" | "1" | "unknown";
  transitions: string;
  soil?: "reference-table";
  place?: DrylandPlace;
  modelActions?: number;
  payloadHash?: string;
}
export interface DrylandResult {
  brief: DrylandBrief;
  payload: DrylandPayload;
  receipt: DrylandReceipt;
}

const record = (v: unknown): v is Record<string, unknown> =>
  Boolean(v) && typeof v === "object" && !Array.isArray(v);
const string = (v: unknown, min: number, max: number): v is string =>
  typeof v === "string" &&
  v.length >= min &&
  v.length <= max &&
  !/[\u0000-\u001f]/.test(v);
const oneOf = (v: unknown, values: readonly string[]) =>
  typeof v === "string" && values.includes(v);
const actions = [
  "zai_pits",
  "half_moons",
  "living_cover",
  "mulch",
  "biocrust_check",
  "delay_sowing",
];
const pathways = [
  "sorghum_cowpea",
  "millet_cowpea",
  "quinoa",
  "opuntia",
  "amaranth",
];
const failure = () =>
  new Error(
    "The field brief could not be verified. Build it again to refresh the evidence.",
  );

export function parsePayload(value: unknown): DrylandPayload {
  if (
    !record(value) ||
    typeof value.lat !== "number" ||
    typeof value.lon !== "number" ||
    !Number.isFinite(value.lat) ||
    !Number.isFinite(value.lon) ||
    Math.abs(value.lat) > 90 ||
    Math.abs(value.lon) > 180 ||
    !oneOf(value.soc_band, ["Strong", "Building", "Weak"]) ||
    !oneOf(value.awc_band, ["Good", "Fair", "Low"]) ||
    !string(value.rain_phrase, 8, 72) ||
    typeof value.dry_days_ahead !== "number" ||
    !Number.isInteger(value.dry_days_ahead) ||
    value.dry_days_ahead < 0 ||
    value.dry_days_ahead > 7 ||
    !Array.isArray(value.fao_crops) ||
    value.fao_crops.length < 3 ||
    value.fao_crops.length > 5 ||
    value.fao_crops.some((id) => !oneOf(id, pathways)) ||
    new Set(value.fao_crops).size !== value.fao_crops.length
  )
    throw failure();
  return value as unknown as DrylandPayload;
}

export function parseDrylandBrief(
  value: unknown,
  payload: DrylandPayload,
): DrylandBrief {
  if (
    !record(value) ||
    !oneOf(value.source, ["model", "fallback"]) ||
    !record(value.vital_signs) ||
    value.vital_signs.soil_life !== payload.soc_band ||
    value.vital_signs.water_held !== payload.awc_band ||
    value.vital_signs.rain_outlook !== payload.rain_phrase ||
    !Array.isArray(value.actions) ||
    value.actions.length < 3 ||
    value.actions.length > 5 ||
    !Array.isArray(value.pathways) ||
    value.pathways.length !== 3
  )
    throw failure();
  const actionIds = new Set<string>(),
    pathIds = new Set<string>();
  for (const action of value.actions) {
    if (
      !record(action) ||
      !oneOf(action.id, actions) ||
      actionIds.has(String(action.id)) ||
      !string(action.title, 4, 48) ||
      !string(action.detail, 8, 120) ||
      !oneOf(action.labor, ["low", "medium", "high"]) ||
      typeof action.done !== "boolean"
    )
      throw failure();
    actionIds.add(String(action.id));
  }
  if (!actionIds.has("biocrust_check")) throw failure();
  for (const path of value.pathways) {
    if (
      !record(path) ||
      !oneOf(path.id, pathways) ||
      !payload.fao_crops.includes(path.id as PathwayId) ||
      pathIds.has(String(path.id)) ||
      !string(path.name, 3, 40) ||
      !string(path.why, 8, 110) ||
      !string(path.water_note, 8, 80)
    )
      throw failure();
    pathIds.add(String(path.id));
  }
  return value as unknown as DrylandBrief;
}

function parseReceipt(
  headers: Headers,
  scenario: DrylandScenario,
  place?: DrylandPlace,
): DrylandReceipt {
  const evidence = headers.get("X-Solquira-Evidence"),
    returnedScenario = headers.get("X-Solquira-Scenario");
  const outcome = headers.get("X-Solquira-Outcome"),
    inference = headers.get("X-Solquira-Inference");
  const model = headers.get("X-Solquira-Model") || null,
    latency = headers.get("X-Solquira-Latency-Ms");
  if (
    evidence !== "synthetic-golden-path" ||
    returnedScenario !== scenario ||
    !oneOf(outcome, [
      "success",
      "disabled",
      "timeout",
      "invalid_output",
      "budget_limit",
      "budget_unavailable",
      "provider_error",
    ]) ||
    !oneOf(inference, ["nebius_live", "provider_stub", "disabled"]) ||
    (model !== null && model !== NANO_MODEL) ||
    latency === null ||
    !/^\d{1,7}$/.test(latency)
  )
    throw failure();
  const attempt = headers.get("X-Solquira-Attempt"),
    reserved = headers.get("X-Solquira-Reserved");
  const transitions = headers.get("X-Solquira-Transitions");
  if (
    !oneOf(attempt, [
      "none",
      "reserved",
      "rejected",
      "returned",
      "in_flight",
    ]) ||
    !oneOf(reserved, ["0", "1", "unknown"]) ||
    !transitions ||
    !/^[a-z_:,-]{1,200}$/.test(transitions)
  )
    throw failure();
  const soil = headers.get("X-Solquira-Soil") || undefined,
    returnedPlace = headers.get("X-Solquira-Place") || undefined;
  const modelActions = headers.get("X-Solquira-Model-Actions"),
    payloadHash = headers.get("X-Solquira-Payload-Hash") || undefined;
  if (
    (soil !== undefined && soil !== "reference-table") ||
    (returnedPlace !== undefined &&
      !REFERENCE_PLACES.some((item) => item.id === returnedPlace)) ||
    (place && returnedPlace && place !== returnedPlace) ||
    (payloadHash && !/^[a-f0-9]{64}$/.test(payloadHash)) ||
    (modelActions !== null && !/^[0-5]$/.test(modelActions))
  )
    throw failure();
  return {
    evidence,
    scenario,
    outcome,
    inference,
    model,
    latencyMs: Number(latency),
    attempt,
    reserved,
    transitions,
    soil,
    place: returnedPlace,
    modelActions: modelActions === null ? undefined : Number(modelActions),
    payloadHash,
  } as DrylandReceipt;
}

function receiptFromObject(
  value: unknown,
  scenario: DrylandScenario,
  place: DrylandPlace,
): DrylandReceipt {
  if (!record(value)) throw failure();
  const headers = new Headers();
  const fields: Record<string, string> = {
    evidence: "Evidence",
    scenario: "Scenario",
    outcome: "Outcome",
    inference: "Inference",
    model: "Model",
    latencyMs: "Latency-Ms",
    attempt: "Attempt",
    reserved: "Reserved",
    transitions: "Transitions",
    soil: "Soil",
    place: "Place",
    modelActions: "Model-Actions",
    payloadHash: "Payload-Hash",
  };
  for (const [key, suffix] of Object.entries(fields))
    if (value[key] !== undefined && value[key] !== null)
      headers.set(`X-Solquira-${suffix}`, String(value[key]));
  return parseReceipt(headers, scenario, place);
}

function resultFromObject(
  value: unknown,
  payload: DrylandPayload,
  scenario: DrylandScenario,
  place: DrylandPlace,
): DrylandResult {
  if (!record(value)) throw failure();
  const brief = parseDrylandBrief(value.brief, payload),
    receipt = receiptFromObject(value.receipt, scenario, place);
  if (
    brief.source === "model" &&
    (receipt.outcome !== "success" ||
      receipt.inference === "disabled" ||
      receipt.model !== NANO_MODEL)
  )
    throw failure();
  if (brief.source === "fallback" && receipt.outcome === "success")
    throw failure();
  return { brief, payload, receipt };
}

// Field patches carry complete actions, never tokens or repaired partial JSON.
function patchAction(
  value: unknown,
  result: DrylandResult,
  seen: Set<string>,
): { result: DrylandResult; id: string } {
  if (
    !record(value) ||
    value.validated !== true ||
    value.wording !== "model" ||
    typeof value.path !== "string" ||
    !/^actions\.[0-4]$/.test(value.path) ||
    seen.has(value.path) ||
    !record(value.value)
  )
    throw failure();
  const index = Number(value.path.split(".")[1]),
    original = result.brief.actions[index],
    action = value.value;
  if (
    !original ||
    action.id !== original.id ||
    action.title !== original.title ||
    action.labor !== original.labor ||
    action.done !== false ||
    !string(action.detail, 8, 80) ||
    /[\d%<>{}]|https?:|www\.|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|mm|kg|ha|percent|hectares?|pesticides?|herbicides?|glyphosate)\b/i.test(
      action.detail,
    )
  )
    throw failure();
  const brief = {
    ...result.brief,
    actions: result.brief.actions.map((item, i) =>
      i === index
        ? (action as unknown as DrylandBrief["actions"][number])
        : item,
    ),
  };
  parseDrylandBrief(brief, result.payload);
  seen.add(value.path);
  return { result: { ...result, brief }, id: original.id };
}

export async function buildDrylandBrief(
  scenario: DrylandScenario,
  csrf: string,
  signal: AbortSignal,
  place: DrylandPlace = "ouagadougou",
  onProgress?: (update: DrylandProgress) => void,
): Promise<DrylandResult> {
  const controller = new AbortController(),
    cancel = () => controller.abort();
  if (signal.aborted) controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(cancel, 6500);
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  let skeleton: DrylandResult | null = null;
  const request = async (path: string, body?: DrylandPayload) => {
    const response = await fetch(`${base}/v1/${path}`, {
      method: body ? "POST" : "GET",
      signal: controller.signal,
      credentials: "same-origin",
      headers: {
        "X-Solquira-CSRF": csrf,
        ...(body
          ? { "Content-Type": "application/json", Accept: "text/event-stream" }
          : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        401: "Reconnect the Studio to refresh your session.",
        403: "Reconnect the Studio to verify this request.",
        409: "The example changed. Build the field brief again.",
        429: "The workspace is busy. Wait a moment before building another brief.",
      };
      throw new Error(
        messages[response.status] ?? "The brief did not build. Try again.",
      );
    }
    return response;
  };
  try {
    const context = await request(
        `field-payload?scenario=${scenario}&place=${place}`,
      ),
      payload = parsePayload(await context.json());
    const reference = REFERENCE_PLACES.find((item) => item.id === place)!;
    if (
      payload.lat !== reference.latitude ||
      payload.lon !== reference.longitude
    )
      throw failure();
    const response = await request("field-brief", payload);
    if (
      !response.headers.get("Content-Type")?.startsWith("text/event-stream") ||
      !response.body
    )
      throw failure();
    const reader = response.body.getReader(),
      decoder = new TextDecoder("utf-8", { fatal: true }),
      seen = new Set<string>();
    let pending = "",
      current: DrylandResult | null = null,
      count = 0,
      bytes = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        bytes += value.length;
        if (bytes > 65536) throw failure();
        pending += decoder.decode(value, { stream: true });
        // CRLF is normalized only once complete, so chunk boundaries are safe.
        let boundary: RegExpMatchArray | null;
        while ((boundary = /\r?\n\r?\n/.exec(pending))) {
          const frame = pending.slice(0, boundary.index),
            end = boundary.index! + boundary[0].length;
          pending = pending.slice(end);
          if (
            !frame.trim() ||
            frame.split(/\r?\n/).every((line) => line.startsWith(":"))
          )
            continue;
          if (++count > 32) throw failure();
          const lines = frame.split(/\r?\n/),
            eventLines = lines.filter((line) => line.startsWith("event:"));
          if (
            eventLines.length !== 1 ||
            lines.some(
              (line) =>
                !line.startsWith("event:") &&
                !line.startsWith("data:") &&
                !line.startsWith(":"),
            )
          )
            throw failure();
          const event = eventLines[0].slice(6).trim(),
            raw = lines
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart())
              .join("\n");
          const data: unknown = JSON.parse(raw);
          if (event === "skeleton") {
            if (skeleton) throw failure();
            current = resultFromObject(data, payload, scenario, place);
            if (current.brief.source !== "fallback") throw failure();
            skeleton = current;
            onProgress?.({ result: current, phase: "skeleton" });
          } else if (event === "field") {
            if (!current || !skeleton) throw failure();
            const patched = patchAction(data, current, seen);
            current = patched.result;
            onProgress?.({
              result: current,
              phase: "field",
              updatedAction: patched.id,
            });
          } else if (event === "degraded") {
            if (
              !skeleton ||
              !record(data) ||
              typeof data.path !== "string" ||
              !/^(brief|actions\.[0-4])$/.test(data.path) ||
              !oneOf(data.reason, [
                "timeout",
                "rejected",
                "disabled",
                "budget_limit",
                "budget_unavailable",
                "provider_error",
              ])
            )
              throw failure();
          } else if (event === "final") {
            if (!skeleton) throw failure();
            const final = resultFromObject(data, payload, scenario, place);
            onProgress?.({ result: final, phase: "final" });
            return final;
          } else throw failure();
        }
      }
      throw new Error(
        "Composition stopped. Field rules remain visible; build again for a complete receipt.",
      );
    } finally {
      await reader.cancel().catch(() => undefined);
      reader.releaseLock();
    }
  } catch (error) {
    if (!signal.aborted && skeleton)
      onProgress?.({ result: skeleton, phase: "interrupted" });
    if (signal.aborted) throw error;
    if (controller.signal.aborted)
      throw new Error(
        "Composition stopped. Field rules remain visible; build again when the connection is ready.",
      );
    throw error instanceof Error ? error : failure();
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}
