import type { Anticipation } from "./client";

const value = (n: number | undefined, digits = 1) =>
  n === undefined ? "Not available" : n.toFixed(digits);
export default function AnticipationIndicators({
  data,
}: {
  data?: Anticipation;
}) {
  if (!data || data.state !== "ready")
    return (
      <p className="qg-weather-empty" data-testid="anticipation-unavailable">
        Seven-day indicators need a complete verified forecast. Choose a place
        in Territorio and load its context; missing values are never shown as
        zero.
      </p>
    );
  return (
    <section
      className="qg-indicators"
      aria-labelledby="qg-indicators-title"
      data-testid="anticipation-indicators"
    >
      <h3 id="qg-indicators-title">Environmental anticipation</h3>
      <p className="qg-source-note">
        {data.source} · model forecast · {data.period_start}–{data.period_end} ·
        UTC
      </p>
      <dl className="qg-indicator-grid">
        <div>
          <dt>Precipitation − reference ET₀</dt>
          <dd data-testid="anticipation-difference">
            {value(data.precipitation_minus_et0_mm)} <small>mm</small>
          </dd>
          <p>
            Atmospheric comparison only; not soil moisture or irrigation need.
          </p>
        </div>
        <div>
          <dt>Low-precipitation days</dt>
          <dd data-testid="anticipation-low-days">
            {value(data.low_precipitation_days, 0)} <small>/ 7 days</small>
          </dd>
          <p>
            Less than {data.thresholds.low_precipitation_mm_per_day} mm/day;
            longest run {value(data.longest_low_precipitation_run_days, 0)}{" "}
            days.
          </p>
        </div>
        <div>
          <dt>Forecast temperature range</dt>
          <dd>
            {value(data.temperature_min_c)}–{value(data.temperature_max_c)}{" "}
            <small>°C</small>
          </dd>
          <p>
            Minimum and maximum across the period, not crop-specific heat
            stress.
          </p>
        </div>
        <div>
          <dt>Precipitation episodes</dt>
          <dd data-testid="anticipation-episodes">
            {value(data.precipitation_episode_count, 0)}
          </dd>
          <p>
            Consecutive days with at least{" "}
            {data.thresholds.wet_episode_min_mm_per_day} mm/day. Daily totals
            cannot establish storm intensity.
          </p>
        </div>
        <div>
          <dt>Wettest day</dt>
          <dd>
            {value(data.maximum_daily_precipitation_mm)} <small>mm</small>
          </dd>
          <p>
            {data.wettest_day_date ?? "No precipitation in this forecast"} ·{" "}
            {data.wettest_day_share_percent === null
              ? "Share undefined when the total is zero."
              : `${value(data.wettest_day_share_percent)}% of the period total.`}
          </p>
        </div>
        <div>
          <dt>Largest wet episode</dt>
          <dd>
            {value(data.largest_episode_total_mm)} <small>mm</small>
          </dd>
          <p>
            {data.largest_episode_share_percent === null
              ? "No episode reaches the declared threshold; share not available."
              : `${value(data.largest_episode_share_percent)}% of the period total.`}
          </p>
        </div>
      </dl>
      <p className="qg-interpretation">
        A dry week does not diagnose drought or desertification. Precipitation
        is not the water stored in the root zone.
      </p>
      <details className="qg-method">
        <summary>Method, missing inputs & limits</summary>
        <p>
          {data.method}. Indicators are calculated by Python; no AI composition
          is needed.
        </p>
        <h4>Missing field evidence</h4>
        <ul>
          {data.missing_inputs.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <h4>Interpretation limits</h4>
        <ul>
          {data.limitations.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <p>
          Retrieved {data.retrieved_at} ·{" "}
          <a href={data.source_url} target="_blank" rel="noreferrer">
            Source and attribution
          </a>
        </p>
      </details>
    </section>
  );
}
