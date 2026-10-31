# Solquira — validación del mapa Cesium y comportamiento responsive

Fecha: 2026-10-31  
Rama: `fix/responsive-cesium-map-20261031`  
PR: https://github.com/Shinken009/solquira/pull/1 (draft)  
Alcance: frontend Solquira (`artifacts/solquira`), CSP de Vercel y pruebas locales. No se fusionó a `main` ni se desplegó producción.

## 1. Problemas confirmados en el árbol real

Se auditó el PR #1 contra los archivos instalados, no contra la orientación inicial:

| Hallazgo                                                         | Evidencia                                                           | Impacto                                                      |
| ---------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------ |
| Natural Earth II era la única capa base                          | `CesiumFieldMap.tsx` inicial, `TileMapServiceImageryProvider` local | Pixelación al ampliar una imagen regional de baja resolución |
| La cámara inicial quedaba a 340 km con una capa de 1:50 M        | captura de referencia y prueba local                                | Detalle territorial insuficiente                             |
| El punto inicial era Coquimbo                                    | `FieldContext.tsx`: `useState<Point>(examples[1])`                  | La app internacional aparecía configurada para Chile         |
| El cambio 2D/3D dependía de un único `requestAnimationFrame`     | `CesiumFieldMap.tsx` del PR                                         | Cámara y canvas podían quedar desalineados tras el morph     |
| La atribución quedaba dentro del canvas y se recortaba en 390 px | captura móvil                                                       | Texto de licencia poco legible                               |
| No había estado visible para teselas detalladas caídas           | el PR solo capturaba el primer raster                               | El usuario no sabía si veía OSM o el respaldo                |
| No había proveedor configurable                                  | URL de OSM fija en el componente                                    | Difícil sustituir el servicio público en producción          |

## 2. Solución aplicada

### 2.1 Arquitectura de capas

Orden de abajo hacia arriba:

1. **Natural Earth II local** (`/cesium/Assets/Textures/NaturalEarthII`): respaldo offline incluido en el build.
2. **Mapa de referencia detallado**: OpenStreetMap por defecto, configurable a MapTiler o a un proveedor XYZ propio.
3. **NASA MODIS Terra** opcional (GIBS, 250 m, nivel máximo 9), sólo cuando el backend entrega `imagery.state = ready_to_load`.

La capa detallada se declara con `UrlTemplateImageryProvider`, `WebMercatorTilingScheme`, `maximumLevel` configurable y crédito HTML visible. El respaldo local permanece debajo: si el proveedor detallado falla, el mapa no desaparece.

### 2.2 Proveedor configurable

Variables de build (documentadas en `artifacts/solquira/.env.example`):

- `VITE_BASEMAP_PROVIDER=osm|maptiler|custom`
- `VITE_BASEMAP_API_KEY` (MapTiler)
- `VITE_BASEMAP_URL` (template HTTPS con `{z}/{x}/{y}`)
- `VITE_BASEMAP_ATTRIBUTION`
- `VITE_BASEMAP_MAX_LEVEL`

`basemap.ts` resuelve la configuración sin tocar el DOM y cae a OSM con un aviso explícito si la configuración solicitada es inválida. La selección es de build-time, por lo que un despliegue puede cambiar de proveedor sin editar componentes.

### 2.3 Auditoría de la política de OpenStreetMap

Se revisó la [Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/) el 2026-10-31:

