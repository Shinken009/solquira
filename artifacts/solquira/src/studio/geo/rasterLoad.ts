export type FirstRasterHooks = {
  isActive: () => boolean;
  onFirstRaster: () => void;
  /** Optional hook called for every resolved raster, including the first. */
  onRaster?: () => void;
  /** Optional diagnostic hook; Cesium still owns retries and fallback policy. */
  onError?: (error: unknown) => void;
};

/**
 * Preserve Cesium's requestImage contract while notifying only once that a
 * raster image has been downloaded. `undefined` remains the throttled/deferred
 * signal, rejections propagate to Cesium for its own retry handling, and a
 * disposed component receives no callback.
 */
export function wrapRequestImage<TArgs extends unknown[], TImage>(
  requestImage: (...args: TArgs) => Promise<TImage> | undefined,
  hooks: FirstRasterHooks,
): (...args: TArgs) => Promise<TImage> | undefined {
  let notified = false;
  return (...args: TArgs) => {
    const pending = requestImage(...args);
    if (!pending) return pending;
    return Promise.resolve(pending).then(
      (image) => {
        if (hooks.isActive()) {
          hooks.onRaster?.();
          if (!notified) {
            notified = true;
            hooks.onFirstRaster();
          }
        }
        return image;
      },
      (error) => {
        if (hooks.isActive()) hooks.onError?.(error);
        throw error;
      },
    );
  };
}
