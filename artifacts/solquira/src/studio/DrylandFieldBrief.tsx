import { usePanelCopy } from "./panel-copy";
import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronRight,
  ClipboardCheck,
  CloudSun,
  Droplets,
  Leaf,
  LoaderCircle,
  Sprout,
} from "lucide-react";
import {
  buildDrylandBrief,
  referencePlaceAt,
  type DrylandResult,
  type DrylandScenario,
  REFERENCE_PLACES,
} from "./dryland-client";
import "./dryland-field-brief.css";
import { bootstrapStudio } from "./client";
import type { Point } from "./geo/client";

const outcomes: Record<DrylandResult["receipt"]["outcome"], string> = {
  success: "Model composition, validated",
  disabled: "Field rules composed this brief",
  timeout: "Deadline reached; field rules composed this brief",
  invalid_output: "Output rejected; field rules composed this brief",
  budget_limit: "Budget guard active; field rules composed this brief",
  budget_unavailable: "Budget guard active; field rules composed this brief",
  provider_error: "Model connection ended; field rules composed this brief",
};

export default function DrylandFieldBrief({
  csrf,
  enabled,
  point,
  inference,
  onSelectPlace,
}: {
  csrf: string;
  enabled: boolean;
  point: Point | null;
  inference?: unknown;
  onSelectPlace?: (place: { latitude: number; longitude: number }) => void;
}) {
  const { t, locale, number, date } = usePanelCopy();
  const [scenario, setScenario] = useState<DrylandScenario>("dry");
  const [result, setResult] = useState<DrylandResult | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null),
    version = useRef(0);
  const place = referencePlaceAt(point);
  const [phase, setPhase] = useState("idle"),
    [updated, setUpdated] = useState<string[]>([]),
    [budget, setBudget] = useState<unknown>(inference);
  useEffect(() => setBudget(inference), [inference]);
  const state =
    budget && typeof budget === "object"
      ? (budget as Record<string, unknown>)
      : {};
  const counters = [
    state.calls_limit,
    state.calls_used,
    state.daily_calls_limit,
    state.daily_calls_used,
  ];
  const validBudget = counters.every(
    (v) => typeof v === "number" && Number.isSafeInteger(v) && v >= 0,
  );
  const remaining = validBudget
    ? Math.max(
        0,
        Math.min(
          Number(counters[0]) - Number(counters[1]),
          Number(counters[2]) - Number(counters[3]),
        ),
      )
    : null;
  useEffect(() => {
    ++version.current;
    controller.current?.abort();
    setResult(null);
    setChecked([]);
    setBusy(false);
    setError("");
    setPhase("idle");
    setUpdated([]);
    return () => {
      ++version.current;
      controller.current?.abort();
    };
  }, [csrf, enabled, place?.id]);
  const select = (value: DrylandScenario) => {
    if (value === scenario) return;
    ++version.current;
    controller.current?.abort();
    setScenario(value);
    setResult(null);
    setChecked([]);
    setBusy(false);
    setError("");
    setPhase("idle");
    setUpdated([]);
  };
  const build = async () => {
    if (!enabled || !csrf || busy || !place) return;
    controller.current?.abort();
    const current = ++version.current,
      pending = new AbortController();
    controller.current = pending;
    setBusy(true);
    setPhase("loading");
    setUpdated([]);
    setError("");
    setResult(null);
    setChecked([]);
    try {
      const value = await buildDrylandBrief(
        scenario,
        csrf,
        pending.signal,
        place.id,
        (update) => {
          if (pending.signal.aborted || current !== version.current) return;
          setResult(update.result);
          setPhase(update.phase);
          if (update.updatedAction)
            setUpdated((items) => [...items, update.updatedAction!]);
          if (update.phase === "interrupted") setUpdated([]);
        },
      );
      if (pending.signal.aborted || current !== version.current) return;
      setResult(value);
      setPhase("final");
      if (value.receipt.inference !== "disabled")
        void bootstrapStudio(pending.signal)
          .then((value) => {
            if (current === version.current && !pending.signal.aborted)
              setBudget(value.inference);
          })
          .catch(() => undefined);
    } catch (caught) {
      if (!pending.signal.aborted && current === version.current)
        setError(
          caught instanceof Error
            ? caught.message
            : "Build the brief again to refresh its evidence.",
        );
    } finally {
      if (!pending.signal.aborted && current === version.current)
        setBusy(false);
    }
  };
  const brief = result?.brief,
    receipt = result?.receipt;
  const provenance =
    brief?.source === "model"
      ? [
          "Numbers · Python",
          receipt?.inference === "provider_stub"
            ? "Wording · Test provider"
            : "Wording · Nemotron 3 Nano",
        ]
      : ["Numbers · Python", "Wording · Field rules"];
  return (
    <section
      className="qd-workspace"
      id="qs-field-vitals"
      aria-labelledby="qd-title"
      data-testid="dryland-workspace"
    >
      <header className="qd-heading">
        <div>
          <h2 id="qd-title">{t("Read the land. Choose the next step.")}</h2>
          <p>{t("A field brief that joins soil, water and the work ahead.")}</p>
        </div>
        <span className="qd-example">
          {place ? t(place.label) :
            (point
              ? t("Your point · no soil profile")
              : t("Choose a reference point"))}
        </span>
      </header>
      <div className="qd-setup">
        <div>
          <strong>{t("One place. Two possible weeks.")}</strong>
          <p>
            {place
              ? `${place.latitude}, ${place.longitude} ${t("· reference table")}`
              : point
                ? t("Map and forecast follow your point. The brief needs a reference landscape.")
                : t("Choose a reference landscape above to explore its prepared soil profile.")}
          </p>
          <div
            className="qd-scenarios"
            role="group"
            aria-label={t("Example weather pattern")}
          >
            <button
              type="button"
              aria-pressed={scenario === "dry"}
              onClick={() => select("dry")}
              data-testid="dryland-dry"
            >{t("Dry week")}</button>
            <button
              type="button"
              aria-pressed={scenario === "rain"}
              onClick={() => select("rain")}
              data-testid="dryland-rain"
            >{t("Rain may return")}</button>
          </div>
        </div>
        <button
          type="button"
          className="qd-build"
          disabled={!enabled || !csrf || busy || !place}
          onClick={() => void build()}
          data-testid="dryland-build"
        >
          {busy ? (
            <LoaderCircle size={18} className="qs-spin" />
          ) : (
            <ClipboardCheck size={18} />
          )}{" "}
          {busy ? t("Building your field brief…") : t("Build field brief")}
        </button>
      </div>
      {!place && (
        <div
          className="qd-reference-picker"
          data-testid="dryland-reference-picker"
        >
          <p>
            {point
              ? t("Your point has no prepared soil profile. The brief uses one of four synthetic reference landscapes; your coordinates and forecast stay yours.")
              : t("Choose one of the four prepared reference landscapes to compose a field brief.")}
          </p>
          <div className="qd-reference-options">
            {REFERENCE_PLACES.map((item) => (
              <button
                type="button"
                key={item.id}
                data-testid={`dryland-reference-${item.id}`}
                onClick={() =>
                  onSelectPlace?.({
                    latitude: item.latitude,
                    longitude: item.longitude,
                  })
                }
              >
                {t(item.label)}
              </button>
            ))}
          </div>
        </div>
      )}
      <p className="qd-live-status" role="status">
        {busy
          ? result
            ? t("Field rules are ready. Validated wording can now improve the checks.")
            : t("Preparing your field rules…")
          : result
            ? t("Brief ready. Mark the steps you want to bring to your next visit.")
            : t("Explore a prepared soil and weather scenario. Nothing is sent to the model until you build.")}
      </p>
      <p className="qd-budget" data-testid="dryland-budget">
        {remaining === null
          ? t("Field rules are ready without a model allowance.")
          : locale === "es" ? `${remaining} redacciones disponibles en la última consulta al servidor.` : `${remaining} compositions remain at the last server check.`}
      </p>
      {error && (
        <p className="qd-error" role="alert">
          {t(error)}
        </p>
      )}
      {brief && result && (
        <div
          className="qd-result"
          data-testid="dryland-result"
          data-source={brief.source}
          data-phase={phase}
        >
          <div className="qd-result-heading">
            <h3 id="qd-vitals-title">{t("Field vital signs")}</h3>
            <span className="qd-source" data-testid="dryland-source">
              {provenance.map((line) => (
                <span key={t(line)}>{t(line)}</span>
              ))}
            </span>
          </div>
          <div
            className="qd-vitals"
            aria-label={t("Field vital signs")}
            data-testid="field-vital-signs"
          >
            <div className="qd-vital-soil">
              <Leaf aria-hidden="true" />
              <span>{t("Soil life")}</span>
              <strong data-testid="vital-soil">
                {t(brief.vital_signs.soil_life)}
              </strong>
              <small>{t("Organic carbon band")}</small>
            </div>
            <div className="qd-vital-water">
              <Droplets aria-hidden="true" />
              <span>{t("Water held")}</span>
              <strong data-testid="vital-water">
                {t(brief.vital_signs.water_held)}
              </strong>
              <small>{t("Available water band")}</small>
            </div>
            <div className="qd-vital-rain">
              <CloudSun aria-hidden="true" />
              <span>{t("Rain outlook")}</span>
              <strong data-testid="vital-rain">
                {t(brief.vital_signs.rain_outlook)}
              </strong>
              <small>{t("Prepared scenario")}</small>
            </div>
          </div>
          <div className="qd-check-heading">
            <h3>{t("The work, in order")}</h3>
            <span aria-live="polite">
              {checked.length} {locale === "es" ? "de" : "of"} {brief.actions.length} {locale === "es" ? "marcados" : "marked"}
            </span>
          </div>
          <ol className="qd-actions" data-testid="dryland-checklist">
            {brief.actions.map((action, index) => (
              <li
                key={action.id}
                data-action={action.id}
                data-checked={checked.includes(action.id)}
                data-updated={updated.includes(action.id)}
              >
                <label>
                  <span className="qd-toggle">
                    <input
                      type="checkbox"
                      disabled={phase !== "final"}
                      checked={checked.includes(action.id)}
                      onChange={(event) =>
                        setChecked((items) =>
                          event.target.checked
                            ? [...items, action.id]
                            : items.filter((id) => id !== action.id),
                        )
                      }
                    />
                    <span aria-hidden="true">
                      {checked.includes(action.id) && <Check size={24} />}
                    </span>
                  </span>
                  <span className="qd-action-copy">
                    {index === 0 && (
                      <small className="qd-first">{t("Start here")}</small>
                    )}
                    <strong>{brief.source === "fallback" ? t(action.title) : action.title}</strong>
                    <span>{brief.source === "fallback" ? t(action.detail) : action.detail}</span>
                  </span>
                </label>
                <span className="qd-labor">{t(action.labor)}{t(" effort")}</span>
              </li>
            ))}
          </ol>
          <div className="qd-path-heading">
            <Sprout size={23} aria-hidden="true" />
            <div>
              <h3>{t("Explore crop pathways")}</h3>
              <p>{t("Compare what each option asks of your water.")}</p>
            </div>
          </div>
          <div className="qd-pathways" data-testid="dryland-pathways">
            {brief.pathways.map((path) => (
              <article key={path.id} data-pathway={path.id}>
                <h4>{t(path.name)}</h4>
                <p>{brief.source === "fallback" ? t(path.why) : path.why}</p>
                <span>
                  <Droplets size={16} aria-hidden="true" />
                  {t(path.water_note)}
                </span>
              </article>
            ))}
          </div>
          <details className="qd-receipt" data-testid="dryland-receipt">
            <summary>{t("Evidence & execution receipt ·")}{" "}
              {brief.source === "model" ? t("model composition") : t("field rules")}{" "}
              <ChevronRight size={16} />
            </summary>
            <p>{t("This reference scenario is prepared for exploring the workflow. Soil and weather values are example inputs, separate from a forecast loaded on the map. ISRIC SoilGrids and FAO are not called by this example.")}</p>
            <dl>
              <div>
                <dt>{t("Evidence")}</dt>
                <dd>{place ? t(place.label) : ""}{t(" · reference table")}</dd>
              </div>
              <div>
                <dt>
                  {brief.source === "model"
                    ? t("Validated model wording")
                    : t("Model wording")}
                </dt>
                <dd>
                  {brief.source === "model"
                    ? `${result.receipt.modelActions ?? 0} ${locale === "es" ? "detalles de acciones" : "action details"}`
                    : t("None — field rules")}
                </dd>
              </div>
              <div>
                <dt>{t("Payload fingerprint")}</dt>
                <dd>
                  {result.receipt.payloadHash ?? t("Shown after composition")}
                </dd>
              </div>
              <div>
                <dt>{t("Calculation owner")}</dt>
                <dd>{t("Python · soil and water bands")}</dd>
              </div>
              <div>
                <dt>{t("Weather scenario")}</dt>
                <dd>{scenario === "dry" ? t("Dry week") : t("Rain may return")}</dd>
              </div>
              <div>
                <dt>{t("Composition")}</dt>
                <dd>{t(outcomes[result.receipt.outcome])}</dd>
              </div>
              <div>
                <dt>{t("Server duration")}</dt>
                <dd>{result.receipt.latencyMs} ms</dd>
              </div>
              {result.receipt.model && (
                <div>
                  <dt>{t("Model identifier")}</dt>
                  <dd>{result.receipt.model}</dd>
                </div>
              )}
              <div>
                <dt>{t("Model attempt")}</dt>
                <dd>
                  {t(result.receipt.attempt)}
                  {result.receipt.reserved === "unknown"
                    ? ""
                    : ` · ${locale === "es" ? "reservado" : "reserved"} ${result.receipt.reserved}`}
                </dd>
              </div>
              <div>
                <dt>{t("Steps observed")}</dt>
                <dd>{result.receipt.transitions}</dd>
              </div>
              <div>
                <dt>{t("Inference path")}</dt>
                <dd>
                  {result.receipt.inference === "nebius_live"
                    ? "Nebius Token Factory"
                    : result.receipt.inference === "provider_stub"
                      ? t("Test composition")
                      : t("Field rules only")}
                </dd>
              </div>
              {result.receipt.attempt !== "none" && (
                <div>
                  <dt>{t("Generation limit")}</dt>
                  <dd>{t("350 output tokens · 3-second model deadline")}</dd>
                </div>
              )}
            </dl>
            <p>{t("Checklist marks stay in this browser tab and reset when you build another brief. They record your choices, not completed work on the land.")}</p>
          </details>
        </div>
      )}
    </section>
  );
}