- URL correcta: `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (HTTPS, sin subdominios).
- Atribución visible: `© OpenStreetMap contributors` con enlace a copyright, en la barra de créditos y en el detalle de fuentes.
- `Referrer-Policy: strict-origin-when-cross-origin` conserva el `Referer` de origen; no se usa `no-referrer`.
- No se envían cabeceras `no-cache`; el navegador respeta el cache del proveedor.
- No hay prefetch, descarga masiva ni uso offline de las teselas públicas.
- El servicio público es _best-effort_, sin SLA. Para producción con tráfico se recomienda MapTiler u otro proveedor contratado; el código ya lo permite.
- Se añadió el enlace recomendado “report a map issue”.
- OpenStreetMap se rotula como mapa de referencia, nunca como imagen satelital.

### 2.4 Correcciones CesiumJS

- Inicialización y destrucción seguras de `CesiumWidget`; limpieza de canvas, créditos, `ResizeObserver`, handler y primitivas.
- Fallback de creación: si falla el modo 3D, se intenta 2D antes de declarar el widget no disponible.
- Cámara inicial: 340 km en escritorio, 230 km en pantallas estrechas; vista mundial neutra si todavía no hay punto.
- `morphTo2D`/`morphTo3D` con `scene.morphComplete` y timeout de seguridad; no se usa un único frame.
- `ResizeObserver` con debounce por `requestAnimationFrame`; `widget.resize()` y `requestRender()`.
- `resolutionScale` limitado según DPR (1,5× en móvil, 1,75× en escritorio) para nitidez sin disparar memoria GPU.
- `tileCacheSize` 24/48 y `maximumScreenSpaceError` 2,5/2.
- Errores de teselas del proveedor detallado: aviso “Reference map · limited detail” y respaldo Natural Earth.
- Errores de NASA: “Satellite tiles are unavailable; the reference map remains available”; la capa se puede apagar.
- La disponibilidad del mapa no depende de Open-Meteo: se verificó con `/solquira-api/studio/bootstrap` devolviendo 500 y el mapa igualmente en `ready`.

### 2.5 Ubicación internacional

- Se eliminó el punto inicial fijo de Coquimbo.
- Al abrir la sección del mapa se solicita la ubicación aproximada del navegador; si se concede, el mapa y el parte se centran en ese punto.
- Si se deniega o falla, el mapa queda en una vista mundial neutra y el usuario puede elegir un punto en el mapa, una de las cuatro referencias o escribir coordenadas.
- Las cuatro referencias (Sahel, valle seco, Andalucía, meseta de África Oriental) siguen disponibles como ejemplos opcionales.
- La línea de coordenadas indica la procedencia: “Approximate location”, “Reference landscape”, “Point chosen on map” o “Coordinates entered”.

### 2.6 Responsive

|   Ancho | Comportamiento                                                    |
| ------: | ----------------------------------------------------------------- |
|  360 px | 2D por defecto, mapa 294×390, botones ≥44 px, créditos visibles   |
|  390 px | 2D por defecto, mapa 316×390, sin scroll horizontal               |
|  768 px | 2D por defecto; el grid pasa a una columna y el mapa mide 638×350 |
| 1366 px | 3D por defecto, mapa 951×520                                      |
| 1920 px | 3D por defecto, mapa 969×520                                      |

La barra de herramientas ocupa 76–80 px en móvil (<25 % de la altura del mapa) y los créditos salen del canvas en una barra propia, por lo que no se recortan.

## 3. Archivos modificados

- `artifacts/solquira/src/studio/geo/CesiumFieldMap.tsx` — reescritura de inicialización, capas, cámara, morph, resize, errores y créditos.
- `artifacts/solquira/src/studio/geo/FieldContext.tsx` — ubicación inicial, estado de procedencia, punto nulo, textos y atribución.
- `artifacts/solquira/src/studio/geo/basemap.ts` — configuración de proveedor y fallback seguro.
- `artifacts/solquira/src/studio/geo/basemap.test.ts` — 5 pruebas de configuración.
- `artifacts/solquira/src/studio/geo/rasterLoad.ts` — hook de error opcional.
- `artifacts/solquira/src/studio/geo/rasterLoad.test.ts` — 7 pruebas (4 existentes + 3 nuevas).
- `artifacts/solquira/src/studio/geo/field-context.css` — créditos, avisos, tipografía móvil y tablet.
- `artifacts/solquira/.env.example` — configuración de proveedor sin secretos.
- `vercel.json` — CSP para OSM y MapTiler; se conserva `Referrer-Policy`.
- `docs/release/SOLQUIRA-MAP-VALIDATION-2026-10-31.md` — este informe.

## 4. Pruebas ejecutadas

Desde la raíz del monorepositorio:

```powershell
pnpm --filter @workspace/solquira typecheck
pnpm --filter @workspace/solquira build
node --test artifacts/solquira/src/studio/geo/rasterLoad.test.ts artifacts/solquira/src/studio/geo/basemap.test.ts
```

Resultados:

- `typecheck`: OK, sin errores.
- `build`: OK; `dist/public` generado. El chunk de Cesium sigue siendo grande (~3,97 MB, advertencia existente).
- Pruebas unitarias: **11/11 PASS**.
- Verificación automatizada con Playwright/Chromium (script temporal, mocks de API y teselas locales): **19/19 PASS**.

Matriz automatizada:

| Prueba                                                                            | Resultado                    |
| --------------------------------------------------------------------------------- | ---------------------------- |
| 360, 390, 768, 1366, 1920 px: estado, escena, overflow, canvas, créditos, toolbar | PASS                         |
| Selección por clic y por toque en el mapa                                         | PASS                         |
| Geolocalización -> punto 37.100, -4.600 + pronóstico                              | PASS                         |
| NASA MODIS activar/desactivar + crédito                                           | PASS                         |
| 2D/3D en teléfono y notebook                                                      | PASS                         |
| Redimensionado 1366 → 768 → 390                                                   | PASS (951 → 638 → 316 px)    |
| Orientación 390×844 → 844×390                                                     | PASS (714×350 en horizontal) |
| OSM caído -> Natural Earth + aviso                                                | PASS                         |
| NASA caída -> mapa de referencia intacto                                          | PASS                         |
| Build estático con CSP de `vercel.json`                                           | PASS, 0 errores CSP/JS       |

La verificación visual con teselas reales de OSM se hizo por separado en 390 px y 1366 px; las pruebas de matriz usan teselas locales para no cargar los servidores públicos de OSM.

## 5. Límites y pendientes

- **No se fusionó ni desplegó** el PR; sigue en draft.
- OpenStreetMap público es _best-effort_. Para producción con tráfico real se debe configurar MapTiler u otro proveedor y ajustar la CSP con el host elegido.
- NASA MODIS depende de GIBS y del backend; nubes, fecha y disponibilidad varían.
- La prueba de matriz usa respuestas API simuladas; no sustituye una prueba contra el backend de producción.
- Las pruebas de navegador se ejecutaron en Chromium headless. No se probó Safari/iOS real ni WebGL por software en un dispositivo físico.
- El fallback 3D→2D está cubierto por código y por el morph probado; no se forzó una pérdida real de contexto WebGL.
- Los perfiles sintéticos de cultivo siguen usando nombres chilenos (Elqui, Limarí, Choapa) como fixtures; no son el punto inicial del mapa, pero una internacionalización completa de esos fixtures queda fuera de este PR.

## 6. Estado del PR

Los cambios se incorporan a `fix/responsive-cesium-map-20261031`. No se modifica `main`, no se ejecuta `vercel --prod` y no se elimina ningún componente.
