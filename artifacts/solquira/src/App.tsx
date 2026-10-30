import { lazy, Suspense } from "react";
import { Route, Switch, useLocation, Router as WouterRouter } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";

// One product: the Solquira field studio. The previous planner and its demo
// surface were removed for the v1.0 release instead of being shipped alongside.
const Studio = lazy(() => import("./studio/Studio"));

function NotFound() {
  const link = { color: "#173d36", fontWeight: 600 } as const;
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        padding: 24,
        fontFamily: "system-ui, sans-serif",
        color: "#1d2b24",
      }}
    >
      <div style={{ maxWidth: 460 }}>
        <h1 style={{ fontSize: 24, margin: 0 }}>Page not found</h1>
        <p style={{ marginTop: 12, color: "#4a5a51" }}>
          This address is not part of the Solquira studio.
        </p>
        <p style={{ marginTop: 16 }}>
          <a href={import.meta.env.BASE_URL} style={link}>
            Back to Solquira
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
  return (
    <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
      <RoutedErrorBoundary>
        <Switch>
          <Route path="/">
            <Suspense
              fallback={
                <p role="status" style={{ padding: 24 }}>
                  Opening Solquira Studio…
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
