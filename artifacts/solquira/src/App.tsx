import { lazy, Suspense } from "react";
import { Route, Switch, useLocation, Router as WouterRouter } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import { useStudioCopy } from "./studio/StudioPreferences";
import { studioCopy } from "./studio/studio-copy";

// One product: the Solquira field studio. The previous planner and its demo
// surface were removed for the v1.0 release instead of being shipped alongside.
const Studio = lazy(() => import("./studio/Studio"));

function NotFound() {
  const { t } = useStudioCopy(studioCopy);
  const link = { color: "var(--ui-primary)", fontWeight: 600 } as const;
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        fontFamily: "system-ui, sans-serif",
        color: "var(--ui-ink)",
      }}
    >
      <div style={{ maxWidth: 460 }}>
        <h1 style={{ fontSize: 24, margin: 0 }}>{t("Page not found")}</h1>
        <p style={{ marginTop: 12, color: "var(--ui-muted)" }}>
          {t("This address is not part of the Solquira studio.")}
        </p>
        <p style={{ marginTop: 16 }}>
          <a href={import.meta.env.BASE_URL} style={link}>
            {t("Back to Solquira")}
          </a>
        </p>
      </div>
    </main>
  );
}

function RoutedErrorBoundary({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  return <ErrorBoundary resetKey={location}>{children}</ErrorBoundary>;
}

export default function App() {
  const { t } = useStudioCopy(studioCopy);
  return (
    <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
      <RoutedErrorBoundary>
        <Switch>
          <Route path="/">
            <Suspense
              fallback={
                <p role="status" style={{ padding: 24 }}>
                  {t("Opening Solquira Studio…")}
                </p>
              }
            >
              <Studio />
            </Suspense>
          </Route>
          <Route component={NotFound} />
        </Switch>
      </RoutedErrorBoundary>
    </WouterRouter>
  );
}
