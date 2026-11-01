import { useEffect, useRef, useState } from "react";
import type { Point } from "./client";

type HistoryResult = {
  schema_version: "solquira.history/1";
  location: Point;
  retrieved_at: string;
  history: {
    state: "ready" | "cached" | "disabled" | "unavailable";
    source: string;
    model: string;
    kind: string;
    time_zone: string;
    units: string;
    note?: string;
    target_period?: { start: string; end: string };
    baseline?: {
      start_year: number;
      end_year: number;
      calendar_month: number;
      years_required: number;
      complete_years: number;
    };
    precipitation_mm?: number;
    baseline_mean_mm?: number;
    anomaly_mm?: number;
    anomaly_percent?: number | null;
    coverage?: {
      target_days: number;
      target_expected_days: number;
      baseline_complete_years: number;
    };
    limitations?: string[];
  };
};
const object = (v: unknown): v is Record<string, unknown> =>
  Boolean(v) && typeof v === "object" && !Array.isArray(v);
const finite = (v: unknown, min: number, max: number): v is number =>
  typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;

async function loadHistory(
  point: Point,
  csrf: string,
  signal: AbortSignal,
): Promise<HistoryResult> {
  const base = (import.meta.env.VITE_API_URL || "/solquira-api").replace(
    /\/$/,
    "",
  );
  const response = await fetch(`${base}/studio/history`, {
    method: "POST",
    credentials: "same-origin",
    signal,
    headers: { "Content-Type": "application/json", "X-Solquira-CSRF": csrf },
    body: JSON.stringify(point),
  });
  if (!response.ok)
    throw new Error(
      response.status === 429
        ? "The history request limit has been reached. Retry later."
        : "Historical context could not be loaded. Your forecast and reference examples remain unchanged.",
    );
  const value: unknown = await response.json();
  if (
    !object(value) ||
    value.schema_version !== "solquira.history/1" ||
    !object(value.location) ||
    value.location.latitude !== point.latitude ||
    value.location.longitude !== point.longitude ||
    typeof value.retrieved_at !== "string" ||
    !Number.isFinite(Date.parse(value.retrieved_at)) ||
    !object(value.history)
  )
    throw new Error("The historical source could not be verified.");
  const h = value.history;
  if (
    !["ready", "cached", "disabled", "unavailable"].includes(String(h.state)) ||
    h.source !== "Open-Meteo" ||
    h.model !== "ERA5" ||
    h.kind !== "model_reanalysis" ||
    h.time_zone !== "UTC" ||
    h.units !== "mm"
  )
    throw new Error("The historical method could not be verified.");
  if (
    h.note !== undefined &&
    (typeof h.note !== "string" || h.note.length > 1500)
  )
    throw new Error("The historical status could not be verified.");
  if (["ready", "cached"].includes(String(h.state))) {
    if (
      !object(h.baseline) ||
      h.baseline.start_year !== 1991 ||
      h.baseline.end_year !== 2020 ||
      h.baseline.complete_years !== 30 ||
      !object(h.target_period) ||
      typeof h.target_period.start !== "string" ||
      typeof h.target_period.end !== "string" ||
      !finite(h.precipitation_mm, 0, 62000) ||
      !finite(h.baseline_mean_mm, 0, 62000) ||
      !finite(h.anomaly_mm, -62000, 62000) ||
      !(h.anomaly_percent === null || finite(h.anomaly_percent, -100, 1e9)) ||
      !object(h.coverage) ||
      h.coverage.target_days !== h.coverage.target_expected_days ||
      h.coverage.baseline_complete_years !== 30 ||
      !Array.isArray(h.limitations) ||
      !h.limitations.every(
        (item) => typeof item === "string" && item.length <= 1500,
      )
    )
      throw new Error("The historical comparison is incomplete.");
  }
  return value as unknown as HistoryResult;
}

