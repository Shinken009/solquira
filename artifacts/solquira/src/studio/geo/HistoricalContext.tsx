import { useStudioCopy } from "../StudioPreferences";
import { GEO_COPY } from "./geo-copy";
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
  const { t, locale } = useStudioCopy(GEO_COPY);
  const number = (value: number | undefined) => value === undefined ? t("Not available") : new Intl.NumberFormat(locale === "es" ? "es-CL" : "en-US", {minimumFractionDigits: 1, maximumFractionDigits: 1}).format(value);
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
      <h3 id="qg-history-title">{t("Compare with previous years")}</h3>
      <p>
        {t("See how rainfall in the latest complete month compares with the same month in 1991–2020.")}</p>
      {point && enabled && csrf ? <button
        type="button"
        className="qg-open"
        data-testid="history-load"
        disabled={!point || !enabled || !csrf || busy}
        onClick={load}
      >
        {busy
          ? t("Loading historical precipitation…")
          : result
            ? t("Refresh historical comparison")
            : t("Load historical precipitation")}
      </button> : <p className="qg-source-note">{t("Confirm a place in Territory to compare its historical rainfall.")}</p>}
      <details className="qg-method">
        <summary>{t("Historical source, quota & method")}</summary>
        <p>{t("Open-Meteo · ERA5 reanalysis · loaded only when requested. No AI request. This is a separate model reanalysis, not a station observation or a desertification diagnosis.")}</p>
        <p>{t("Large historical lookups use additional source quota; recent results may be cached. Missing data are not zero precipitation.")}</p>
      </details>
      {error && (
        <p role="alert" className="qg-error">
          {t(error)}
        </p>
      )}
      {history && (history.state === "ready" || history.state === "cached") ? (
        <div data-testid="history-result">
          <p>
            <strong>
              {history.target_period?.start}–{history.target_period?.end}
            </strong>{" "}
            {t("· UTC ·")}{" "}
            {history.state === "cached" ? t("Cached source") : t("Source received")}
          </p>
          <dl className="qg-indicator-grid">
            <div>
              <dt>{t("Month precipitation")}</dt>
              <dd>
                {number(history.precipitation_mm)} <small>{t("mm")}</small>
              </dd>
            </div>
            <div>
              <dt>{t("Same-month baseline mean")}</dt>
              <dd>
                {number(history.baseline_mean_mm)} <small>{t("mm")}</small>
              </dd>
              <p>{t("1991–2020 · 30 complete years")}</p>
            </div>
            <div>
              <dt>{t("Difference from baseline")}</dt>
              <dd>
                {number(history.anomaly_mm)} <small>{t("mm")}</small>
              </dd>
              <p>
                {history.anomaly_percent === null
                  ? t("Relative difference is undefined for a zero baseline.")
                  : `${number(history.anomaly_percent)}${t("% relative to the baseline mean.")}`}
              </p>
            </div>
          </dl>
          <details className="qg-method">
            <summary>{t("Historical coverage & limits")}</summary>
            <p>
              {history.coverage?.target_days} {t("of")}{" "}{" "}
              {history.coverage?.target_expected_days} {t("target days;")}{" "}{" "}
              {history.coverage?.baseline_complete_years} {t("complete baseline years. Retrieved")}{" "}{result?.retrieved_at}.
            </p>
            <ul>
              {history.limitations?.map((item) => (
                <li key={item}>{t(item)}</li>
              ))}
            </ul>
            <a
              href="https://open-meteo.com/en/docs/historical-weather-api"
              target="_blank"
              rel="noreferrer"
            >
              {t("Historical source and attribution")}</a>
          </details>
        </div>
      ) : history ? (
        <p className="qg-weather-empty" data-testid="history-unavailable">
          {t(history.note ?? "A complete historical comparison is unavailable; no anomaly has been invented.")}
        </p>
      ) : null}
    </section>
  );
}
