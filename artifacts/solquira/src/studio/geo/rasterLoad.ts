export type FirstRasterHooks = {
  isActive: () => boolean;
  onFirstRaster: () => void;
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
        if (!notified) {
          notified = true;
          if (hooks.isActive()) hooks.onFirstRaster();
        }
        return image;
      },
      (error) => {
        throw error;
      },
    );
  };
}
