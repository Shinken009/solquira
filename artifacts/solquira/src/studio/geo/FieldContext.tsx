import { useStudioCopy } from "../StudioPreferences";
import { GEO_COPY } from "./geo-copy";
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
type LocationSource = "none" | "device" | "manual" | "map" | "reference";
type DeviceLocation = Point & { accuracyMeters: number | null; capturedAt: number };
export default function FieldContext({
  enabled,
  offline,
  csrfToken,
  onAttach,
  attachedRef,
  onPointChange,
  onEvidenceContextChange,
  externalPoint,
  activeArea,
  onNavigate,
}: {
  enabled: boolean;
  offline: boolean;
  csrfToken: string;
  onAttach: (context: AttachedContext | null) => void;
  attachedRef?: string;
  onPointChange?: (point: Point | null) => void;
  onEvidenceContextChange?: (reference: string | null) => void;
  externalPoint?: Point | null;
  activeArea: WorkspaceArea;
  onNavigate: (area: WorkspaceArea, target?: string) => void;
}) {
  const { t, locale } = useStudioCopy(GEO_COPY);
  const language = locale === "es" ? "es-CL" : "en-US";
  const number = (value: number, digits = 1) => new Intl.NumberFormat(language, {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(value);
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
    onEvidenceContextChange?.(null);
    setNasa(false);
    setImageryState("off");
    onAttach(null);
  }, [offline, enabled, csrfToken, onAttach, onEvidenceContextChange]);
  const invalidate = () => {
    controller.current?.abort();
    revision.current++;
    setData(null);
    onEvidenceContextChange?.(null);
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
    setLocationNote("Review the marker, then confirm your place.");
  };
  // Only an explicit selection from the demonstration controls supplies this
  // prop. It previews a place; it does not turn example data into observations.
  useEffect(() => {
    if (!externalPoint) return;
    prepare(externalPoint, "reference");
    setSelecting(false);
    setOpened(true);
    // An event-like object from the parent changes only on an explicit choice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalPoint]);
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
        // Metadata inventory is separate from explicit attachment to the assistant.
        onEvidenceContextChange?.(value.context_ref ?? null);
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
    setLocationNote(enabled && !offline ? "Place confirmed. Continue to the coming week." : "Place confirmed");
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
    ? "Device location" : locationSource === "reference"
      ? "Reference place" : locationSource === "map"
      ? "Map selection" : locationSource === "manual"
        ? "Entered coordinates" : "No point yet";
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
              <MapPin size={17} /> {t("Start with a place")}</span>
            <h2 id="qg-title" tabIndex={-1}>
              {t("Put your land in context.")}</h2>
            <p>
              {t("Choose a point once. Its map, forecast and field record stay with you as you move through the workspace.")}</p>
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
            {opened ? t("Close landscape view") : t("Open landscape view")}
          </button>
        </div>
        <div className="qg-grid">
          <div className="qg-location">
            <span className="qg-step">{t("01 / Choose your place")}</span>
            <h3>{t("The story starts here.")}</h3>
            <p className="qg-location-hint">
              {t("Choose on the map or use your location. Review the marker before continuing.")}</p>
            <div className="qg-place-actions">
              <button type="button" className="qg-load" data-testid="geo-select-map"
                aria-pressed={selecting} onClick={beginSelection}>
                <MapPin size={17}/>{selecting ? t("Tap the map to choose") : t("Select a place on the map")}
              </button>
              <button type="button" className="qg-locate" data-testid="geo-use-location"
                onClick={locate} disabled={locating}>
                <LocateFixed size={16}/>{locating ? t("Finding your location…") : t("Use my location")}
              </button>
            </div>
            {locationNote && <p className="qg-location-note" role="status">{t(locationNote)}</p>}
            {draft && <div className="qg-point-card" data-testid="geo-point-review" data-confirmed={Boolean(point)}>
              <span>{point ? t("Place confirmed") : t("Ready to confirm")}</span>
              <strong>{draft.latitude.toFixed(5)}, {draft.longitude.toFixed(5)}</strong>
              <small>{t(sourceLabel)}</small>
              {locationSource === "device" && deviceLocation && <small>
                {t("Reported GPS accuracy:")}{" "}{deviceLocation.accuracyMeters === null ? t("not available") : `±${Math.round(deviceLocation.accuracyMeters)} m`} · {new Date(deviceLocation.capturedAt).toLocaleTimeString(language)}
              </small>}
              <button type="button" className="qg-load" data-testid="geo-confirm-point" disabled={busy || Boolean(point && (!enabled || offline))}
                onClick={() => void confirm(draft, draftSource.current)}>
                {busy ? t("Reading the forecast…") : point ? (enabled && !offline ? t("Refresh forecast") : t("Confirm this place")) : enabled && !offline ? t("Confirm place & see forecast") : t("Confirm this place")}
              </button>
              {point && <button type="button" className="qg-next" data-testid="geo-next-guidance"
                onClick={() => onNavigate(enabled && !offline ? "anticipation" : "guidance")}>
                {enabled && !offline ? t("Next: read the coming week →") : t("Next: choose a useful check →")}</button>}
            </div>}
            <details className="qg-manual" data-testid="geo-manual">
              <summary>{t("Enter coordinates instead")}</summary>
              <form onSubmit={load}>
                <div className="qg-coordinate-inputs">
                  <label>{t("Latitude")}<input data-testid="geo-lat" inputMode="decimal" value={latitude}
                    onChange={(e) => {invalidate();setPoint(null);setDraft(null);onPointChange?.(null);setLatitude(e.target.value);}}/></label>
                  <label>{t("Longitude")}<input data-testid="geo-lon" inputMode="decimal" value={longitude}
                    onChange={(e) => {invalidate();setPoint(null);setDraft(null);onPointChange?.(null);setLongitude(e.target.value);}}/></label>
                </div>
                <button type="submit" className="qg-load" data-testid="geo-load-context" disabled={busy}>
                  {busy ? <RefreshCw size={16} className="qs-spin"/> : <Globe2 size={16}/>}
                  {busy ? t("Reading local context…") : t("Confirm coordinates & see forecast")}
                </button>
              </form>
            </details>
            {error && <p role="alert" className="qg-error">{t(error)}</p>}
            <details className="qg-method" data-testid="geo-methods">
              <summary>{t("Sources, privacy & map details")}</summary>
              <p>{t("Confirmation sends rounded coordinates to Open-Meteo for a forecast. It does not save a field observation or contact the AI assistant.")}</p>
              <p>
                {t("The Cesium view uses OpenStreetMap reference tiles (")}<a
                  href="https://www.openstreetmap.org/copyright"
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("© OpenStreetMap contributors")}</a>
                {t("), with Natural Earth as a coarse offline fallback, and an ellipsoid, not surveyed terrain. The public tile service is best-effort; a deployment can configure a contracted provider. If a reference tile is wrong,")}{" "}
                <a
                  href="https://www.openstreetmap.org/fixthemap"
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("report a map issue")}</a>
                {t(". The selected point and forecast are independent of any synthetic crop examples.")}</p>
              <p>
                {t("Loading a forecast sends rounded coordinates to Open-Meteo. The agent receives weather only after explicit attachment, without those coordinates.")}</p>
              <p>
                {t("Weather is a seven-day model forecast in UTC from")}{" "}
                <a
                  href="https://open-meteo.com/"
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("Open-Meteo")}</a>{" "}
                {t("(CC BY 4.0). Reference ET₀ describes a standard reference surface, not an irrigation prescription.")}</p>
              <p>
                {t("Optional NASA MODIS imagery provides regional visual context from a 250 m product. Its date is shown beside the layer. The Web Mercator layer covers approximately 85°S to 85°N. It is not an NDVI calculation or a field boundary.")}</p>
            </details>
          </div>
          <div className="qg-context">
            <div className="qg-map-area" id="qg-map-area">
              <div hidden={!opened}>

                <Suspense
                  fallback={
                    <div className="qg-map-loading">
                      {t("Opening the landscape tools…")}</div>
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
                    : draft ? t("Marker ready · confirm your place") : t("Choose your place on the map")}
                  {point && (
                    <small className="qg-location-source">{t(sourceLabel)}</small>
                  )}
                </span>
              </div>
              <p className="qg-map-reference" data-testid="geo-map-attribution">
                {t("Reference map ·")}{" "}<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">{t("© OpenStreetMap contributors")}</a>
              </p>
              <details className="qg-method qg-map-layers" data-testid="geo-map-layers">
                <summary>{t("Map layers")}</summary>
                {data?.imagery.state === "ready_to_load" && point ? (
                  <label className="qg-layer-toggle">
                    <input type="checkbox" data-testid="geo-nasa-toggle" aria-describedby="qg-layer-detail"
                      checked={nasa} disabled={!opened}
                      onChange={(e) => {setNasa(e.target.checked);setImageryState(e.target.checked ? "loading" : "off");}} />
                    <span><strong>{t("NASA MODIS regional imagery")}</strong><small>{t("250 m ·")}{" "}{data.imagery.date}</small></span>
                  </label>
                ) : <p>{t("Confirm your place and load its forecast to see available regional layers.")}</p>}
                <p id="qg-layer-detail" className="qg-source-note" role="status">
                  {nasa ? <>
                    {t("NASA EOSDIS GIBS / MODIS Terra /")}{" "}{data?.imagery.date} {t("· 250 m.")}{imageryState === "received"
                      ? t("Imagery received.") : imageryState === "unavailable"
                        ? t("Satellite tiles are unavailable; the reference map remains available.") : t("Loading regional imagery…")}
                  </> : t("OpenStreetMap is a reference map, not satellite imagery. Natural Earth provides a coarse fallback. Optional NASA MODIS imagery is regional; clouds, date and availability vary.")}
                </p>
              </details>
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
            <span className="qg-eyebrow">{t("Plan your next visit")}</span>
            <h2>{t("The coming week at your place.")}</h2>
            <p>
              {t("See when rain and temperature may change, then choose what to check on your next visit.")}</p>
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
                ? t("The next seven days")
                : t("Read the landscape.")}
            </h3>
            <span>
              {data
                ? data.weather.state === "cached"
                  ? t("Recent cached forecast")
                  : data.weather.state === "ready"
                    ? t("Forecast received")
                    : data.weather.state === "disabled"
                      ? t("Local map mode")
                      : t("Forecast needs retry")
                : enabled && !offline
                  ? t("Start by confirming your place")
                  : t("Three questions for your next visit")}
            </span>
          </div>
          {days.length === 7 ? (
            <>
              <div className="qg-weather-totals">
                <div>
                  <Droplets size={20} />
                  <strong>
                    {number(totalRain)}
                    <small> {t("mm")}</small>
                  </strong>
                  <span>{t("Forecast precipitation")}</span>
                </div>
                <div>
                  <Sun size={20} />
                  <strong>
                    {number(totalET)}
                    <small> {t("mm")}</small>
                  </strong>
                  <span>{t("Reference ET₀")}</span>
                </div>
              </div>
              <div className="qg-chart-legend">
                <span>
                  <i /> {t("Precipitation")}</span>
                <span>
                  <i /> {t("Reference ET₀")}</span>
                <small>{t("mm per day · UTC")}</small>
              </div>
              <div
                className="qg-chart"
                role="img"
                aria-label={
                  t("Daily forecast in UTC:") + " " +
                  days
                    .map(
                      (d) =>
                        `${d.date}: ${t("precipitation")} ${number(d.precipitation_mm)} ${t("millimetres, reference ET0")} ${number(d.reference_et0_mm)} ${t("millimetres, temperature")} ${number(d.temperature_min_c)} ${t("to")} ${number(d.temperature_max_c)} ${t("degrees Celsius")}`,
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
                    <span title={`${day.date} UTC`}>{new Intl.DateTimeFormat(language, {weekday: "short", day: "numeric", timeZone: "UTC"}).format(new Date(`${day.date}T00:00:00Z`))}</span>
                    <small>
                      {number(day.precipitation_mm)} /{" "}
                      {number(day.reference_et0_mm)}
                    </small>
                    <small>
                      {number(day.temperature_min_c, 0)}–
                      {number(day.temperature_max_c, 0)}{t("°C")}</small>
                  </div>
                ))}
              </div>
              <p className="qg-guidance">
                {t("Compare rain with atmospheric water demand, then check soil moisture and irrigation records with local agronomic support.")}</p>
              <p className="qg-source-note">
                {t("Open-Meteo · Model forecast · Retrieved")}{" "}
                {new Date(data!.retrieved_at)
                  .toISOString()
                  .slice(0, 16)
                  .replace("T", " ")}{" "}
                {t("UTC")}</p>
            </>
          ) : (
            <p className="qg-weather-empty">
              {data?.weather.note ? t(data.weather.note) :
                (enabled && !offline
                  ? t("Load the forecast to explore rain, temperature and reference water demand for this point.")
                  : t("Follow the water paths. Notice where the soil is exposed. Bring local knowledge into what you check next."))}
            </p>
          )}
          {(!enabled || offline) && (
            <div className="qg-landscape-lens">
              <div>
                <Droplets size={21} />
                <strong>{t("Water")}</strong>
                <span>{t("Where does it move?")}</span>
              </div>
              <div>
                <Sun size={21} />
                <strong>{t("Cover")}</strong>
                <span>{t("What protects the soil?")}</span>
              </div>
              <div>
                <MapPin size={21} />
                <strong>{t("People")}</strong>
                <span>{t("Who knows this place?")}</span>
              </div>
              <a href="#qs-land-record">
                {t("Bring your observations into a record")}</a>
            </div>
          )}
        </div>
        {data && <AnticipationIndicators data={data.anticipation} />}
        {data?.context_ref && days.length === 7 && !offline && (
          <div className="qg-connect" data-testid="geo-connect">
            <div>
              <strong>{t("Forecast ready. What should you check?")}</strong>
              <p>
                {t("Bring these seven days into your question about water, soil or living cover.")}</p>
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
                ? t("Forecast attached · go to question")
                : t("Use this forecast in my question")}
            </button>
          </div>
        )}

        <button
          type="button"
          className={point ? "qg-open" : "qg-load"}
          data-testid="anticipation-choose-place"
          onClick={() => onNavigate("territory", draft ? "geo-confirm-point" : "qs-landscape")}
        >
          {draft && !point ? t("Confirm your chosen place") : point ? t("Change the place") : t("Choose a place to see its forecast")}
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
