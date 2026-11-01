import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Droplets,
  Globe2,
  Layers3,
  LocateFixed,
  MapPin,
  RefreshCw,
  Sun,
} from "lucide-react";
import {
  fetchFieldContext,
  roundedPoint,
  samePoint,
  type AttachedContext,
  type FieldContextData,
  type Point,
} from "./client";
import "./field-context.css";
import type { WorkspaceArea } from "../Studio";
import AnticipationIndicators from "./AnticipationIndicators";
import HistoricalContext from "./HistoricalContext";
const Landscape = lazy(() => import("./CesiumFieldMap"));
type LocationSource = "none" | "device" | "manual" | "map";
type DeviceLocation = Point & { accuracyMeters: number | null; capturedAt: number };
export default function FieldContext({
  enabled,
  offline,
  csrfToken,
  onAttach,
  attachedRef,
  onPointChange,
  activeArea,
  onNavigate,
}: {
  enabled: boolean;
  offline: boolean;
  csrfToken: string;
  onAttach: (context: AttachedContext | null) => void;
  attachedRef?: string;
  onPointChange?: (point: Point | null) => void;
  externalPoint?: Point | null;
  activeArea: WorkspaceArea;
  onNavigate: (area: WorkspaceArea, target?: string) => void;
}) {
  const [latitude, setLatitude] = useState(""),
    [longitude, setLongitude] = useState("");
  const [point, setPoint] = useState<Point | null>(null);
  const [draft, setDraft] = useState<Point | null>(null);
  const [deviceLocation, setDeviceLocation] = useState<DeviceLocation | null>(null);
  const [selecting, setSelecting] = useState(false);
  const draftSource = useRef<LocationSource>("none");
  const [opened, setOpened] = useState(true),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [locationNote, setLocationNote] = useState(""),
    [locating, setLocating] = useState(false);
  const [data, setData] = useState<FieldContextData | null>(null),
    [nasa, setNasa] = useState(false),
    [imageryState, setImageryState] = useState("off"),
    [locationSource, setLocationSource] = useState<LocationSource>("none");
  const controller = useRef<AbortController | null>(null),
    revision = useRef(0),
    mounted = useRef(true),
    sectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    controller.current?.abort();
    revision.current++;
    setBusy(false);
    setData(null);
    setNasa(false);
    setImageryState("off");
    onAttach(null);
  }, [offline, enabled, csrfToken, onAttach]);
  const invalidate = () => {
    controller.current?.abort();
    revision.current++;
    setData(null);
    setNasa(false);
    setBusy(false);
    setError("");
    setImageryState("off");
    onAttach(null);
  };
  // A draft is not an analysis point. GPS and map coordinates retain their
  // original precision; only a copy sent to the weather service is rounded.
  const prepare = (value: Point, source: LocationSource) => {
    if (!Number.isFinite(value.latitude) || !Number.isFinite(value.longitude)
      || Math.abs(value.latitude) > 90 || Math.abs(value.longitude) > 180) return;
    invalidate();
    setPoint(null);
    onPointChange?.(null);
    setDraft(value);
    draftSource.current = source;
    setLocationSource(source);
    setLatitude(String(value.latitude));
    setLongitude(String(value.longitude));
    setLocationNote("Review the marker, then confirm this place. No weather request has been sent.");
  };
  const beginSelection = () => {
    invalidate();
    setPoint(null);
    onPointChange?.(null);
    setSelecting(true);
    setOpened(true);
    setLocationNote("Tap or click the map to mark your place, then confirm. You can also enter coordinates below.");
  };
  const current = (): Point | null => {
    if (!latitude.trim() || !longitude.trim()) return null;
    const p = { latitude: Number(latitude), longitude: Number(longitude) };
    return Number.isFinite(p.latitude) &&
      Number.isFinite(p.longitude) &&
      Math.abs(p.latitude) <= 90 &&
      Math.abs(p.longitude) <= 180
      ? p
      : null;
  };
  const requestForecast = async (selected: Point) => {
    if (!enabled || offline || !csrfToken) return;
    // Forecasts only follow a confirmation or an explicit refresh, never
    // navigation, a draft edit, GPS completion or a rejected-response loop.
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    const version = ++revision.current;
    setBusy(true);
    try {
      const value = await fetchFieldContext(
        selected,
        csrfToken,
        request.signal,
      );
      if (!request.signal.aborted && version === revision.current) {
        if (!samePoint(value.location, selected))
          throw new Error("The returned point is different.");
        setData(value);
      }
    } catch (caught) {
      if (!request.signal.aborted && version === revision.current)
        setError(
          caught instanceof Error
            ? caught.message
            : "The forecast could not be loaded.",
        );
    } finally {
      if (!request.signal.aborted && version === revision.current)
        setBusy(false);
    }
  };
  const confirm = async (selected: Point, source: LocationSource) => {
    invalidate();
    setPoint(selected);
    setDraft(selected);
    setSelecting(false);
    setLocationSource(source);
    onPointChange?.(selected);
    setLatitude(String(selected.latitude));
    setLongitude(String(selected.longitude));
    setLocationNote("Place confirmed. Your environmental context belongs to this point; field observations remain separate.");
    await requestForecast(roundedPoint(selected));
  };
  const load = async (event: FormEvent) => {
    event.preventDefault();
    const selected = current();
    if (!selected) {
      setError("Enter a latitude from −90 to 90 and longitude from −180 to 180.");
      return;
    }
    await confirm(selected, "manual");
  };
  const locate = () => {
    if (deviceLocation) {
      prepare({latitude: deviceLocation.latitude, longitude: deviceLocation.longitude}, "device");
      setSelecting(false);
      return;
    }
    if (!navigator.geolocation) {
      setError("Location is unavailable. Mark the place on the map or enter coordinates instead.");
      return;
    }
    setLocating(true);
    setError("");
    const version = revision.current;
    navigator.geolocation.getCurrentPosition(
      (value) => {
        if (!mounted.current) return;
        const device: DeviceLocation = {
          latitude: value.coords.latitude,
          longitude: value.coords.longitude,
          accuracyMeters: Number.isFinite(value.coords.accuracy) && value.coords.accuracy >= 0 ? value.coords.accuracy : null,
          capturedAt: value.timestamp,
        };
        setDeviceLocation(device);
        setLocating(false);
        // A late GPS reply is retained as device context, never as an overwrite
        // of a more recent map/manual choice.
        if (version === revision.current) {
          prepare({latitude: device.latitude, longitude: device.longitude}, "device");
          setSelecting(false);
        }
      },
      () => {
        if (!mounted.current) return;
        setLocating(false);
        if (version === revision.current)
          setError("Location was not shared. You can still mark the place on the map or enter coordinates.");
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 },
    );
  };
  const days = data?.weather.days ?? [],
    totalRain = days.reduce((sum, d) => sum + d.precipitation_mm, 0),
    totalET = days.reduce((sum, d) => sum + d.reference_et0_mm, 0);
  const chartMax = Math.max(
    1,
    ...days.flatMap((d) => [d.precipitation_mm, d.reference_et0_mm]),
  );
  const sourceLabel = locationSource === "device"
    ? "Device GPS" : locationSource === "map"
      ? "Chosen on map · GPS accuracy not measured" : locationSource === "manual"
        ? "Entered coordinates · GPS accuracy not measured" : "No point yet";
  return (
    <>
      <section
        hidden={activeArea !== "territory"}
        role="tabpanel"
        aria-labelledby="qs-area-tab-territory"
        ref={sectionRef}
        className="qg-section"
        id="qs-landscape"
        tabIndex={-1}
        data-testid="geo-section"
      >
        <div className="qg-heading">
          <div>
            <span className="qg-eyebrow">
              <MapPin size={17} /> Start with a place
            </span>
            <h2 id="qg-title" tabIndex={-1}>
              Put your land in context.
            </h2>
            <p>
              Choose a point once. Its map, forecast and field record stay with
              you as you move through the workspace.
            </p>
          </div>
          <button
            type="button"
            className="qg-open"
            data-testid="geo-open-map"
            aria-expanded={opened}
            aria-controls="qg-map-area"
            onClick={() => setOpened(!opened)}
          >
            <Layers3 size={17} />
            {opened ? "Close landscape view" : "Open landscape view"}
          </button>
        </div>
        <div className="qg-grid">
          <div className="qg-location">
            <span className="qg-step">01 / Choose your place</span>
            <h3>The story starts here.</h3>
            <p className="qg-location-hint">
              Mark the land you want to understand. You do not need an address,
              a parcel boundary or a GPS permission to explore.
            </p>
            <div className="qg-place-actions">
              <button type="button" className="qg-load" data-testid="geo-select-map"
                aria-pressed={selecting} onClick={beginSelection}>
                <MapPin size={17}/>{selecting ? "Tap the map to choose" : "Select a place on the map"}
              </button>
              <button type="button" className="qg-locate" data-testid="geo-use-location"
                onClick={locate} disabled={locating}>
                <LocateFixed size={16}/>{locating ? "Finding your location…" : "Use my location"}
              </button>
            </div>
            {locationNote && <p className="qg-location-note" role="status">{locationNote}</p>}
            {draft && <div className="qg-point-card" data-testid="geo-point-review" data-confirmed={Boolean(point)}>
              <span>{point ? "Analysis point confirmed" : "Review your chosen place"}</span>
              <strong>{draft.latitude.toFixed(5)}, {draft.longitude.toFixed(5)}</strong>
              <small>{sourceLabel}</small>
              {locationSource === "device" && deviceLocation && <small>
                Reported GPS accuracy: {deviceLocation.accuracyMeters === null ? "not available" : `±${Math.round(deviceLocation.accuracyMeters)} m`} · {new Date(deviceLocation.capturedAt).toLocaleTimeString()}
              </small>}
              <button type="button" className="qg-load" data-testid="geo-confirm-point" disabled={busy}
                onClick={() => void confirm(draft, draftSource.current)}>
                {busy ? "Reading the forecast…" : point ? "Refresh this point’s context" : "Confirm this point"}
              </button>
              {point && <button type="button" className="qg-next" data-testid="geo-next-guidance"
                onClick={() => onNavigate("guidance", "qs-exploration")}>
                Next: tell us what concerns you →
              </button>}
            </div>}
            <details className="qg-manual" data-testid="geo-manual">
              <summary>Enter coordinates instead</summary>
              <form onSubmit={load}>
                <div className="qg-coordinate-inputs">
                  <label>Latitude<input data-testid="geo-lat" inputMode="decimal" value={latitude}
                    onChange={(e) => {invalidate();setPoint(null);setDraft(null);onPointChange?.(null);setLatitude(e.target.value);}}/></label>
                  <label>Longitude<input data-testid="geo-lon" inputMode="decimal" value={longitude}
                    onChange={(e) => {invalidate();setPoint(null);setDraft(null);onPointChange?.(null);setLongitude(e.target.value);}}/></label>
                </div>
                <button type="submit" className="qg-load" data-testid="geo-load-context" disabled={busy}>
                  {busy ? <RefreshCw size={16} className="qs-spin"/> : <Globe2 size={16}/>}
                  {busy ? "Reading local context…" : "Confirm coordinates & load context"}
                </button>
              </form>
            </details>
            <p className="qg-sharing">Confirmation requests a forecast for rounded coordinates.
              It does not save a field observation or contact the AI assistant.</p>
            {error && <p role="alert" className="qg-error">{error}</p>}
            <details className="qg-method" data-testid="geo-methods">
              <summary>Sources & map details</summary>
              <p>
                The Cesium view uses OpenStreetMap reference tiles (
                <a
                  href="https://www.openstreetmap.org/copyright"
                  target="_blank"
                  rel="noreferrer"
                >
                  © OpenStreetMap contributors
                </a>
                ), with Natural Earth as a coarse offline fallback, and an
                ellipsoid, not surveyed terrain. The public tile service is
                best-effort; a deployment can configure a contracted provider.
                If a reference tile is wrong,{" "}
                <a
                  href="https://www.openstreetmap.org/fixthemap"
                  target="_blank"
                  rel="noreferrer"
                >
                  report a map issue
                </a>
                . The selected point and forecast are independent of any
                synthetic crop examples.
              </p>
              <p>
                Loading a forecast sends rounded coordinates to Open-Meteo. The
                agent receives weather only after explicit attachment, without
                those coordinates.
              </p>
              <p>
                Weather is a seven-day model forecast in UTC from{" "}
                <a
                  href="https://open-meteo.com/"
                  target="_blank"
                  rel="noreferrer"
                >
                  Open-Meteo
                </a>{" "}
                (CC BY 4.0). Reference ET₀ describes a standard reference
                surface, not an irrigation prescription.
              </p>
              <p>
                Optional NASA MODIS imagery provides regional visual context
                from a 250 m product. Its date is shown beside the layer. The
                Web Mercator layer covers approximately 85°S to 85°N. It is not
                an NDVI calculation or a field boundary.
              </p>
            </details>
          </div>
          <div className="qg-context">
            <div className="qg-map-area" id="qg-map-area">
              <div hidden={!opened}>

                <Suspense
                  fallback={
                    <div className="qg-map-loading">
                      Opening the landscape tools…
                    </div>
                  }
                >
                  <Landscape
                    point={draft ?? point}
                    selecting={selecting}
                    onPick={(next) => prepare(next, "map")}
                    imagery={data?.imagery ?? null}
                    nasaEnabled={nasa}
                    onImageryState={setImageryState}
                  />
                </Suspense>

              </div>
              <div className="qg-layer-line">
                <span
                  className="qg-coordinates"
                  data-testid="geo-selected-point"
                  data-source={locationSource}
                >
                  <MapPin size={14} />
                  {point
                    ? `${point.latitude.toFixed(3)}, ${point.longitude.toFixed(3)}`
                    : "No point confirmed"}
                  {point && (
                    <small className="qg-location-source">{sourceLabel}</small>
                  )}
                </span>
                <label className="qg-layer-toggle">
                  <input
                    type="checkbox"
                    data-testid="geo-nasa-toggle"
                    aria-describedby="qg-layer-detail"
                    checked={nasa}
                    disabled={
                      !opened ||
                      !point ||
                      data?.imagery.state !== "ready_to_load"
                    }
                    onChange={(e) => {
                      setNasa(e.target.checked);
                      setImageryState(e.target.checked ? "loading" : "off");
                    }}
                  />
                  <span>
                    <strong>NASA MODIS detail</strong>
                    <small>
                      {!point
                        ? "Choose a point to enable"
                        : data?.imagery.state === "ready_to_load"
                          ? `250 m regional / ${data.imagery.date}`
                          : "Load local context to enable"}
                    </small>
                  </span>
                </label>
              </div>
              <p
                id="qg-layer-detail"
                className={nasa ? "qg-source-note" : "qg-layer-hint"}
                role="status"
              >
                {!point ? (
                  <>
                    Choose a point on the map or use your location, then confirm it. The map shows OpenStreetMap reference
                    tiles (not satellite imagery) with a coarse Natural Earth
                    fallback.
                  </>
                ) : nasa ? (
                  <>
                    NASA EOSDIS GIBS / MODIS Terra /{" "}
                    {data?.imagery.date ?? "date pending"} / regional 250 m;
                    clouds and availability vary.{" "}
                    {imageryState === "received"
                      ? "Imagery received."
                      : imageryState === "unavailable"
                        ? "Satellite tiles are unavailable; the reference map remains available."
                        : "Loading regional imagery…"}
                  </>
                ) : (
                  <>
                    OpenStreetMap provides a detailed reference map (not
                    satellite imagery).{" "}
                    <a
                      href="https://www.openstreetmap.org/copyright"
                      target="_blank"
                      rel="noreferrer"
                    >
                      © OpenStreetMap contributors
                    </a>
                    . NASA MODIS offers optional 250 m regional imagery once
                    local context loads; clouds, date and availability vary.
                  </>
                )}
              </p>
            </div>
          </div>
        </div>
      </section>
      <section
        className="qg-section qg-anticipation"
        id="qs-anticipation"
        role="tabpanel"
        aria-labelledby="qs-area-tab-anticipation"
        tabIndex={0}
        hidden={activeArea !== "anticipation"}
        data-testid="area-anticipation"
      >
        <div className="qg-heading">
          <div>
            <span className="qg-eyebrow">Anticipación ambiental</span>
            <h2>The coming week, with its limits.</h2>
            <p>
              Read forecast indicators before choosing checks for your next
              visit. No model call is needed.
            </p>
          </div>
        </div>
        <div
          className="qg-weather"
          aria-busy={busy}
          data-testid="geo-weather"
          data-state={data?.weather.state ?? "idle"}
        >
          <div className="qg-weather-heading">
            <h3 tabIndex={-1}>
              {enabled && !offline
                ? "The next seven days"
                : "Read the landscape."}
            </h3>
            <span>
              {data
                ? data.weather.state === "cached"
                  ? "Recent cached forecast"
                  : data.weather.state === "ready"
                    ? "Forecast received"
                    : data.weather.state === "disabled"
                      ? "Local map mode"
                      : "Forecast needs retry"
                : enabled && !offline
                  ? "Choose a point, then load context"
                  : "Three questions for your next visit"}
            </span>
          </div>
          {days.length === 7 ? (
            <>
              <div className="qg-weather-totals">
                <div>
                  <Droplets size={20} />
                  <strong>
                    {totalRain.toFixed(1)}
                    <small> mm</small>
                  </strong>
                  <span>Forecast precipitation</span>
                </div>
                <div>
                  <Sun size={20} />
                  <strong>
                    {totalET.toFixed(1)}
                    <small> mm</small>
                  </strong>
                  <span>Reference ET₀</span>
                </div>
              </div>
              <div className="qg-chart-legend">
                <span>
                  <i /> Precipitation
                </span>
                <span>
                  <i /> Reference ET₀
                </span>
                <small>mm per day · UTC</small>
              </div>
              <div
                className="qg-chart"
                role="img"
                aria-label={
                  "Daily forecast in UTC: " +
                  days
                    .map(
                      (d) =>
                        `${d.date}: precipitation ${d.precipitation_mm} millimetres, reference ET0 ${d.reference_et0_mm} millimetres, temperature ${d.temperature_min_c} to ${d.temperature_max_c} degrees Celsius`,
                    )
                    .join("; ")
                }
              >
                {days.map((day) => (
                  <div key={day.date} className="qg-chart-day">
                    <div className="qg-bars">
                      <i
                        style={{
                          height: `${(day.precipitation_mm / chartMax) * 100}%`,
                        }}
                      />
                      <b
                        style={{
                          height: `${(day.reference_et0_mm / chartMax) * 100}%`,
                        }}
                      />
                    </div>
                    <span>{day.date.slice(5)}</span>
                    <small>
                      {day.precipitation_mm.toFixed(1)} /{" "}
                      {day.reference_et0_mm.toFixed(1)}
                    </small>
                    <small>
                      {day.temperature_min_c.toFixed(0)}–
                      {day.temperature_max_c.toFixed(0)}°C
                    </small>
                  </div>
                ))}
              </div>
              <p className="qg-guidance">
                Compare rain with atmospheric water demand, then check soil
                moisture and irrigation records with local agronomic support.
              </p>
              <p className="qg-source-note">
                Open-Meteo · Model forecast · Retrieved{" "}
                {new Date(data!.retrieved_at)
                  .toISOString()
                  .slice(0, 16)
                  .replace("T", " ")}{" "}
                UTC
              </p>
            </>
          ) : (
            <p className="qg-weather-empty">
              {data?.weather.note ??
                (enabled && !offline
                  ? "Load the forecast to explore rain, temperature and reference water demand for this point."
                  : "Follow the water paths. Notice where the soil is exposed. Bring local knowledge into what you check next.")}
            </p>
          )}
          {(!enabled || offline) && (
            <div className="qg-landscape-lens">
              <div>
                <Droplets size={21} />
                <strong>Water</strong>
                <span>Where does it move?</span>
              </div>
              <div>
                <Sun size={21} />
                <strong>Cover</strong>
                <span>What protects the soil?</span>
              </div>
              <div>
                <MapPin size={21} />
                <strong>People</strong>
                <span>Who knows this place?</span>
              </div>
              <a href="#qs-land-record">
                Bring your observations into a record
              </a>
            </div>
          )}
        </div>
        <AnticipationIndicators data={data?.anticipation} />
        {data?.context_ref && days.length === 7 && !offline && (
          <div className="qg-connect" data-testid="geo-connect">
            <div>
              <strong>Forecast ready. What should you check?</strong>
              <p>
                Bring these seven days into your question about water, soil or
                living cover.
              </p>
            </div>
            <button
              type="button"
              className="qg-load"
              data-testid="geo-attach"
              onClick={() => {
                onAttach({
                  reference: data.context_ref!,
                  location: data.location,
                  retrievedAt: data.retrieved_at,
                });
                onNavigate("guidance", "qs-query");
              }}
            >
              {attachedRef === data.context_ref
                ? "Forecast attached · go to question"
                : "Use this forecast in my question"}
            </button>
          </div>
        )}

        <button
          type="button"
          className="qg-open"
          onClick={() => onNavigate("territory")}
        >
          Choose or change the place
        </button>
        <HistoricalContext
          point={point}
          enabled={enabled && !offline}
          csrf={csrfToken}
        />
      </section>
    </>
  );
}
