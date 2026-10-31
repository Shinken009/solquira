import { useEffect, useRef, useState } from 'react';
import { Cartesian2, Cartesian3, Cartographic, CesiumWidget, Color, Credit, EllipsoidTerrainProvider, PointPrimitiveCollection, OpenStreetMapImageryProvider, SceneMode, WebMercatorTilingScheme, Math as CesiumMath, ScreenSpaceEventHandler, ScreenSpaceEventType, TileMapServiceImageryProvider, UrlTemplateImageryProvider, type ImageryLayer, type PointPrimitive } from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import type { Imagery, Point } from './client';
import { wrapRequestImage } from './rasterLoad';

export default function CesiumFieldMap({ point, onPick, imagery, nasaEnabled, onImageryState }: { point: Point; onPick: (p: Point) => void; imagery: Imagery | null; nasaEnabled: boolean; onImageryState: (state: string) => void }) {
  const container = useRef<HTMLDivElement>(null), viewer = useRef<CesiumWidget | null>(null), marker = useRef<PointPrimitive | null>(null);
  const pick = useRef(onPick), latestPoint = useRef(point), imageryState = useRef(onImageryState);
  pick.current = onPick; latestPoint.current = point; imageryState.current = onImageryState;
  const mobile = typeof window !== 'undefined' && window.matchMedia('(max-width: 720px)').matches;
  const [state, setState] = useState('loading'), [flat, setFlat] = useState(mobile), [detailed, setDetailed] = useState(false);
  const recenter = () => {
    const p = latestPoint.current;
    const height = window.matchMedia('(max-width: 720px)').matches ? 230000 : 340000;
    viewer.current?.camera.setView({ destination: Cartesian3.fromDegrees(p.longitude,p.latitude,height) });
    viewer.current?.scene.requestRender();
  };
  useEffect(() => {
    let disposed = false, instance: CesiumWidget | undefined, handler: ScreenSpaceEventHandler | undefined,
      removePostRender: (() => void) | undefined, loadTimer: number | undefined, resizeObserver: ResizeObserver | undefined;
    const initialize = async () => {
      try {
        if (!container.current) return;
        // Our own toolbar owns interaction. Avoid Viewer/Knockout's import-time
        // dynamic evaluation, so the landscape works without CSP unsafe-eval.
        instance = new CesiumWidget(container.current, {
          baseLayer:false, skyBox:false, skyAtmosphere:false, sceneMode: mobile ? SceneMode.SCENE2D : SceneMode.SCENE3D,
          useBrowserRecommendedResolution:false, msaaSamples:mobile ? 1 : 2,
          terrainProvider:new EllipsoidTerrainProvider(), requestRenderMode:true, maximumRenderTimeChange:Infinity, shouldAnimate:false
        });
        viewer.current = instance;
        instance.scene.globe.tileCacheSize = mobile ? 32 : 64;
        instance.scene.globe.maximumScreenSpaceError = mobile ? 2 : 1.5;
        // Limit physical canvas resolution for high-DPI phones and laptops.
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        instance.resolutionScale = Math.min(1, (mobile ? 2 : 1.5) / dpr);
        if (typeof ResizeObserver !== 'undefined') {
          resizeObserver = new ResizeObserver(() => {
            if (instance && !instance.isDestroyed()) { instance.resize(); instance.scene.requestRender(); }
          });
          resizeObserver.observe(container.current);
        }
        const provider = await TileMapServiceImageryProvider.fromUrl(`${import.meta.env.BASE_URL}cesium/Assets/Textures/NaturalEarthII`, { credit: new Credit('Natural Earth') });
        if (disposed) return;
        let rasterLoaded = false;
        provider.requestImage = wrapRequestImage(provider.requestImage.bind(provider), {
          isActive: () => !disposed,
          onFirstRaster: () => {
            const widget = instance;
            if (disposed || rasterLoaded || !widget) return;
            rasterLoaded = true;
            removePostRender = widget.scene.postRender.addEventListener(() => {
              removePostRender?.();
              removePostRender = undefined;
              if (loadTimer !== undefined) { window.clearTimeout(loadTimer); loadTimer = undefined; }
              if (!disposed) setState('ready');
            });
            widget.scene.requestRender();
          },
        });
        instance.imageryLayers.addImageryProvider(provider);
        // Natural Earth remains beneath the detailed map as an offline fallback.
        try {
          const osm = new OpenStreetMapImageryProvider({
            url: 'https://tile.openstreetmap.org/',
            maximumLevel: 16,
            credit: new Credit('<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>', true),
          });
          osm.requestImage = wrapRequestImage(osm.requestImage.bind(osm), {
            isActive: () => !disposed,
            onFirstRaster: () => {
              if (!disposed) { setDetailed(true); instance?.scene.requestRender(); }
            },
          });
          instance.imageryLayers.addImageryProvider(osm);
        } catch {
          // The bundled fallback is still usable if the remote provider fails.
        }
        loadTimer = window.setTimeout(() => {
          if (!disposed) setState(current => current === 'loading' ? 'unavailable' : current);
        }, 12000);
        const p = latestPoint.current;
        const points = instance.scene.primitives.add(new PointPrimitiveCollection()) as PointPrimitiveCollection;
        marker.current = points.add({ id:'selected-context-point', position:Cartesian3.fromDegrees(p.longitude,p.latitude), pixelSize:14,color:Color.fromCssColorString('#efbb39'),outlineColor:Color.fromCssColorString('#173d36'),outlineWidth:3,disableDepthTestDistance:Infinity });
        handler = new ScreenSpaceEventHandler(instance.scene.canvas);
        handler.setInputAction((event: { position: Cartesian2 }) => {
          const value = instance?.camera.pickEllipsoid(event.position,instance.scene.globe.ellipsoid);
          if (value) { const position = Cartographic.fromCartesian(value); pick.current({ latitude:CesiumMath.toDegrees(position.latitude),longitude:CesiumMath.toDegrees(position.longitude) }); }
        },ScreenSpaceEventType.LEFT_CLICK);
        recenter();
      } catch { if (!disposed) { setState('unavailable'); if (instance && !instance.isDestroyed()) instance.destroy(); viewer.current=null; marker.current=null; } }
    };
    void initialize();
    return () => { disposed=true; resizeObserver?.disconnect(); if (loadTimer !== undefined) window.clearTimeout(loadTimer); removePostRender?.(); handler?.destroy(); if (instance && !instance.isDestroyed()) instance.destroy(); viewer.current=null; marker.current=null; };
  }, []);
  useEffect(() => {
    const instance=viewer.current;
    if (!instance || instance.isDestroyed() || state !== 'ready') return;
    if (marker.current) marker.current.position = Cartesian3.fromDegrees(point.longitude,point.latitude);
    recenter();
  }, [point.latitude,point.longitude,state]);
  useEffect(() => {
    const instance=viewer.current;
    if (!instance || instance.isDestroyed() || state !== 'ready' || !nasaEnabled || imagery?.state !== 'ready_to_load') return;
    let active=true, layer: ImageryLayer | undefined;
    imageryState.current('loading');
    const provider=new UrlTemplateImageryProvider({url:imagery.url_template,tilingScheme:new WebMercatorTilingScheme(),tileWidth:256,tileHeight:256,maximumLevel:imagery.max_level,credit:new Credit('NASA EOSDIS GIBS · MODIS Terra')});
    const original=provider.requestImage.bind(provider);
    provider.requestImage=(...args: Parameters<typeof original>) => {
      const pending=original(...args);
      if (!pending) return pending;
      return Promise.resolve(pending).then(value => { if (active) imageryState.current('received'); return value; }, error => { if (active) imageryState.current('unavailable'); throw error; });
    };
    provider.errorEvent.addEventListener(() => { if (active) imageryState.current('unavailable'); });
    layer=instance.imageryLayers.addImageryProvider(provider);instance.scene.requestRender();
    return () => { active=false; if (!instance.isDestroyed() && layer) { instance.imageryLayers.remove(layer,true);instance.scene.requestRender(); } };
  },[nasaEnabled,imagery?.url_template,state]);
  return <div className="qg-map-wrap" data-testid="geo-map-state" data-state={state} data-raster-state={state === 'ready' ? 'rendered' : state === 'loading' ? 'pending' : 'unavailable'} aria-busy={state === 'loading'}>
    <div className="qg-map" ref={container} aria-label="Interactive Cesium landscape: select a point, zoom or use the coordinate form"/>
    {state === 'unavailable' ? <div className="qg-map-fallback" role="status"><strong>The interactive map is unavailable.</strong><p>You can still select coordinates and read the forecast. Try a browser with WebGL enabled.</p></div> : <div className="qg-map-toolbar"><span>{state === 'ready' ? (nasaEnabled ? 'NASA regional detail' : detailed ? 'Detailed regional map' : 'Regional map · limited detail') : 'Opening the map…'}</span><button type="button" disabled={state!=='ready'} onClick={recenter}>Back to point</button><button type="button" disabled={state!=='ready'} onClick={() => { const v=viewer.current;if(v && !v.isDestroyed()){ const nextFlat=!flat;setFlat(nextFlat);if(nextFlat)v.scene.morphTo2D(0);else v.scene.morphTo3D(0);window.requestAnimationFrame(() => { if(!v.isDestroyed()){v.resize();recenter();v.scene.requestRender();} }); } }}>{flat ? '3D view' : '2D view'}</button></div>}
  </div>;
}