export default function HistoricalContext({
  point,
  enabled,
  csrf,
}: {
  point: Point | null;
  enabled: boolean;
  csrf: string;
}) {
  const [result, setResult] = useState<HistoryResult | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const previous = controller.current;
    controller.current = null;
    previous?.abort();
    setResult(null);
    setError("");
    setBusy(false);
    return () => {
      const pending = controller.current;
      controller.current = null;
      pending?.abort();
    };
  }, [point?.latitude, point?.longitude, csrf]);
  const load = async () => {
    if (!point || !enabled || !csrf || busy) return;
    const request = new AbortController();
    controller.current = request;
    const timer = setTimeout(() => request.abort(), 30000);
    setBusy(true);
    setError("");
    try {
      const data = await loadHistory(point, csrf, request.signal);
      if (!request.signal.aborted) setResult(data);
    } catch (caught) {
      if (controller.current === request)
        setError(
          request.signal.aborted
            ? "Historical context took too long. Retry when ready."
            : caught instanceof Error
              ? caught.message
              : "History is unavailable.",
        );
    } finally {
      clearTimeout(timer);
      if (controller.current === request) setBusy(false);
    }
  };
  const history = result?.history;
  return (
    <section
      className="qg-history"
      aria-labelledby="qg-history-title"
      data-testid="historical-context"
      aria-busy={busy}
    >
      <h3 id="qg-history-title">Look beyond the forecast</h3>
      <p>
        Compare the latest available complete month of precipitation with the
        same calendar month in 1991–2020. This is a separate reanalysis, not a
        station observation or a desertification diagnosis.
      </p>
      <button
        type="button"
        className="qg-open"
        data-testid="history-load"
        disabled={!point || !enabled || !csrf || busy}
        onClick={load}
      >
        {busy
          ? "Loading historical precipitation…"
          : result
            ? "Refresh historical comparison"
            : "Load historical precipitation"}
      </button>
      <p className="qg-source-note">
        Open-Meteo · ERA5 reanalysis · loaded only when requested. No model
        composition. {!point && "Choose a point in Territorio first."}
      </p>
      <p className="qg-source-note">
        Large historical lookups use additional source quota; recent results may
        be cached. Missing data are not zero precipitation.
      </p>
      {error && (
        <p role="alert" className="qg-error">
          {error}
        </p>
      )}
      {history && (history.state === "ready" || history.state === "cached") ? (
        <div data-testid="history-result">
          <p>
            <strong>
              {history.target_period?.start}–{history.target_period?.end}
            </strong>{" "}
            · UTC ·{" "}
            {history.state === "cached" ? "Cached source" : "Source received"}
          </p>
          <dl className="qg-indicator-grid">
            <div>
              <dt>Month precipitation</dt>
              <dd>
                {history.precipitation_mm?.toFixed(1)} <small>mm</small>
              </dd>
            </div>
            <div>
              <dt>Same-month baseline mean</dt>
              <dd>
                {history.baseline_mean_mm?.toFixed(1)} <small>mm</small>
              </dd>
              <p>1991–2020 · 30 complete years</p>
            </div>
            <div>
              <dt>Difference from baseline</dt>
              <dd>
                {history.anomaly_mm?.toFixed(1)} <small>mm</small>
              </dd>
              <p>
                {history.anomaly_percent === null
                  ? "Relative difference is undefined for a zero baseline."
                  : `${history.anomaly_percent?.toFixed(1)}% relative to the baseline mean.`}
              </p>
            </div>
          </dl>
          <details className="qg-method">
            <summary>Historical coverage & limits</summary>
            <p>
              {history.coverage?.target_days} of{" "}
              {history.coverage?.target_expected_days} target days;{" "}
              {history.coverage?.baseline_complete_years} complete baseline
              years. Retrieved {result?.retrieved_at}.
            </p>
            <ul>
              {history.limitations?.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
            <a
              href="https://open-meteo.com/en/docs/historical-weather-api"
              target="_blank"
              rel="noreferrer"
            >
              Historical source and attribution
            </a>
          </details>
        </div>
      ) : history ? (
        <p className="qg-weather-empty" data-testid="history-unavailable">
          {history.note ??
            "A complete historical comparison is unavailable; no anomaly has been invented."}
        </p>
      ) : null}
    </section>
  );
}
