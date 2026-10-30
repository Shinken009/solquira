import { useEffect, useRef, useState } from 'react';
import { Cartesian2, Cartesian3, Cartographic, CesiumWidget, Color, Credit, EllipsoidTerrainProvider, PointPrimitiveCollection, WebMercatorTilingScheme, Math as CesiumMath, ScreenSpaceEventHandler, ScreenSpaceEventType, TileMapServiceImageryProvider, UrlTemplateImageryProvider, type ImageryLayer, type PointPrimitive } from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import type { Imagery, Point } from './client';

export default function CesiumFieldMap({ point, onPick, imagery, nasaEnabled, onImageryState }: { point: Point; onPick: (p: Point) => void; imagery: Imagery | null; nasaEnabled: boolean; onImageryState: (state: string) => void }) {
  const container = useRef<HTMLDivElement>(null), viewer = useRef<CesiumWidget | null>(null), marker = useRef<PointPrimitive | null>(null);
  const pick = useRef(onPick), latestPoint = useRef(point), imageryState = useRef(onImageryState);
  pick.current = onPick; latestPoint.current = point; imageryState.current = onImageryState;
  const [state, setState] = useState('loading'), [flat, setFlat] = useState(false);
  const recenter = () => { const p = latestPoint.current; viewer.current?.camera.setView({ destination: Cartesian3.fromDegrees(p.longitude,p.latitude,650000) }); viewer.current?.scene.requestRender(); };
  useEffect(() => {
    let disposed = false, instance: CesiumWidget | undefined, handler: ScreenSpaceEventHandler | undefined;
    const initialize = async () => {
      try {
        if (!container.current) return;
        // Our own toolbar owns interaction. Avoid Viewer/Knockout's import-time
        // dynamic evaluation, so the landscape works without CSP unsafe-eval.
        instance = new CesiumWidget(container.current, { baseLayer:false, skyBox:false, skyAtmosphere:false,
          terrainProvider:new EllipsoidTerrainProvider(), requestRenderMode:true, maximumRenderTimeChange:Infinity, shouldAnimate:false });
        viewer.current = instance;
        instance.scene.globe.tileCacheSize = 64;
        instance.scene.globe.maximumScreenSpaceError = 1.5;
        instance.resolutionScale = Math.min(window.devicePixelRatio, 1.5);
        const provider = await TileMapServiceImageryProvider.fromUrl(`${import.meta.env.BASE_URL}cesium/Assets/Textures/NaturalEarthII`, { credit: new Credit('Natural Earth') });
        if (disposed) return;
        instance.imageryLayers.addImageryProvider(provider);
        const p = latestPoint.current;
        const points = instance.scene.primitives.add(new PointPrimitiveCollection()) as PointPrimitiveCollection;
        marker.current = points.add({ id:'selected-context-point', position:Cartesian3.fromDegrees(p.longitude,p.latitude), pixelSize:14,color:Color.fromCssColorString('#efbb39'),outlineColor:Color.fromCssColorString('#173d36'),outlineWidth:3,disableDepthTestDistance:Infinity });
        handler = new ScreenSpaceEventHandler(instance.scene.canvas);
        handler.setInputAction((event: { position: Cartesian2 }) => {
          const value = instance?.camera.pickEllipsoid(event.position,instance.scene.globe.ellipsoid);
          if (value) { const position = Cartographic.fromCartesian(value); pick.current({ latitude:CesiumMath.toDegrees(position.latitude),longitude:CesiumMath.toDegrees(position.longitude) }); }
        },ScreenSpaceEventType.LEFT_CLICK);
        recenter(); setState('ready');
      } catch { if (!disposed) { setState('unavailable'); if (instance && !instance.isDestroyed()) instance.destroy(); viewer.current=null; marker.current=null; } }
    };
    void initialize();
    return () => { disposed=true; handler?.destroy(); if (instance && !instance.isDestroyed()) instance.destroy(); viewer.current=null; marker.current=null; };
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
  return <div className="qg-map-wrap" data-testid="geo-map-state" data-state={state}>
    <div className="qg-map" ref={container} aria-label="Interactive Cesium landscape: select a point, zoom or use the coordinate form"/>
    {state === 'unavailable' ? <div className="qg-map-fallback" role="status"><strong>This device cannot render 3D.</strong><p>You can still select coordinates and read their forecast below.</p></div> : <div className="qg-map-toolbar"><span>{state === 'ready' ? 'Cesium landscape' : 'Opening the globe…'}</span><button type="button" disabled={state!=='ready'} onClick={recenter}>Back to point</button><button type="button" disabled={state!=='ready'} onClick={() => { const v=viewer.current;if(v){ if(flat)v.scene.morphTo3D(0);else v.scene.morphTo2D(0);setFlat(!flat);recenter();v.scene.requestRender(); } }}>{flat ? '3D view' : '2D view'}</button></div>}
  </div>;
}
