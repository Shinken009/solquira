import { useLayoutEffect, useRef } from "react";

/**
 * Tab panels (GoogleChrome/modern-web-guidance "tabs" guide): an inactive
 * panel can use hidden="until-found" so find-in-page still discovers its
 * content, and a beforematch listener syncs the selected tab. React does not
 * preserve the attribute's value across re-renders, so the ref manages it
 * directly; useLayoutEffect avoids a first-paint flash of every panel open.
 */
export function useHiddenUntilFound<T extends HTMLElement>(inactive: boolean) {
  const ref = useRef<T>(null);
  useLayoutEffect(() => {
    const panel = ref.current;
    if (!panel) return;
    if (inactive) panel.setAttribute("hidden", "until-found");
    else panel.removeAttribute("hidden");
  }, [inactive]);
  return ref;
}
