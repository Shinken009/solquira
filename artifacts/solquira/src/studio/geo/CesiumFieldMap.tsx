import { useCallback, useEffect, useRef, useState } from "react";
import {
  Cartesian2,
  Cartesian3,
  Cartographic,
  CesiumWidget,
  Color,
  Credit,
  EllipsoidTerrainProvider,
  Math as CesiumMath,
  PointPrimitiveCollection,
  SceneMode,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  TileMapServiceImageryProvider,
  UrlTemplateImageryProvider,
  WebMercatorTilingScheme,
  type ImageryLayer,
  type PointPrimitive,
} from "cesium";
import "cesium/Build/Cesium/Widgets/widgets.css";
import { basemapConfig } from "./basemap";
import type { Imagery, Point } from "./client";
import { wrapRequestImage } from "./rasterLoad";

type MapState = "loading" | "ready" | "unavailable";
type BasemapState = "loading" | "ready" | "fallback";
type SceneView = "2d" | "3d";

const DESKTOP_HEIGHT = 340_000;
const NARROW_HEIGHT = 230_000;
const WORLD_HEIGHT = 20_000_000;
const NARROW_VIEWPORT = "(max-width: 900px)";

const prefersFlatView = () =>
  typeof window !== "undefined" && window.matchMedia(NARROW_VIEWPORT).matches;

const cameraHeight = () => (prefersFlatView() ? NARROW_HEIGHT : DESKTOP_HEIGHT);

const sceneModeFor = (view: SceneView) =>
  view === "2d" ? SceneMode.SCENE2D : SceneMode.SCENE3D;

