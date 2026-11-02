import { usePanelCopy } from "./panel-copy";
import { useEffect, useRef, useState } from "react";
import { ArrowRight, BookOpen, CloudSun, Layers3, Leaf, MapPin, RefreshCw, Search } from "lucide-react";
import type { Point } from "./geo/client";
import { fetchLandEvidence, type LandEvidenceInventory, type LandEvidenceItem } from "./land-evidence-client";
import "./land-evidence.css";

function Metadata({ item }: { item: LandEvidenceItem }) {
  const { t, locale, number, date } = usePanelCopy();
  return <details className="le-metadata"><summary><BookOpen size={14} aria-hidden="true"/>{t(" Source, scale & limits")}</summary><dl>
    <div><dt>{t("Source")}</dt><dd>{item.source ? <a href={item.source.url} target="_blank" rel="noreferrer">{item.source.name}</a> : t("No source included for this item")}</dd></div>
    {item.source?.version && <div><dt>{t("Version")}</dt><dd>{item.source.version}</dd></div>}
    <div><dt>{t("Method")}</dt><dd>{item.source?.method ? t(item.source.method) : t("No data-processing method applied")}</dd></div>
    <div><dt>{t("Period")}</dt><dd>{item.period ? `${item.period.start} → ${item.period.end} (${item.period.time_zone})` : t("No period supplied")}</dd></div>
    <div><dt>{t("Retrieved")}</dt><dd>{item.retrieved_at ? date(item.retrieved_at) : t("Not retrieved")}</dd></div>
    <div><dt>{t("Spatial support")}</dt><dd>{t(item.spatial_support?.description ?? "Not supplied")}{item.spatial_support?.resolution_m != null && ` · ${number(item.spatial_support.resolution_m)} m ${locale === "es" ? "de resolución de la fuente" : "source resolution"}`}</dd></div>
    {item.spatial_support?.query_precision_degrees != null && <div><dt>{t("Query rounding")}</dt><dd>{number(item.spatial_support.query_precision_degrees, 6)}{t("°; this is not the source resolution or GPS accuracy.")}</dd></div>}
    <div><dt>{t("Coverage")}</dt><dd>{item.coverage ? `${item.coverage.crs}: ${item.coverage.bbox.join(", ")}` : t("No geographic footprint asserted")}</dd></div>
    <div><dt>{t("Units")}</dt><dd>{item.units?.join(", ") || t("No measurements supplied")}</dd></div>
    <div><dt>{t("Quality")}</dt><dd>{item.quality === "metadata_checked" ? t("Source metadata checked, not independently validated for this land") : t("Not assessed")}</dd></div>
    <div><dt>{t("Uncertainty")}</dt><dd>{t(item.uncertainty)}</dd></div>
    {item.source && <div><dt>{t("Terms")}</dt><dd><a href={item.source.license_url} target="_blank" rel="noreferrer">{t("Source terms and attribution")}</a></dd></div>}
  </dl><ul>{item.limitations.map((limit, i) => <li key={i}>{t(limit)}</li>)}</ul></details>;
}
function EvidenceCard({ item }: { item: LandEvidenceItem }) {
  const { t, locale, number, date } = usePanelCopy();
  return <article className="le-card" data-testid={`evidence-slot-${item.id}`} data-state={item.state}>
    <div className="le-card-top"><CloudSun size={20} aria-hidden="true"/><span className="le-state" data-testid={`evidence-state-${item.id}`}>{t("Forecast source reviewed")}</span></div>
    <h3>{t("The coming weather, in context")}</h3><p>{t(item.reason)}</p>
    <Metadata item={item}/>
  </article>;
}
export default function LandEvidencePanel({ point, contextRef, csrfToken, enabled, onChoosePlace, onReviewForecast, onOpenRecord }: {
  point: Point | null; contextRef: string | null; csrfToken: string; enabled: boolean;
  onChoosePlace: () => void; onReviewForecast: () => void; onOpenRecord: () => void;
}) {
  const { t, locale, number, date } = usePanelCopy();
  const [snapshot, setSnapshot] = useState<{ scope: string; value: LandEvidenceInventory } | null>(null), [errorSnapshot, setErrorSnapshot] = useState<{ scope: string; message: string } | null>(null), [busyScope, setBusyScope] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const latitude = point?.latitude, longitude = point?.longitude;
  // A stale snapshot is hidden during the render that changes its scope, not
  // merely after an effect has had time to clear state. The opaque scope is
  // local only: it is never rendered, logged, persisted or sent to an assistant.
  const scope = `${latitude}:${longitude}|${contextRef}|${csrfToken}|${enabled}`;
  const activeScope = useRef(scope); activeScope.current = scope;
  const inventory = snapshot?.scope === scope ? snapshot.value : null;
  const error = errorSnapshot?.scope === scope ? errorSnapshot.message : "";
  const busy = busyScope === scope;
  useEffect(() => {
    controller.current?.abort(); controller.current = null;
    setSnapshot(null); setErrorSnapshot(null); setBusyScope(null);
    return () => { controller.current?.abort(); controller.current = null; };
  }, [latitude, longitude, contextRef, csrfToken, enabled]);
  const review = async () => {
    if (!point || !enabled || !csrfToken || busy) return;
    controller.current?.abort();
    const request = new AbortController(); controller.current = request;
    setBusyScope(scope); setErrorSnapshot(null); setSnapshot(null);
    try {
      const value = await fetchLandEvidence(point, contextRef, csrfToken, request.signal);
      if (!request.signal.aborted && controller.current === request && activeScope.current === scope) setSnapshot({ scope, value });
    } catch (caught) {
      if (!request.signal.aborted && controller.current === request && activeScope.current === scope) setErrorSnapshot({ scope, message: caught instanceof Error ? caught.message : "The inventory could not be loaded." });
    } finally {
      if (!request.signal.aborted && controller.current === request && activeScope.current === scope) setBusyScope(null);
    }
  };
  // The internal inventory remains complete, but the product exposes only an
  // operational source. Missing adapters are not advertised as future features.
  const forecast = inventory?.items.find(item => item.id === "forecast_context");
  const available = forecast?.state === "available" ? forecast : null;
  return <section id="qs-land-evidence" className="le-panel" aria-labelledby="le-heading" data-testid="land-evidence-panel">
    <header className="le-heading"><div><span className="qs-index"><Layers3 size={15} aria-hidden="true"/>{t(" Understand your sources")}</span><h2 id="le-heading">{t("From the forecast to your next visit.")}</h2><p>{t("Review the source behind the weather context, then bring one clear observation to your next visit.")}</p></div><span className="le-inventory-tag">{t("Forecast source")}</span></header>
    {!point ? <div className="le-empty" data-testid="land-evidence-empty"><MapPin size={21} aria-hidden="true"/><div><strong>{t("Start with a confirmed place.")}</strong><p>{t("Confirm your place to connect the forecast and your next observation.")}</p><button type="button" className="le-text-button" onClick={onChoosePlace}>{t("Choose a place ")}<ArrowRight size={15} aria-hidden="true"/></button></div></div>
      : <p className="le-place">{t("For the confirmed point ")}<strong>{point.latitude.toFixed(5)}, {point.longitude.toFixed(5)}</strong></p>}
    <div className="le-load-row"><button type="button" className="le-load" data-testid="land-evidence-load" onClick={() => void review()} disabled={!point || !enabled || !csrfToken || busy}>
      {busy ? <RefreshCw size={17} className="qs-spin" aria-hidden="true"/> : <Search size={17} aria-hidden="true"/>}{busy ? t("Reviewing source context…") : inventory ? t("Review source context again") : t("Review forecast evidence")}
    </button><p>{t("Review the forecast already loaded for this place.")}</p></div>
    {point && !enabled && <p className="le-service-note">{t("Reconnect to the workspace to review the source context. Existing local exploration remains available.")}</p>}
    <div className="le-status" role="status" aria-live="polite" aria-atomic="true">{busy ? t("Reviewing source metadata for the confirmed place.") : inventory ? available ? t("Forecast source reviewed. Choose one observation for your next visit.") : t("No usable forecast was found for this review. No land condition has been inferred.") : ""}</div>
    {error && <div className="le-error" role="alert" data-testid="land-evidence-error"><p>{t(error)}</p><button type="button" className="le-text-button" data-testid="land-evidence-review-forecast" onClick={onReviewForecast}>{t("Review forecast ")}<ArrowRight size={15} aria-hidden="true"/></button></div>}
    {inventory && available && <div className="le-result" data-testid="land-evidence-result">
      <p className="le-snapshot">{t("Reviewed ")}{date(inventory.generated_at)}.</p>
      <div className="le-context"><EvidenceCard item={available}/></div>
      <p className="le-boundary">{t("Forecast context, not a soil diagnosis or irrigation prescription.")}</p>
      <details className="le-inventory-method"><summary>{t("Sources, method & privacy")}</summary><p>{t("Your selected point goes only to this service to review existing metadata. This does not acquire new environmental data, read field notes or contact historical sources or AI.")}</p><p>{t("Review again when the forecast for this place changes.")}</p><p>{t("Method: ")}{inventory.schema_version}{t(". Scientific validation: not validated.")}</p><ul>{inventory.limitations.map((limit, i) => <li key={i}>{t(limit)}</li>)}</ul></details>
    </div>}
    {inventory && !available && <div className="le-no-context" data-testid="land-evidence-no-context" data-forecast-state={forecast?.state ?? "missing"}>
      <CloudSun size={22} aria-hidden="true"/><div><strong>{t("There is no usable forecast to interpret here.")}</strong><p>{t(forecast?.reason ?? "The source context could not be established.")}</p><p>{t("No conclusion about the condition of this land has been calculated.")}</p><button type="button" className="le-text-button" data-testid="land-evidence-review-forecast" onClick={onReviewForecast}>{t("Review forecast ")}<ArrowRight size={15} aria-hidden="true"/></button></div>
    </div>}
    {inventory && <div className="le-observation" data-testid="land-evidence-observation"><Leaf size={20} aria-hidden="true"/><div><span>{t("One useful check for your next visit")}</span><p>{t("At a safe, accessible place, note the date and describe the visible cover. Repeat the same observation before interpreting change.")}</p><button type="button" className="le-text-button" data-testid="land-evidence-open-record" onClick={onOpenRecord}>{t("Keep a dated observation ")}<ArrowRight size={15} aria-hidden="true"/></button></div></div>}
  </section>;
}
