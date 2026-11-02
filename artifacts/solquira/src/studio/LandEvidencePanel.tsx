import { useEffect, useRef, useState } from "react";
import { ArrowRight, BookOpen, CloudSun, Layers3, Leaf, MapPin, RefreshCw, Search, Sprout } from "lucide-react";
import type { Point } from "./geo/client";
import { fetchLandEvidence, type EvidenceId, type EvidenceState, type LandEvidenceInventory, type LandEvidenceItem } from "./land-evidence-client";
import "./land-evidence.css";

const labels: Record<EvidenceId, string> = {
  forecast_context: "The coming weather", climate_baseline: "The long-term climate", land_cover_change: "How the land cover has changed",
  land_productivity: "How vegetation productivity has changed", soil_organic_carbon: "What is known about soil carbon", terrain: "The shape of the land", field_observations: "What has been observed on site",
};
const stateLabels: Record<EvidenceState, string> = { available: "Source metadata available", missing: "Not included", unavailable: "Source unavailable", stale: "Needs a fresh check", not_applicable: "Not applicable" };
const priority: EvidenceId[] = ["forecast_context", "land_cover_change", "soil_organic_carbon"];
function Metadata({ item }: { item: LandEvidenceItem }) {
  return <details className="le-metadata"><summary><BookOpen size={14} aria-hidden="true"/> Source, scale & limits</summary><dl>
    <div><dt>Source</dt><dd>{item.source ? <a href={item.source.url} target="_blank" rel="noreferrer">{item.source.name}</a> : "No source included for this item"}</dd></div>
    <div><dt>Version</dt><dd>{item.source?.version ?? "Not supplied"}</dd></div>
    <div><dt>Method</dt><dd>{item.source?.method ?? "No data-processing method applied"}</dd></div>
    <div><dt>Period</dt><dd>{item.period ? `${item.period.start} → ${item.period.end} (${item.period.time_zone})` : "No period supplied"}</dd></div>
    <div><dt>Retrieved</dt><dd>{item.retrieved_at ? new Date(item.retrieved_at).toLocaleString() : "Not retrieved"}</dd></div>
    <div><dt>Spatial support</dt><dd>{item.spatial_support?.description ?? "Not supplied"}{item.spatial_support?.resolution_m != null && ` · ${item.spatial_support.resolution_m} m source resolution`}</dd></div>
    {item.spatial_support?.query_precision_degrees != null && <div><dt>Query rounding</dt><dd>{item.spatial_support.query_precision_degrees}°; this is not the source resolution or GPS accuracy.</dd></div>}
    <div><dt>Coverage</dt><dd>{item.coverage ? `${item.coverage.crs}: ${item.coverage.bbox.join(", ")}` : "No geographic footprint asserted"}</dd></div>
    <div><dt>Units</dt><dd>{item.units?.join(", ") || "No measurements supplied"}</dd></div>
    <div><dt>Quality</dt><dd>{item.quality === "metadata_checked" ? "Source metadata checked, not independently validated for this land" : "Not assessed"}</dd></div>
    <div><dt>Uncertainty</dt><dd>{item.uncertainty}</dd></div>
    {item.source && <div><dt>Terms</dt><dd><a href={item.source.license_url} target="_blank" rel="noreferrer">Source terms and attribution</a></dd></div>}
  </dl><ul>{item.limitations.map((limit, i) => <li key={i}>{limit}</li>)}</ul></details>;
}
function EvidenceCard({ item }: { item: LandEvidenceItem }) {
  const Icon = item.id === "forecast_context" ? CloudSun : item.id === "soil_organic_carbon" ? Sprout : Leaf;
  return <article className="le-card" data-testid={`evidence-slot-${item.id}`} data-state={item.state}>
    <div className="le-card-top"><Icon size={20} aria-hidden="true"/><span className="le-state" data-testid={`evidence-state-${item.id}`}>{stateLabels[item.state]}</span></div>
    <h3>{labels[item.id]}</h3><p>{item.reason}</p>
    <div className="le-next"><span>A useful next observation</span><p>{item.next_observation}</p></div>
    <Metadata item={item}/>
  </article>;
}
export default function LandEvidencePanel({ point, contextRef, csrfToken, enabled, onChoosePlace }: {
  point: Point | null; contextRef: string | null; csrfToken: string; enabled: boolean; onChoosePlace: () => void;
}) {
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
  const available = inventory?.items.filter(item => item.state === "available") ?? [];
  const rest = inventory?.items.filter(item => !priority.includes(item.id)) ?? [];
  return <section id="qs-land-evidence" className="le-panel" aria-labelledby="le-heading" data-testid="land-evidence-panel">
    <header className="le-heading"><div><span className="qs-index"><Layers3 size={15} aria-hidden="true"/> Before drawing conclusions</span><h2 id="le-heading">What do we know about this place?</h2><p>See what supports the conversation, what is still missing, and what could be useful to observe next.</p></div><span className="le-inventory-tag">Evidence inventory · no land score</span></header>
    {!point ? <div className="le-empty" data-testid="land-evidence-empty"><MapPin size={21} aria-hidden="true"/><div><strong>Start with a confirmed place.</strong><p>There is no land condition to infer from an unselected map.</p><button type="button" className="le-text-button" onClick={onChoosePlace}>Choose a place <ArrowRight size={15} aria-hidden="true"/></button></div></div>
      : <p className="le-place">For the confirmed point <strong>{point.latitude.toFixed(5)}, {point.longitude.toFixed(5)}</strong></p>}
    <div className="le-load-row"><button type="button" className="le-load" data-testid="land-evidence-load" onClick={() => void review()} disabled={!point || !enabled || !csrfToken || busy}>
      {busy ? <RefreshCw size={17} className="qs-spin" aria-hidden="true"/> : <Search size={17} aria-hidden="true"/>}{busy ? "Reviewing the inventory…" : inventory ? "Refresh this inventory" : "Review what we know"}
    </button><p>Your selected point goes only to this service to review existing metadata. This does not acquire new environmental data, read field notes or contact historical sources or AI.</p></div>
    {point && !enabled && <p className="le-service-note">Reconnect to the workspace to review this inventory. Existing local exploration remains available.</p>}
    <div className="le-status" role="status" aria-live="polite" aria-atomic="true">{busy ? "Reviewing source metadata for the confirmed place." : inventory ? "Evidence inventory ready. Available metadata are not a diagnosis or a validation of this land." : ""}</div>
    {error && <p className="le-error" role="alert" data-testid="land-evidence-error">{error}</p>}
    {inventory && <div className="le-result" data-testid="land-evidence-result">
      <p className="le-snapshot">Snapshot reviewed {new Date(inventory.generated_at).toLocaleString()}. This is not a live assessment; review again after the point’s source context changes.</p>
      <div className="le-summary"><div><span>What is known</span><p>{available.length ? "Forecast source metadata are included for the confirmed point." : "No environmental evidence is included in this inventory yet."}</p></div><div><span>What is missing</span><p>Comparable changes in land cover, vegetation productivity and soil carbon are not established here. Missing evidence is not proof that degradation is absent.</p></div></div>
      <div className="le-priority">{priority.map(id => inventory.items.find(item => item.id === id)).filter((item): item is LandEvidenceItem => Boolean(item)).map(item => <EvidenceCard key={item.id} item={item}/>)}</div>
      <details className="le-other"><summary>Other evidence gaps <span>{rest.length} items</span></summary><div className="le-other-grid">{rest.map(item => <EvidenceCard key={item.id} item={item}/>)}</div></details>
      <p className="le-boundary"><strong>A starting point, not a desertification assessment.</strong> Metadata availability does not validate a source for a parcel. This inventory calculates no MEDALUS-R index, land-degradation indicator, restoration outcome or irrigation recommendation.</p>
      <details className="le-inventory-method"><summary>Inventory method & limitations</summary><p>Inventory generated {new Date(inventory.generated_at).toLocaleString()}. Method: {inventory.schema_version}. Scientific validation: not validated.</p><ul>{inventory.limitations.map((limit, i) => <li key={i}>{limit}</li>)}</ul></details>
    </div>}
  </section>;
}
