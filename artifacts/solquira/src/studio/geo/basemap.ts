export type BasemapProviderId = "osm" | "maptiler" | "custom";

export type BasemapEnv = Record<string, string | undefined>;

export interface BasemapConfig {
  /** Provider requested at build time. */
  requestedProvider: BasemapProviderId;
  /** Provider actually usable with the current configuration. */
  provider: BasemapProviderId;
  /** XYZ template consumed by Cesium's UrlTemplateImageryProvider. */
  urlTemplate: string;
  /** HTML credit shown by Cesium. Build-time configuration is trusted. */
  attributionHtml: string;
  /** Highest zoom requested from the provider. */
  maxLevel: number;
  /**
   * True when the public OpenStreetMap tile service is in use. That service is
   * best-effort and must not be treated as a production SLA.
   */
  publicOsm: boolean;
  /** Human-readable reason when the requested provider could not be used. */
  warning?: string;
}

const OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION =
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>';
const MAPTILER_ATTRIBUTION =
  '<a href="https://www.maptiler.com/copyright/" target="_blank" rel="noopener noreferrer">© MapTiler</a> ' +
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">© OpenStreetMap contributors</a>';

const readLevel = (value: string | undefined, fallback: number): number => {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 && parsed <= 22
    ? parsed
    : fallback;
};

const readProvider = (value: string | undefined): BasemapProviderId => {
  const normalized = (value || "osm").trim().toLowerCase();
  return normalized === "maptiler" || normalized === "custom"
    ? normalized
    : "osm";
};

const isUsableTemplate = (value: string): boolean =>
  /^https:\/\//i.test(value) &&
  value.includes("{z}") &&
  value.includes("{x}") &&
  value.includes("{y}");

/**
 * Resolve the basemap without touching the DOM. Keeping this pure makes the
 * provider policy testable and keeps the Cesium component free of env parsing.
 */
export function resolveBasemapConfig(env: BasemapEnv): BasemapConfig {
  const requestedProvider = readProvider(env.VITE_BASEMAP_PROVIDER);
  const overrideUrl = (env.VITE_BASEMAP_URL || "").trim();
  const overrideAttribution = (env.VITE_BASEMAP_ATTRIBUTION || "").trim();
  const osmConfig: BasemapConfig = {
    requestedProvider,
    provider: "osm",
    urlTemplate: OSM_URL,
    attributionHtml: OSM_ATTRIBUTION,
    maxLevel: 18,
    publicOsm: true,
  };

  if (requestedProvider === "maptiler") {
    const key = (env.VITE_BASEMAP_API_KEY || "").trim();
    const template =
      overrideUrl ||
      (key
        ? `https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key=${encodeURIComponent(key)}`
        : "");
    if (isUsableTemplate(template)) {
      return {
        requestedProvider,
        provider: "maptiler",
        urlTemplate: template,
        attributionHtml: overrideAttribution || MAPTILER_ATTRIBUTION,
        maxLevel: readLevel(env.VITE_BASEMAP_MAX_LEVEL, 19),
        publicOsm: false,
      };
    }
    return {
      ...osmConfig,
      warning:
        "MapTiler was requested but no tile URL or API key was configured; using the public OpenStreetMap fallback.",
    };
  }

  if (requestedProvider === "custom") {
    if (isUsableTemplate(overrideUrl)) {
      return {
        requestedProvider,
        provider: "custom",
        urlTemplate: overrideUrl,
        attributionHtml: overrideAttribution || OSM_ATTRIBUTION,
        maxLevel: readLevel(env.VITE_BASEMAP_MAX_LEVEL, 18),
        publicOsm: false,
      };
    }
    return {
      ...osmConfig,
      warning:
        "A custom basemap was requested without a valid HTTPS {z}/{x}/{y} URL; using the public OpenStreetMap fallback.",
    };
  }

  return overrideUrl && isUsableTemplate(overrideUrl)
    ? {
        ...osmConfig,
        urlTemplate: overrideUrl,
        attributionHtml: overrideAttribution || OSM_ATTRIBUTION,
        maxLevel: readLevel(env.VITE_BASEMAP_MAX_LEVEL, 18),
        publicOsm: /(^|\.)openstreetmap\.org$/i.test(
          (() => {
            try {
              return new URL(overrideUrl.replace(/\{[^}]+\}/g, "0")).hostname;
            } catch {
              return "";
            }
          })(),
        ),
      }
    : osmConfig;
}

const env = (import.meta as ImportMeta & { env?: BasemapEnv }).env ?? {};

/** Build-time basemap selection. Change it with VITE_BASEMAP_* variables. */
export const basemapConfig = resolveBasemapConfig(env);
