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
import { REFERENCE_PLACES } from "../dryland-client";
import "./field-context.css";
const Landscape = lazy(() => import("./CesiumFieldMap"));
type LocationSource = "none" | "geolocation" | "reference" | "manual" | "map";
const examples = REFERENCE_PLACES.map((item) => ({
  label: item.label,
  latitude: item.latitude,
  longitude: item.longitude,
}));
export default function FieldContext({
  enabled,
  offline,
  csrfToken,
  onAttach,
  attachedRef,
  onPointChange,
  externalPoint,
}: {
  enabled: boolean;
  offline: boolean;
  csrfToken: string;
  onAttach: (context: AttachedContext | null) => void;
  attachedRef?: string;
  onPointChange?: (point: Point | null) => void;
  externalPoint?: Point | null;
}) {
  const [latitude, setLatitude] = useState(""),
    [longitude, setLongitude] = useState("");
  const [point, setPoint] = useState<Point | null>(null);
  const [opened, setOpened] = useState(false),
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
    sectionRef = useRef<HTMLElement>(null),
    autoOpened = useRef(false),
    autoLoadedFor = useRef(""),
    locationAsked = useRef(false),
    manualSelection = useRef(false);
  useEffect(() => {
    const node = sectionRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setOpened(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        observer.disconnect();
        if (!autoOpened.current) {
          autoOpened.current = true;
          setOpened(true);
        }
      },
      { threshold: 0.3, rootMargin: "0px 0px -8% 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, offline, csrfToken]);
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
    autoLoadedFor.current = "";
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
  const select = (value: Point, source: LocationSource = "manual") => {
    const next = roundedPoint(value);
    invalidate();
    if (source !== "geolocation") manualSelection.current = true;
    setPoint(next);
    setLocationSource(source);
    setLocationNote("");
    onPointChange?.(next);
    setLatitude(String(next.latitude));
    setLongitude(String(next.longitude));
  };
  // The brief can choose one of the four prepared profiles; the map follows that choice.
  useEffect(() => {
    if (!externalPoint) return;
    if (point && samePoint(point, externalPoint)) return;
    select(externalPoint, "reference");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalPoint?.latitude, externalPoint?.longitude]);
  // The map starts at the person's approximate location, never at a fixed
  // reference region. The browser owns the permission prompt; a denial leaves
  // the map in a neutral world view and the reference landscapes remain optional.
  useEffect(() => {
    if (!opened || offline || locationAsked.current) return;
    locationAsked.current = true;
    const geolocation = navigator.geolocation;
    if (!geolocation) {
      setLocationNote(
        "This browser cannot share a location. Choose a point on the map, a reference landscape, or enter coordinates.",
      );
      return;
    }
    let cancelled = false;
    geolocation.getCurrentPosition(
      (value) => {
        if (cancelled || !mounted.current || manualSelection.current) return;
        select(
          {
            latitude: value.coords.latitude,
            longitude: value.coords.longitude,
          },
          "geolocation",
        );
      },
      (failure) => {
        if (cancelled || !mounted.current) return;
        setLocationSource("none");
        if (failure.code === failure.PERMISSION_DENIED) {
          setLocationNote(
            "Location was not shared. Choose a point on the map, a reference landscape, or enter coordinates.",
          );
        } else {
          setLocationNote(
            "Your location could not be read. Choose a point on the map, a reference landscape, or enter coordinates.",
          );
        }
      },
      {
        enableHighAccuracy: false,
        timeout: 10000,
        maximumAge: 300000,
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, offline]);
  const current = (): Point | null => {
    if (!latitude.trim() || !longitude.trim()) return null;
    const p = { latitude: Number(latitude), longitude: Number(longitude) };
    return Number.isFinite(p.latitude) &&
      Number.isFinite(p.longitude) &&
      Math.abs(p.latitude) <= 90 &&
      Math.abs(p.longitude) <= 180
      ? roundedPoint(p)
      : null;
  };
  const requestForecast = async (selected: Point) => {
    if (!enabled || offline || !csrfToken) return;
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
  // Onboarding: when the landscape section scrolls into view, the forecast for
  // the selected point loads once. If geolocation arrives after the section opens,
  // this effect waits for that point instead of loading a fixed region.
  useEffect(() => {
    if (!opened || !enabled || offline || !csrfToken || !point) return;
    if (data || busy) return;
    const key = `${point.latitude}:${point.longitude}`;
    if (autoLoadedFor.current === key) return;
    autoLoadedFor.current = key;
    void requestForecast(point);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, enabled, offline, csrfToken, point, data, busy]);
  const load = async (event: FormEvent) => {
    event.preventDefault();
    const selected = current();
    if (!selected) {
      setError(
        "Enter a latitude from −90 to 90 and longitude from −180 to 180.",
      );
      return;
    }
    select(selected, "manual");
    await requestForecast(selected);
  };
  const locate = () => {
    if (!navigator.geolocation) {
      setError("Location was not shared. Enter coordinates.");
      return;
    }
    setLocating(true);
    setError("");
    const version = revision.current;
    navigator.geolocation.getCurrentPosition(
      (value) => {
        if (mounted.current) {
          if (version === revision.current) {
            const next = roundedPoint({
              latitude: value.coords.latitude,
              longitude: value.coords.longitude,
            });
            select(next, "geolocation");
            // An explicit request from the person may also read the forecast.
            void requestForecast(next);
          }
          setLocating(false);
        }
      },
      () => {
        if (mounted.current) {
          setLocating(false);
          if (version === revision.current)
            setError(
              "Location was not shared. You can enter coordinates instead.",
            );
        }
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
  const sourceLabel =
    locationSource === "geolocation"
      ? "Approximate location"
      : locationSource === "reference"
        ? "Reference landscape"
        : locationSource === "map"
          ? "Point chosen on map"
          : locationSource === "manual"
            ? "Coordinates entered"
            : "No point yet";
  return (
    <section
      ref={sectionRef}
      className="qg-section"
      id="qs-landscape"
      tabIndex={-1}
      data-testid="geo-section"
      aria-labelledby="qg-title"
    >
      <div className="qg-heading">
        <div>
          <span className="qg-eyebrow">
            <MapPin size={17} /> Start with a place
          </span>
          <h2 id="qg-title" tabIndex={-1}>
            {enabled && !offline
              ? "What is the week ahead?"
              : "Put your land in context."}
          </h2>
          <p>
            {enabled && !offline
              ? "Read the coming weather, then turn it into a useful question for your next visit."
              : "Explore a reference landscape, choose a point and decide what you want to understand on the ground."}
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
          <h3>Choose a place to explore</h3>
          <p className="qg-location-hint">
            Allow location to center the map on you, choose a reference
            landscape, or enter your own point.
          </p>
          {locationNote && (
            <p className="qg-location-note" role="status">
              {locationNote}
            </p>
          )}
          <div className="qg-examples">
            {examples.map((example) => (
              <button
                type="button"
                key={example.label}
                aria-pressed={Boolean(
                  current() && samePoint(current()!, example),
                )}
                onClick={() => select(example, "reference")}
              >
                {example.label}
              </button>
            ))}
          </div>
          {current() &&
            !examples.some((example) => samePoint(example, current()!)) && (
              <p
                className="qg-location-state"
                data-testid="geo-location-state"
                role="status"
              >
                Your point is not one of the four prepared reference profiles.
                The map follows your point; the field brief uses a reference
                landscape.
              </p>
            )}
          <form onSubmit={load}>
            <button
              type="button"
              className="qg-locate"
              onClick={locate}
              disabled={locating}
            >
              <LocateFixed size={15} />
              {locating
                ? "Finding your approximate location…"
                : "Use my location"}
            </button>
            <div className="qg-coordinate-inputs">
              <label>
                Latitude
                <input
                  data-testid="geo-lat"
                  inputMode="decimal"
                  value={latitude}
                  onChange={(e) => {
                    invalidate();
                    onPointChange?.(null);
                    setLatitude(e.target.value);
                  }}
                />
              </label>
              <label>
                Longitude
                <input
                  data-testid="geo-lon"
                  inputMode="decimal"
                  value={longitude}
                  onChange={(e) => {
                    invalidate();
                    onPointChange?.(null);
                    setLongitude(e.target.value);
                  }}
                />
              </label>
            </div>
            <button
              type="submit"
              className="qg-load"
              data-testid="geo-load-context"
              disabled={busy}
            >
              {busy ? (
                <RefreshCw size={16} className="qs-spin" />
              ) : (
                <Globe2 size={16} />
              )}{" "}
              {busy
                ? "Reading local context…"
                : enabled && !offline
                  ? "Load local context"
                  : "Set map location"}
            </button>
          </form>
          <p className="qg-sharing">
            {enabled && !offline
              ? "Load a forecast for rounded coordinates. Attach it when you want to use it in a question."
              : "Set your reference point for the regional map."}
          </p>
          {error && (
            <p role="alert" className="qg-error">
              {error}
            </p>
          )}
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
              best-effort; a deployment can configure a contracted provider. If
              a reference tile is wrong,{" "}
              <a
                href="https://www.openstreetmap.org/fixthemap"
                target="_blank"
                rel="noreferrer"
              >
                report a map issue
              </a>
              . The selected point and forecast are independent of the synthetic
              crop example below.
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
              (CC BY 4.0). Reference ET₀ describes a standard reference surface,
              not an irrigation prescription.
            </p>
            <p>
              Optional NASA MODIS imagery provides regional visual context from
              a 250 m product. Its date is shown beside the layer. The Web
              Mercator layer covers approximately 85°S to 85°N. It is not an
              NDVI calculation or a field boundary.
            </p>
          </details>
        </div>
        <div className="qg-context">
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
                    <span>Forecast rainfall</span>
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
                    <i /> Rain
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
                          `${d.date}: rain ${d.precipitation_mm} millimetres, reference ET0 ${d.reference_et0_mm} millimetres, temperature ${d.temperature_min_c} to ${d.temperature_max_c} degrees Celsius`,
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
                  document.getElementById("qs-query")?.focus();
                  document.getElementById("qs-agent-title")?.scrollIntoView({
                    behavior: window.matchMedia(
                      "(prefers-reduced-motion: reduce)",
                    ).matches
                      ? "auto"
                      : "smooth",
                    block: "start",
                  });
                }}
              >
                {attachedRef === data.context_ref
                  ? "Forecast attached · go to question"
                  : "Use this forecast in my question"}
              </button>
            </div>
          )}
          <div className="qg-map-area" id="qg-map-area">
            {opened ? (
              <Suspense
                fallback={
                  <div className="qg-map-loading">
                    Opening the landscape tools…
                  </div>
                }
              >
                <Landscape
                  point={point}
                  onPick={(next) => select(next, "map")}
                  imagery={data?.imagery ?? null}
                  nasaEnabled={nasa}
                  onImageryState={setImageryState}
                />
              </Suspense>
            ) : (
              <div className="qg-map-invitation">
                <Globe2 size={44} strokeWidth={1.2} />
                <div>
                  <strong>A wider view, when you need it.</strong>
                  <p>
                    Open the regional view to place your next field conversation
                    in context.
                  </p>
                </div>
              </div>
            )}
            <div className="qg-layer-line">
              <span
                className="qg-coordinates"
                data-testid="geo-selected-point"
                data-source={locationSource}
              >
                <MapPin size={14} />
                {point
                  ? `${point.latitude.toFixed(3)}, ${point.longitude.toFixed(3)}`
                  : "No point selected"}
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
                    !opened || !point || data?.imagery.state !== "ready_to_load"
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
                  Choose a point on the map, use your location, or select a
                  reference landscape. The map shows OpenStreetMap reference
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
                  OpenStreetMap provides a detailed reference map (not satellite
                  imagery).{" "}
                  <a
                    href="https://www.openstreetmap.org/copyright"
                    target="_blank"
                    rel="noreferrer"
                  >
                    © OpenStreetMap contributors
                  </a>
                  . NASA MODIS offers optional 250 m regional imagery once local
                  context loads; clouds, date and availability vary.
                </>
              )}
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
