import { useStudioCopy } from "../StudioPreferences";
import { GEO_COPY } from "./geo-copy";
import type { Anticipation } from "./client";

export default function AnticipationIndicators({
  data,
}: {
  data?: Anticipation;
}) {
  const { t, locale } = useStudioCopy(GEO_COPY);
  const value = (n: number | undefined, digits = 1) => n === undefined ? t("Not available") : new Intl.NumberFormat(locale === "es" ? "es-CL" : "en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(n);
  if (!data || data.state !== "ready")
    return (
      <p className="qg-weather-empty" data-testid="anticipation-unavailable">
        {t("Complete weekly indicators are unavailable for this forecast. You can retry the forecast from Territory.")}</p>
    );
  return (
    <section
      className="qg-indicators"
      aria-labelledby="qg-indicators-title"
      data-testid="anticipation-indicators"
    >
      <h3 id="qg-indicators-title">{t("What to check this week")}</h3>
      <p className="qg-source-note">
        {data.source} {t("· model forecast ·")}{" "}{data.period_start}–{data.period_end} {t("· UTC")}</p>
      <dl className="qg-indicator-grid">
        <div>
          <dt>{t("Precipitation − reference ET₀")}</dt>
          <dd data-testid="anticipation-difference">
            {value(data.precipitation_minus_et0_mm)} <small>{t("mm")}</small>
          </dd>
          <p>
            {t("Atmospheric comparison only; not soil moisture or irrigation need.")}</p>
        </div>
        <div>
          <dt>{t("Low-precipitation days")}</dt>
          <dd data-testid="anticipation-low-days">
            {value(data.low_precipitation_days, 0)} <small>{t("/ 7 days")}</small>
          </dd>
          <p>
            {t("Less than")}{" "}{data.thresholds.low_precipitation_mm_per_day} {t("mm/day; longest run")}{" "}{value(data.longest_low_precipitation_run_days, 0)}{" "}
            {t("days.")}</p>
        </div>
        <div>
          <dt>{t("Forecast temperature range")}</dt>
          <dd>
            {value(data.temperature_min_c)}–{value(data.temperature_max_c)}{" "}
            <small>{t("°C")}</small>
          </dd>
          <p>
            {t("Minimum and maximum across the period, not crop-specific heat stress.")}</p>
        </div>
        <div>
          <dt>{t("Precipitation episodes")}</dt>
          <dd data-testid="anticipation-episodes">
            {value(data.precipitation_episode_count, 0)}
          </dd>
          <p>
            {t("Consecutive days with at least")}{" "}
            {data.thresholds.wet_episode_min_mm_per_day} {t("mm/day. Daily totals cannot establish storm intensity.")}</p>
        </div>
        <div>
          <dt>{t("Wettest day")}</dt>
          <dd>
            {value(data.maximum_daily_precipitation_mm)} <small>{t("mm")}</small>
          </dd>
          <p>
            {data.wettest_day_date ?? t("No precipitation in this forecast")} ·{" "}
            {data.wettest_day_share_percent === null
              ? t("Share undefined when the total is zero.")
              : `${value(data.wettest_day_share_percent)}${t("% of the period total.")}`}
          </p>
        </div>
        <div>
          <dt>{t("Largest wet episode")}</dt>
          <dd>
            {value(data.largest_episode_total_mm)} <small>{t("mm")}</small>
          </dd>
          <p>
            {data.largest_episode_share_percent === null
              ? t("No episode reaches the declared threshold; share not available.")
              : `${value(data.largest_episode_share_percent)}${t("% of the period total.")}`}
          </p>
        </div>
      </dl>
      <p className="qg-interpretation">{t("Use these indicators to plan observations, not an irrigation dose.")}</p>
      <details className="qg-method">
        <summary>{t("Method, missing inputs & limits")}</summary>
        <p>{t(data.method)}.</p>
        <p>{t("A dry week does not diagnose drought or desertification. Precipitation is not the water stored in the root zone.")}</p>
        <h4>{t("Missing field evidence")}</h4>
        <ul>
          {data.missing_inputs.map((item) => (
            <li key={item}>{t(item)}</li>
          ))}
        </ul>
        <h4>{t("Interpretation limits")}</h4>
        <ul>
          {data.limitations.map((item) => (
            <li key={item}>{t(item)}</li>
          ))}
        </ul>
        <p>
          {t("Retrieved")}{" "}{data.retrieved_at} ·{" "}
          <a href={data.source_url} target="_blank" rel="noreferrer">
            {t("Source and attribution")}</a>
        </p>
      </details>
    </section>
  );
}
