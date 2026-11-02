import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { Moon, Sun } from "lucide-react";
import "./preferences.css";

export type StudioLocale = "en" | "es";
export type StudioTheme = "light" | "dark";
const LOCALE_KEY = "solquira.locale";
const THEME_KEY = "solquira.theme";

function stored(key: string): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(key);
  } catch {
    // Preferences remain usable in private/blocked-storage browsing.
    return null;
  }
}

function initialLocale(): StudioLocale {
  const value = stored(LOCALE_KEY);
  if (value === "en" || value === "es") return value;
  return typeof navigator !== "undefined" && navigator.language.toLowerCase().startsWith("es")
    ? "es"
    : "en";
}

function initialTheme(): StudioTheme {
  // An explicit light default avoids relying on automatic browser darkening.
  return stored(THEME_KEY) === "dark" ? "dark" : "light";
}

function applyPreferences(locale: StudioLocale, theme: StudioTheme) {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute(
    "content", theme === "dark" ? "#12221e" : "#fbfcf7",
  );
}

// Set the surface before the first React paint, without an inline CSP script.
const firstLocale = initialLocale();
const firstTheme = initialTheme();
applyPreferences(firstLocale, firstTheme);

type Preferences = {
  locale: StudioLocale;
  theme: StudioTheme;
  setLocale: (locale: StudioLocale) => void;
  setTheme: (theme: StudioTheme) => void;
};
const PreferencesContext = createContext<Preferences | null>(null);

export function StudioPreferencesProvider({ children }: { children: ReactNode }) {
  const [locale, updateLocale] = useState<StudioLocale>(firstLocale);
  const [theme, updateTheme] = useState<StudioTheme>(firstTheme);
  const setLocale = useCallback((value: StudioLocale) => {
    if (value !== "en" && value !== "es") return;
    updateLocale(value);
    try { window.localStorage.setItem(LOCALE_KEY, value); } catch { /* optional storage */ }
  }, []);
  const setTheme = useCallback((value: StudioTheme) => {
    if (value !== "light" && value !== "dark") return;
    updateTheme(value);
    try { window.localStorage.setItem(THEME_KEY, value); } catch { /* optional storage */ }
  }, []);

  useLayoutEffect(() => applyPreferences(locale, theme), [locale, theme]);
  useEffect(() => {
    const receive = (event: StorageEvent) => {
      if (event.key === LOCALE_KEY && (event.newValue === "en" || event.newValue === "es")) updateLocale(event.newValue);
      if (event.key === THEME_KEY && (event.newValue === "light" || event.newValue === "dark")) updateTheme(event.newValue);
    };
    window.addEventListener("storage", receive);
    return () => window.removeEventListener("storage", receive);
  }, []);

  const value = useMemo(() => ({ locale, theme, setLocale, setTheme }), [locale, theme, setLocale, setTheme]);
  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function useStudioPreferences(): Preferences {
  const preferences = useContext(PreferencesContext);
  if (!preferences) throw new Error("Studio preferences need their provider.");
  return preferences;
}

/** Local catalogues translate presentation only; IDs and user content stay intact. */
export function useStudioCopy(messages: Record<string, string>) {
  const { locale, theme } = useStudioPreferences();
  const t = useCallback((english: string) => locale === "es" && Object.hasOwn(messages, english) ? messages[english] : english, [locale, messages]);
  return { t, locale, theme };
}

export function StudioPreferenceControls() {
  const { locale, theme, setLocale, setTheme } = useStudioPreferences();
  const spanish = locale === "es";
  return (
    <div className="qs-preferences" data-testid="studio-preferences">
      <div className="qs-preference-group" role="group" aria-label={spanish ? "Idioma" : "Language"}>
        <button type="button" lang="es" aria-pressed={spanish} onClick={() => setLocale("es")} data-testid="locale-es">Español</button>
        <button type="button" lang="en" aria-pressed={!spanish} onClick={() => setLocale("en")} data-testid="locale-en">English</button>
      </div>
      <div className="qs-preference-group" role="group" aria-label={spanish ? "Apariencia" : "Appearance"}>
        <button type="button" aria-pressed={theme === "light"} onClick={() => setTheme("light")} data-testid="theme-light">
          <Sun size={15} aria-hidden="true" /><span>{spanish ? "Claro" : "Light"}</span>
        </button>
        <button type="button" aria-pressed={theme === "dark"} onClick={() => setTheme("dark")} data-testid="theme-dark">
          <Moon size={15} aria-hidden="true" /><span>{spanish ? "Oscuro" : "Dark"}</span>
        </button>
      </div>
    </div>
  );
}