export default function CesiumFieldMap({
  point,
  onPick,
  imagery,
  nasaEnabled,
  onImageryState,
}: {
  point: Point | null;
  onPick: (p: Point) => void;
  imagery: Imagery | null;
  nasaEnabled: boolean;
  onImageryState: (state: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const credits = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<CesiumWidget | null>(null);
  const markerRef = useRef<PointPrimitive | null>(null);
  const latestPoint = useRef<Point | null>(point);
  const pick = useRef(onPick);
  const imageryState = useRef(onImageryState);
  const viewRef = useRef<SceneView>(prefersFlatView() ? "2d" : "3d");
  const morphingRef = useRef(false);
  const [state, setState] = useState<MapState>("loading");
  const [basemap, setBasemap] = useState<BasemapState>("loading");
  const [view, setView] = useState<SceneView>(viewRef.current);
  const [morphing, setMorphing] = useState(false);
  const [notice, setNotice] = useState("");

  latestPoint.current = point;
  pick.current = onPick;
  imageryState.current = onImageryState;
  viewRef.current = view;

  const recenter = useCallback(() => {
    const widget = widgetRef.current;
    if (!widget || widget.isDestroyed()) return;
    const selected = latestPoint.current;
    const destination = selected
      ? Cartesian3.fromDegrees(
          selected.longitude,
          selected.latitude,
          cameraHeight(),
        )
      : Cartesian3.fromDegrees(0, 15, WORLD_HEIGHT);
    widget.camera.setView({ destination });
    widget.scene.requestRender();
  }, []);

  const syncMarker = useCallback((selected: Point | null) => {
    const marker = markerRef.current;
    if (!marker) return;
    if (!selected) {
      marker.show = false;
      return;
    }
    marker.show = true;
    marker.position = Cartesian3.fromDegrees(
      selected.longitude,
      selected.latitude,
    );
  }, []);

  useEffect(() => {
    let disposed = false;
    let widget: CesiumWidget | undefined;
    let handler: ScreenSpaceEventHandler | undefined;
    let points: PointPrimitiveCollection | undefined;
    let removePostRender: (() => void) | undefined;
    let removeRenderError: (() => void) | undefined;
    let resizeObserver: ResizeObserver | undefined;
    let resizeFrame = 0;
    let readyTimer = 0;
    let fallbackTimer = 0;

    const createWidget = (mode: SceneMode, creditContainer?: Element) =>
      new CesiumWidget(container.current!, {
        // The app owns the toolbar and the base layers. Viewer/Knockout is
        // intentionally avoided so the map works without CSP unsafe-eval.
        baseLayer: false,
        skyBox: false,
        skyAtmosphere: false,
        sceneMode: mode,
        useBrowserRecommendedResolution: false,
        msaaSamples: mode === SceneMode.SCENE2D ? 1 : 2,
        terrainProvider: new EllipsoidTerrainProvider(),
        requestRenderMode: true,
        maximumRenderTimeChange: Infinity,
        shouldAnimate: false,
        showRenderLoopErrors: false,
        creditContainer,
      });

    const markReady = () => {
      if (disposed || !widget || widget.isDestroyed()) return;
      if (removePostRender) return;
      removePostRender = widget.scene.postRender.addEventListener(() => {
        removePostRender?.();
        removePostRender = undefined;
        window.clearTimeout(readyTimer);
        readyTimer = 0;
        if (!disposed) setState("ready");
      });
      widget.scene.requestRender();
    };

    const markBasemapUnavailable = () => {
      if (disposed) return;
      setBasemap((current) => (current === "ready" ? current : "fallback"));
      setNotice(
        "Detailed map tiles are unavailable. Showing the coarse Natural Earth fallback; coordinates and forecast still work.",
      );
    };

    const initialize = async () => {
      if (!container.current) return;
      credits.current?.replaceChildren();
      const startView = viewRef.current;
      try {
        try {
          widget = createWidget(
            sceneModeFor(startView),
            credits.current ?? undefined,
          );
        } catch (error) {
          // A device may expose a 2D-capable context but fail to create the 3D
          // scene. Keep a usable map instead of declaring the whole widget dead.
          if (startView === "3d") {
            widget = createWidget(
              SceneMode.SCENE2D,
              credits.current ?? undefined,
            );
            setView("2d");
          } else {
            throw error;
          }
        }
        widgetRef.current = widget;
        const scene = widget.scene;
        const isFlat = scene.mode === SceneMode.SCENE2D;
        scene.backgroundColor = Color.fromCssColorString("#173d36");
        scene.globe.tileCacheSize = isFlat ? 24 : 48;
        scene.globe.maximumScreenSpaceError = isFlat ? 2.5 : 2;
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        widget.resolutionScale = Math.min(1, (isFlat ? 1.5 : 1.75) / dpr);
        widget.resize();

        removeRenderError = scene.renderError.addEventListener(() => {
          if (!disposed) {
            setNotice(
              "The map stopped rendering. You can still choose coordinates and read the forecast; try 2D or reload.",
            );
          }
        });

        if (typeof ResizeObserver !== "undefined") {
          resizeObserver = new ResizeObserver(() => {
            if (resizeFrame || disposed) return;
            resizeFrame = window.requestAnimationFrame(() => {
              resizeFrame = 0;
              if (disposed || !widget || widget.isDestroyed()) return;
              widget.resize();
              scene.requestRender();
            });
          });
          resizeObserver.observe(container.current);
        }

        // Natural Earth is bundled with the app and remains underneath the
        // detailed provider. A network or tile failure therefore degrades to a
        // coarse map instead of removing the map or the rest of the page.
        const fallbackProvider = await TileMapServiceImageryProvider.fromUrl(
          `${import.meta.env.BASE_URL}cesium/Assets/Textures/NaturalEarthII`,
          { credit: new Credit("Natural Earth") },
        );
        if (disposed) {
          return;
        }
        fallbackProvider.requestImage = wrapRequestImage(
          fallbackProvider.requestImage.bind(fallbackProvider),
          {
            isActive: () => !disposed,
            onFirstRaster: markReady,
          },
        );
        widget.imageryLayers.addImageryProvider(fallbackProvider);

        // Detailed reference map. The provider is selected at build time so a
        // production deployment can use a contracted tile service without
        // changing components. Public OSM tiles are best-effort and attributed.
        try {
          const detailed = new UrlTemplateImageryProvider({
            url: basemapConfig.urlTemplate,
            tilingScheme: new WebMercatorTilingScheme(),
            tileWidth: 256,
            tileHeight: 256,
            maximumLevel: basemapConfig.maxLevel,
            credit: new Credit(basemapConfig.attributionHtml, true),
          });
          let detailedLoaded = false;
          detailed.requestImage = wrapRequestImage(
            detailed.requestImage.bind(detailed),
            {
              isActive: () => !disposed,
              onFirstRaster: () => {
                detailedLoaded = true;
                window.clearTimeout(fallbackTimer);
                fallbackTimer = 0;
                if (!disposed) {
                  setBasemap("ready");
                  setNotice("");
                  widget?.scene.requestRender();
                }
              },
            },
          );
          detailed.errorEvent.addEventListener((error) => {
            if (error.timesRetried >= 2 && !detailedLoaded)
              markBasemapUnavailable();
          });
          widget.imageryLayers.addImageryProvider(detailed);
          fallbackTimer = window.setTimeout(markBasemapUnavailable, 8_000);
        } catch {
          markBasemapUnavailable();
        }

        // A missing bundled raster is fatal for the map, but not for the rest of
        // the field context. The timeout covers a provider that never resolves.
        readyTimer = window.setTimeout(() => {
          if (!disposed) setState("unavailable");
        }, 12_000);

        points = widget.scene.primitives.add(
          new PointPrimitiveCollection(),
        ) as PointPrimitiveCollection;
        markerRef.current = points.add({
          id: "selected-context-point",
          position: Cartesian3.fromDegrees(0, 0),
          pixelSize: 14,
          color: Color.fromCssColorString("#efbb39"),
          outlineColor: Color.fromCssColorString("#173d36"),
          outlineWidth: 3,
          disableDepthTestDistance: Infinity,
          show: false,
        });
        syncMarker(latestPoint.current);

        handler = new ScreenSpaceEventHandler(scene.canvas);
        handler.setInputAction((event: { position: Cartesian2 }) => {
          const position = widget?.camera.pickEllipsoid(
            event.position,
            scene.globe.ellipsoid,
          );
          if (!position) return;
          const cartographic = Cartographic.fromCartesian(position);
          if (!cartographic) return;
          pick.current({
            latitude: CesiumMath.toDegrees(cartographic.latitude),
            longitude: CesiumMath.toDegrees(cartographic.longitude),
          });
        }, ScreenSpaceEventType.LEFT_CLICK);

        recenter();
        markReady();
      } catch {
        if (!disposed) {
          setState("unavailable");
          setNotice(
            "The interactive map is unavailable. Coordinates and forecast remain usable.",
          );
        }
        if (widget && !widget.isDestroyed()) widget.destroy();
        widgetRef.current = null;
        markerRef.current = null;
        credits.current?.replaceChildren();
      }
    };

    void initialize();

    return () => {
      disposed = true;
      window.clearTimeout(readyTimer);
      window.clearTimeout(fallbackTimer);
      if (resizeFrame) window.cancelAnimationFrame(resizeFrame);
      resizeObserver?.disconnect();
      removePostRender?.();
      removeRenderError?.();
      handler?.destroy();
      if (points && widget && !widget.isDestroyed()) {
        widget.scene.primitives.remove(points);
      }
      if (widget && !widget.isDestroyed()) widget.destroy();
      widgetRef.current = null;
      markerRef.current = null;
      credits.current?.replaceChildren();
      container.current?.replaceChildren();
    };
  }, [recenter, syncMarker]);

  useEffect(() => {
    const widget = widgetRef.current;
    if (!widget || widget.isDestroyed()) return;
    syncMarker(point);
    if (state === "ready" && !morphingRef.current) recenter();
  }, [point?.latitude, point?.longitude, state, recenter, syncMarker]);

  useEffect(() => {
    const widget = widgetRef.current;
    if (
      !widget ||
      widget.isDestroyed() ||
      state !== "ready" ||
      !nasaEnabled ||
      imagery?.state !== "ready_to_load"
    ) {
      return;
    }
    let active = true;
    let layer: ImageryLayer | undefined;
    imageryState.current("loading");
    const provider = new UrlTemplateImageryProvider({
      url: imagery.url_template,
      tilingScheme: new WebMercatorTilingScheme(),
      tileWidth: 256,
      tileHeight: 256,
      maximumLevel: imagery.max_level,
      credit: new Credit("NASA EOSDIS GIBS · MODIS Terra", true),
    });
    provider.requestImage = wrapRequestImage(
      provider.requestImage.bind(provider),
      {
        isActive: () => active && !widget.isDestroyed(),
        onFirstRaster: () => imageryState.current("received"),
        onError: () => imageryState.current("unavailable"),
      },
    );
    provider.errorEvent.addEventListener(() => {
      if (active) imageryState.current("unavailable");
    });
    layer = widget.imageryLayers.addImageryProvider(provider);
    widget.scene.requestRender();
    return () => {
      active = false;
      if (!widget.isDestroyed() && layer) {
        widget.imageryLayers.remove(layer, true);
        widget.scene.requestRender();
      }
    };
  }, [nasaEnabled, imagery?.url_template, state]);

  const changeView = () => {
    const widget = widgetRef.current;
    if (!widget || widget.isDestroyed() || morphingRef.current) return;
    const scene = widget.scene;
    const current: SceneView = scene.mode === SceneMode.SCENE2D ? "2d" : "3d";
    const next: SceneView = current === "2d" ? "3d" : "2d";
    if (next === current) {
      setView(current);
      return;
    }
    morphingRef.current = true;
    setMorphing(true);
    setNotice("");
    let settled = false;
    let removeMorph: (() => void) | undefined;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      removeMorph?.();
      morphingRef.current = false;
      setMorphing(false);
      setView(scene.mode === SceneMode.SCENE2D ? "2d" : "3d");
      widget.resize();
      recenter();
      scene.requestRender();
    };
    const timeout = window.setTimeout(finish, 2_500);
    removeMorph = scene.morphComplete.addEventListener(finish);
    try {
      if (next === "2d") scene.morphTo2D(0.55);
      else scene.morphTo3D(0.55);
    } catch {
      window.clearTimeout(timeout);
      removeMorph?.();
      settled = true;
      morphingRef.current = false;
      setMorphing(false);
      setView("2d");
      setNotice(
        "3D is not available on this device. The 2D map remains active.",
      );
    }
  };

  const title =
    state !== "ready"
      ? "Opening the map…"
      : nasaEnabled
        ? "Satellite detail · NASA MODIS"
        : basemap === "fallback"
          ? "Reference map · limited detail"
          : !point
            ? "World view · choose a point"
            : basemap === "ready"
              ? "Detailed reference map"
              : "Reference map";

  return (
    <div
      className="qg-map-wrap"
      data-testid="geo-map-state"
      data-state={state}
      data-basemap-state={basemap}
      data-scene-mode={view}
      data-morphing={morphing ? "true" : "false"}
      data-raster-state={
        state === "ready"
          ? "rendered"
          : state === "loading"
            ? "pending"
            : "unavailable"
      }
      aria-busy={state === "loading" || morphing}
    >
      <div
        className="qg-map"
        ref={container}
        aria-label="Interactive Cesium landscape: select a point, zoom or use the coordinate form"
      />
      {state === "unavailable" ? (
        <div className="qg-map-fallback" role="status">
          <strong>The interactive map is unavailable.</strong>
          <p>
            You can still select coordinates and read the forecast. Try a
            browser with WebGL enabled or use the 2D view.
          </p>
        </div>
      ) : (
        <div className="qg-map-toolbar">
          <span title={basemapConfig.warning ?? notice}>{title}</span>
          <button
            type="button"
            disabled={state !== "ready" || morphing || !point}
            onClick={recenter}
          >
            Back to point
          </button>
          <button
            type="button"
            aria-label={
              view === "2d" ? "Switch to 3D view" : "Switch to 2D view"
            }
            disabled={state !== "ready" || morphing}
            onClick={changeView}
          >
            {morphing
              ? "Changing view…"
              : view === "2d"
                ? "3D view"
                : "2D view"}
          </button>
        </div>
      )}
      <div
        className="qg-map-credits"
        ref={credits}
        aria-label="Map attribution"
      />
      {notice ? (
        <p className="qg-map-notice" role="status">
          {notice}
        </p>
      ) : null}
    </div>
  );
}
