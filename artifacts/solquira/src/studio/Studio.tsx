import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type MouseEvent,
  type KeyboardEvent,
} from "react";

import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  Droplets,
  MapPin,
  FileCheck2,
  Layers3,
  Leaf,
  LoaderCircle,
  RefreshCw,
  Send,
  ShieldCheck,
  Sprout,
  Sun,
  Wheat,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";

import {
  askStudio,
  bootstrapStudio,
  evaluateStudio,
  offlineBootstrap,
  offlineEvaluate,
  STATIC_STUDIO,
  StudioRequestError,
} from "./client";

import {
  CROP_LABELS,
  INITIAL_SCENARIO,
  PROFILE_LABELS,
  sameScenario,
  type Bootstrap,
  type Envelope,
  type Plan,
  type Scenario,
  type StudioView,
} from "./types";

import "./studio.css";
import "./terrain-study.css";

import FieldContext from "./geo/FieldContext";

import FieldBrief from "./FieldBrief";
import EvidenceSynthesis from "./EvidenceSynthesis";
import DrylandFieldBrief from "./DrylandFieldBrief";
import { useHiddenUntilFound } from "./hidden-until-found";
import type { FieldRecord } from "./types";

import BrandMark from "./BrandMark";

import LandCareRecord from "./LandCareRecord";
import LandEvidencePanel from "./LandEvidencePanel";
import GuidedExploration, { type GuidedRecordDraft } from "./GuidedExploration";

import type { AttachedContext, Point } from "./geo/client";
import { StudioPreferenceControls, useStudioCopy } from "./StudioPreferences";
import { studioCopy } from "./studio-copy";

const toolLabels: Record<string, string> = {
  read_landscape_context: "Connect the location forecast",
  leer_escenario: "Understand your example",
  comparar_escenarios: "Check water & compare crop paths",
  revisar_evidencia: "Look at the sources",
  revisar_suelo_cobertura: "Review soil & cover questions",
  preparar_decision: "Prepare your next checks",
};

const sourceLabels: Record<string, string> = {
  "parcel-input": "Example field profile",
  "demo-rules": "Synthetic comparison rules",
};

const scrollToSection = (element: HTMLElement | null) =>
  element?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
    block: "start",
  });

const profileHints: Record<Scenario["profile_id"], string> = {
  valle: "An irrigated valley example.",
  secano: "A water-limited dryland example.",
  precordillera: "A foothill landscape example.",
};

const prompts = [
  "Why do some plans not fit?",
  "What should I check about soil cover?",
  "What changes when water is limited?",
];

export type WorkspaceArea =
  "territory" | "anticipation" | "guidance" | "evidence";
const areas: {
  id: WorkspaceArea;
  label: string;
  target: string;
  detail: string;
}[] = [
  {
    id: "territory",
    label: "Territory",
    target: "qs-landscape",
    detail: "Choose a place",
  },
  {
    id: "anticipation",
    label: "Anticipation",
    target: "qs-anticipation",
    detail: "Read the coming week",
  },
  {
    id: "guidance",
    label: "Guidance",
    target: "qs-guidance",
    detail: "Your knowledge, one next check",
  },
  {
    id: "evidence",
    label: "Evidence",
    target: "qs-evidence-area",
    detail: "Review sources and limits",
  },
];
const areaForTarget = (target: string): WorkspaceArea =>
  ["qs-landscape", "qs-about"].includes(target)
    ? "territory"
    : target === "qs-anticipation"
      ? "anticipation"
      : ["qs-evidence-area", "qs-review", "qs-connections"].includes(target)
        ? "evidence"
        : "guidance";

function TerrainStudy() {
  const { t } = useStudioCopy(studioCopy);
  return (
    <figure className="qs-terrain">
      <div className="qs-terrain-frame">
        <img
          className="qs-terrain-art"
          src={`${import.meta.env.BASE_URL}solquira-rural-landscape.svg`}
          alt={t(
            "A grower tending crop paths beside a river in a planted valley",
          )}
          width={2048}
          height={1152}
          loading="eager"
          decoding="async"
        />
      </div>
      <figcaption>
        <span>{t("One landscape. Many livelihoods.")}</span>
        <span>
          {t("Water")} <i /> {t("Soil")} <i /> {t("People")}
        </span>
      </figcaption>
    </figure>
  );
}

function CropIcon({ crop }: { crop: string }) {
  const Icon = crop.toLowerCase().includes("cereal")
    ? Wheat
    : crop.toLowerCase().includes("fallow")
      ? Sun
      : crop.toLowerCase().includes("cover")
        ? Leaf
        : Sprout;

  return <Icon size={18} strokeWidth={1.7} aria-hidden="true" />;
}

function PlanRow({ plan, available }: { plan: Plan; available: number }) {
  const { t, locale } = useStudioCopy(studioCopy);
  const number = (value: number) =>
    new Intl.NumberFormat(locale === "es" ? "es-CL" : "en-US", {
      maximumFractionDigits: 1,
    }).format(value);
  const shortage = plan.balance_m3_ha < 0;

  const status = plan.eligible
    ? "Fits this example"
    : shortage && !plan.history_ok
      ? "Water + rotation constraint"
      : shortage
        ? "Water shortfall"
        : "Previous group repeats";

  const limit = Math.max(available, plan.demand_m3_ha, 1);

  return (
    <article
      className={`qs-plan ${plan.eligible ? "qs-plan-match" : ""}`}
      data-testid="plan-row"
    >
      <div className="qs-plan-title">
        <span className="qs-plan-letter">
          {plan.id.replace("rotation-", "").toUpperCase()}
        </span>
        <div>
          <h3>{plan.sequence.map((crop) => t(crop)).join(" → ")}</h3>
          <span className="qs-plan-status">
            {plan.eligible ? (
              <Check size={13} />
            ) : (
              <span className="qs-status-dash" />
            )}
            {t(status)}
          </span>
        </div>
      </div>

      <ol className="qs-cycles" aria-label={t("Illustrative crop sequence")}>
        {plan.sequence.map((crop, index) => (
          <li key={`${plan.id}-${index}`}>
            <CropIcon crop={crop} />
            <span>
              {t("Cycle")} {index + 1}
            </span>
            <strong>{t(crop)}</strong>
          </li>
        ))}
      </ol>

      <div className="qs-water-chart">
        <div className="qs-water-caption">
          <span>{t("Assumed demand")}</span>
          <strong>
            {number(plan.demand_m3_ha)} <small>m³/ha</small>
          </strong>
        </div>
        <div className="qs-water-track" aria-hidden="true">
          <i
            style={{
              width: `${Math.min(100, (plan.demand_m3_ha / limit) * 100)}%`,
            }}
          />
          <b style={{ left: `${Math.min(99, (available / limit) * 100)}%` }} />
        </div>
        <div className="qs-water-balance">
          {shortage
            ? `${number(Math.abs(plan.balance_m3_ha))} ${t("m³/ha below assumption")}`
            : `${number(plan.balance_m3_ha)} ${t("m³/ha above assumption")}`}
        </div>
      </div>

      <div className="qs-plan-details">
        <div className="qs-rule-checks">
          <span className={shortage ? "qs-check-hold" : "qs-check-pass"}>
            <Droplets size={14} />
            {shortage ? t("Water: shortfall") : t("Water: enough in example")}
          </span>
          <span className={plan.history_ok ? "qs-check-pass" : "qs-check-hold"}>
            <Sprout size={14} />
            {plan.history_ok
              ? t("History: different group")
              : t("History: repeats group")}
          </span>
        </div>
        <details>
          <summary>
            {t("Understand these checks")} <ChevronRight size={14} />
          </summary>
          <p>
            {shortage
              ? t("The scenario water is below this sequence’s assumed demand.")
              : t(
                  "The scenario water covers this sequence’s assumed demand.",
                )}{" "}
            {!plan.history_ok
              ? t(
                  "Its first cycle repeats the previous crop group. More water alone does not resolve that restriction.",
                )
              : t("Its first cycle differs from the previous crop group.")}
          </p>
          <p>
            {t("Use these checks to start a conversation with your adviser.")}
          </p>
        </details>
      </div>
    </article>
  );
}

export default function Studio() {
  const { t, locale } = useStudioCopy(studioCopy);
  const number = (value: number) =>
    new Intl.NumberFormat(locale === "es" ? "es-CL" : "en-US", {
      maximumFractionDigits: 1,
    }).format(value);
  const [area, setArea] = useState<WorkspaceArea>(() =>
    window.location.hash
      ? areaForTarget(window.location.hash.slice(1))
      : "territory",
  );
  // Inactive area panels stay searchable by find-in-page (tabs guide).
  const guidancePanelRef = useHiddenUntilFound<HTMLElement>(area !== "guidance");
  const evidencePanelRef = useHiddenUntilFound<HTMLElement>(area !== "evidence");
  const navigateArea = useCallback((next: WorkspaceArea, target?: string) => {
    setArea(next);
    requestAnimationFrame(() => {
      const node = document.getElementById(
        target ?? areas.find((item) => item.id === next)!.target,
      );
      // Explicit links to examples also reveal their progressive disclosure.
      for (
        let parent = node?.parentElement;
        parent;
        parent = parent.parentElement
      )
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      scrollToSection(node);
      if (node && !node.matches("a,button,input,select,textarea,[tabindex]"))
        node.tabIndex = -1;
      node?.focus({ preventScroll: true });
    });
  }, []);
  const activateArea = (next: WorkspaceArea) => {
    setArea(next);
    const target = areas.find((item) => item.id === next)!.target;
    history.replaceState(null, "", `#${target}`);
    requestAnimationFrame(() =>
      scrollToSection(document.getElementById(target)),
    );
  };
  const followWorkspaceLink = (event: MouseEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey)
      return;
    const link = (event.target as HTMLElement).closest<HTMLAnchorElement>(
      'a[href^="#"]',
    );
    const target = link?.getAttribute("href")?.slice(1);
    if (!target || !document.getElementById(target)) return;
    event.preventDefault();
    history.replaceState(null, "", `#${target}`);
    navigateArea(areaForTarget(target), target);
  };
  const areaKeys = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % areas.length;
    else if (event.key === "ArrowLeft")
      next = (index + areas.length - 1) % areas.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = areas.length - 1;
    else return;
    event.preventDefault();
    activateArea(areas[next].id);
    document.getElementById(`qs-area-tab-${areas[next].id}`)?.focus();
  };
  useEffect(() => {
    const changed = () => {
      const target = window.location.hash.slice(1);
      navigateArea(
        target ? areaForTarget(target) : "territory",
        target || undefined,
      );
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, [navigateArea]);
  useEffect(() => {
    // Find-in-page revealing an inactive until-found panel activates its area
    // without scrolling: the UA is already scrolling to the matched content.
    const onBeforeMatch = (event: Event) => {
      const panel = (event.target as HTMLElement | null)?.closest<HTMLElement>(
        "[data-beforematch-area]",
      );
      const next = panel?.dataset.beforematchArea as WorkspaceArea | null;
      if (!next || next === area) return;
      setArea(next);
      const target = areas.find((item) => item.id === next)!.target;
      history.replaceState(null, "", `#${target}`);
    };
    document.addEventListener("beforematch", onBeforeMatch);
    return () => document.removeEventListener("beforematch", onBeforeMatch);
  }, [area]);
  const [mode, setMode] = useState<"service" | "offline">(
    STATIC_STUDIO ? "offline" : "service",
  );

  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);

  const [scenario, setScenario] = useState<Scenario>(INITIAL_SCENARIO);

  const scenarioRef = useRef<Scenario>(INITIAL_SCENARIO);

  const [result, setResult] = useState<Envelope | null>(null);

  const [agentResult, setAgentResult] = useState<Envelope | null>(null);

  const [proposal, setProposal] = useState<Scenario | null>(null);

  const [tab, setTab] = useState<StudioView>("soil");

  const [query, setQuery] = useState("");

  const [busy, setBusy] = useState(false);

  const [agentBusy, setAgentBusy] = useState(false);

  const [connecting, setConnecting] = useState(true);

  const [error, setError] = useState("");

  const [agentError, setAgentError] = useState("");

  const [agentRecovery, setAgentRecovery] = useState(false);

  const [attachedContext, setAttachedContext] =
    useState<AttachedContext | null>(null);

  const [attachedRecord, setAttachedRecord] = useState<FieldRecord | null>(
    null,
  );
  const recordRef = useRef<FieldRecord | null>(null);
  const contextRevision = useRef(0);

  const attachedRef = useRef<AttachedContext | null>(null);

  const [refresh, setRefresh] = useState(0);
  const [briefPoint, setBriefPoint] = useState<Point | null>(null);
  const [evidenceContextRef, setEvidenceContextRef] = useState<string | null>(
    null,
  );
  const [requestedPoint, setRequestedPoint] = useState<Point | null>(null);
  const [guidedDraft, setGuidedDraft] = useState<GuidedRecordDraft | null>(
    null,
  );

  const revision = useRef(0);

  const evaluationController = useRef<AbortController | null>(null);

  const agentController = useRef<AbortController | null>(null);

  const queryRef = useRef<HTMLTextAreaElement>(null);

  const comparisonRef = useRef<HTMLElement>(null);

  const reviewRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const controller = new AbortController();

    const current = ++revision.current;

    setConnecting(true);
    setBootstrap(null);
    setError("");
    setAgentError("");
    setAgentRecovery(false);
    setBusy(false);
    setAgentBusy(false);
    setResult(null);
    setAgentResult(null);
    setProposal(null);

    attachedRef.current = null;
    setAttachedContext(null);
    recordRef.current = null;
    setAttachedRecord(null);
    contextRevision.current++;

    const initialize = async () => {
      try {
        const boot =
          mode === "offline"
            ? offlineBootstrap()
            : await bootstrapStudio(controller.signal);

        if (controller.signal.aborted || current !== revision.current) return;

        setBootstrap(boot);

        const requested = { ...scenarioRef.current };

        const value =
          mode === "offline"
            ? offlineEvaluate(requested)
            : await evaluateStudio(
                requested,
                boot.csrf_token,
                controller.signal,
              );

        if (
          controller.signal.aborted ||
          current !== revision.current ||
          !sameScenario(requested, scenarioRef.current)
        )
          return;

        if (!sameScenario(value.scenario, requested))
          throw new Error("The returned scenario does not match your inputs.");

        setResult(value);
      } catch (caught) {
        if (!controller.signal.aborted && current === revision.current)
          setError(
            caught instanceof Error
              ? caught.message
              : "The Studio could not be initialized.",
          );
      } finally {
        if (!controller.signal.aborted && current === revision.current)
          setConnecting(false);
      }
    };

    void initialize();

    return () => {
      controller.abort();
      evaluationController.current?.abort();
      agentController.current?.abort();
    };
  }, [mode, refresh]);

  const attachContext = useCallback((context: AttachedContext | null) => {
    attachedRef.current = context;
    contextRevision.current++;

    agentController.current?.abort();
    setAgentBusy(false);
    setAgentResult(null);
    setProposal(null);
    setAgentError("");

    setAgentRecovery(false);
    setAttachedContext(context);
  }, []);

  const attachRecord = useCallback((record: FieldRecord | null) => {
    recordRef.current = record;
    contextRevision.current++;
    agentController.current?.abort();
    setAgentBusy(false);
    setAgentResult(null);
    setProposal(null);
    setAgentError("");
    setAgentRecovery(false);
    setAttachedRecord(record);
  }, []);

  const changeScenario = useCallback((patch: Partial<Scenario>) => {
    const next = { ...scenarioRef.current, ...patch };

    if (sameScenario(next, scenarioRef.current)) return;

    scenarioRef.current = next;
    revision.current += 1;

    evaluationController.current?.abort();
    agentController.current?.abort();

    setScenario(next);
    setResult(null);
    setAgentResult(null);
    setProposal(null);
    setError("");
    setAgentError("");
    setBusy(false);
    setAgentBusy(false);
  }, []);

  const compare = async (event?: FormEvent) => {
    event?.preventDefault();

    if (!bootstrap || connecting) return;

    evaluationController.current?.abort();
    agentController.current?.abort();

    const controller = new AbortController();
    evaluationController.current = controller;

    const requested = { ...scenarioRef.current };
    const current = ++revision.current;

    setBusy(true);
    setError("");
    setAgentResult(null);
    setProposal(null);
    setAgentError("");
    setAgentBusy(false);

    try {
      const value =
        mode === "offline"
          ? offlineEvaluate(requested)
          : await evaluateStudio(
              requested,
              bootstrap.csrf_token,
              controller.signal,
            );

      if (
        controller.signal.aborted ||
        current !== revision.current ||
        !sameScenario(requested, scenarioRef.current)
      )
        return;

      if (!sameScenario(value.scenario, requested))
        throw new Error("The returned scenario does not match your inputs.");

      setResult(value);
    } catch (caught) {
      if (!controller.signal.aborted && current === revision.current) {
        setResult(null);
        setError(
          caught instanceof Error
            ? caught.message
            : "This comparison could not be completed.",
        );
      }
    } finally {
      if (!controller.signal.aborted && current === revision.current)
        setBusy(false);
    }
  };

  const ask = async (event?: FormEvent) => {
    event?.preventDefault();

    if (
      !bootstrap ||
      (!result && !attachedRef.current) ||
      busy ||
      connecting ||
      !query.trim() ||
      mode === "offline"
    )
      return;

    agentController.current?.abort();

    const controller = new AbortController();
    agentController.current = controller;

    const requested = { ...scenarioRef.current };
    const current = revision.current;

    const contextVersion = contextRevision.current,
      context = attachedRef.current;

    setAgentBusy(true);
    setAgentError("");
    setAgentRecovery(false);
    setAgentResult(null);
    setProposal(null);

    try {
      const value = await askStudio(
        requested,
        query.trim(),
        bootstrap.csrf_token,
        controller.signal,
        context?.reference,
        recordRef.current ?? undefined,
      );

      if (
        controller.signal.aborted ||
        current !== revision.current ||
        contextVersion !== contextRevision.current ||
        !sameScenario(requested, scenarioRef.current)
      )
        return;

      if (
        context &&
        value.report.field_brief &&
        (value.report.field_brief.location.latitude !==
          context.location.latitude ||
          value.report.field_brief.location.longitude !==
            context.location.longitude)
      )
        throw new Error(
          "The field brief refers to a different point. Reload your context.",
        );

      if (!sameScenario(value.scenario, requested))
        throw new Error(
          "The assistant returned a different requested scenario. Your inputs are unchanged.",
        );

      if (
        value.report.scenario &&
        !sameScenario(value.report.scenario, requested)
      ) {
        const proposed = value.proposed_scenario;

        if (!proposed || !sameScenario(proposed, value.report.scenario))
          throw new Error(
            "The proposed alternative could not be verified. Your inputs are unchanged.",
          );

        setProposal(proposed);
      } else setAgentResult(value);
    } catch (caught) {
      if (!controller.signal.aborted && current === revision.current) {
        setAgentError(
          caught instanceof Error
            ? caught.message
            : "The explanation could not be completed.",
        );
        setAgentRecovery(
          caught instanceof StudioRequestError &&
            (caught.status === 401 || caught.status === 403),
        );
      }
    } finally {
      if (!controller.signal.aborted && current === revision.current)
        setAgentBusy(false);
    }
  };

  const reconnectSession = () => {
    evaluationController.current?.abort();
    agentController.current?.abort();

    setAgentBusy(false);
    setBusy(false);
    setAgentError("");
    setAgentRecovery(false);

    setRefresh((value) => value + 1);
  };

  const usePrompt = (prompt: string) => {
    setQuery(t(prompt));
    queryRef.current?.focus();
  };

  const reviewField = (destination: "weather" | "soil" | "question") => {
    if (destination === "weather") {
      navigateArea("anticipation");
    } else if (destination === "soil") openReview("soil");
    else
      usePrompt(
        "What field inputs do I need before deciding on irrigation or soil cover?",
      );
  };

  const openReview = (view: StudioView) => {
    setTab(view);
    navigateArea("evidence", `qs-tab-${view}`);
  };

  const openComparison = () => {
    navigateArea("guidance", "qs-comparison-title");
  };

  const openTool = (tool: string) => {
    if (tool === "read_landscape_context") reviewField("weather");
    else if (tool === "revisar_evidencia") openReview("evidence");
    else if (tool === "revisar_suelo_cobertura") openReview("soil");
    else if (tool === "preparar_decision") openReview("decision");
    else if (tool === "comparar_escenarios") openComparison();
    else {
      navigateArea("guidance", "qs-profile");
    }
  };

  const report = result?.report;

  const count = report?.plans.filter((plan) => plan.eligible).length ?? 0;

  const available = report?.available_water_m3_ha ?? 0;

  const canAsk = Boolean(
    bootstrap &&
    (attachedContext || result) &&
    mode === "service" &&
    !busy &&
    !connecting,
  );

  const displayedReport = agentResult?.report ?? report;

  const sourceMode =
    mode === "service" && briefPoint
      ? "Place selected"
      : mode === "offline"
        ? "Sample workspace"
        : connecting
          ? "Connecting…"
          : bootstrap
            ? "Ready to choose a place"
            : "Service unavailable · retry";

  // The chip used to describe an internal transport state ("Studio connected").
  // It now states what the visitor is actually looking at, and the full
  // explanation travels with it for assistive tech and hover.
  const sourceHint = bootstrap
    ? "Your place stays selected across all four areas."
    : undefined;

  const returnedTools = agentResult?.run.trace ?? [];

  const inferenceMode =
    agentResult?.run.inference_mode ?? bootstrap?.inference.mode;

  const liveResponse =
    mode === "service" && agentResult?.run.inference_mode === "nebius_live";

  const inferenceAvailable =
    mode === "service" && Boolean(bootstrap?.inference.available);

  const model = agentResult?.run.model ?? bootstrap?.inference.model;

  const modelLabel =
    model === "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
      ? "Nemotron 3 Nano"
      : model;

  const provider = bootstrap?.inference.provider;

  const providerLabel =
    provider === "nebius" ? "Nebius Token Factory" : provider;

  const providerFallback =
    mode === "service" &&
    agentResult?.run.inference_mode === "local_fallback" &&
    !["disabled", "success"].includes(agentResult.run.outcome);

  const fallbackStatus = [
    "trial_limit",
    "budget_exceeded",
    "budget_limit",
  ].includes(agentResult?.run.outcome ?? "")
    ? "Model composition paused at the session limit"
    : agentResult?.run.outcome === "budget_unavailable"
      ? "Model composition paused by the budget guard"
      : agentResult?.run.outcome === "timeout"
        ? "Model composition exceeded its deadline"
        : "Composed by field rules";

  const budgetStatus =
    bootstrap?.inference.mode === "budget_exhausted"
      ? "Session limit reached"
      : bootstrap?.inference.mode === "budget_unavailable"
        ? "Budget guard active"
        : null;

  const aiStatus =
    mode === "offline"
      ? "Field rules only"
      : liveResponse
        ? "Live response received"
        : inferenceMode === "provider_stub"
          ? "Test composition"
          : providerFallback
            ? fallbackStatus
            : (budgetStatus ?? "Field rules · always on");

  const openQuestion = () => {
    navigateArea(
      "guidance",
      mode === "service" ? "qs-query" : "qs-agent-title",
    );
  };

  const openFieldBrief = () => {
    navigateArea(
      "guidance",
      mode === "service" ? "qs-field-vitals" : "qs-land-record",
    );
  };

  const ruleVersion = bootstrap?.rule_version;

  const methodVersion = ruleVersion?.match(/^demo-rules-(\d+(?:\.\d+)+)$/)?.[1];

  return (
    <div
      className="qs-studio"
      onClickCapture={followWorkspaceLink}
      data-active-area={area}
      data-testid={bootstrap && !connecting ? "studio-ready" : "studio-loading"}
    >
      <a className="qs-skip" href="#qs-landscape">
        {t("Skip to the landscape")}
      </a>

      <header className="qs-header">
        <a
          className="qs-brand"
          href={import.meta.env.BASE_URL}
          aria-label={t("Solquira home")}
        >
          <BrandMark className="qs-brand-symbol" />
          <span>
            Solquira
            <small>{t("Understand the land. Cultivate the future.")}</small>
          </span>
        </a>
        <div className="qs-header-status">
          <StudioPreferenceControls />
          <a className="qs-about-link" href="#qs-about">
            {t("About Solquira")}
          </a>
          <span
            data-testid="connection-mode"
            className={`qs-connection ${mode === "offline" ? "qs-is-offline" : ""}`}
            title={sourceHint ? t(sourceHint) : undefined}
            aria-label={sourceHint ? t(sourceHint) : undefined}
          >
            {mode === "offline" ? <Layers3 size={14} /> : <Wifi size={14} />}
            <span>{t(sourceMode)}</span>
          </span>
        </div>
      </header>

      <nav className="qs-area-nav" aria-label={t("Solquira workspace")}>
        <div role="tablist" aria-label={t("Workspace areas")}>
          {areas.map((item, index) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`qs-area-tab-${item.id}`}
              aria-controls={item.target}
              aria-selected={area === item.id}
              tabIndex={area === item.id ? 0 : -1}
              data-testid={`area-tab-${item.id}`}
              onClick={() => activateArea(item.id)}
              onKeyDown={(event) => areaKeys(event, index)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{t(item.label)}</strong>
              <small>{t(item.detail)}</small>
            </button>
          ))}
        </div>
      </nav>

      <main className="qs-main">
        <section
          className="qs-journey-status"
          aria-label={t("Field journey")}
          data-testid="journey-next-step"
        >
          <div>
            <strong>{t("Place → forecast → next check")}</strong>
            <p>
              {briefPoint
                ? t("Your place stays selected across all four areas.")
                : t("Choose and confirm a place to begin.")}
            </p>
          </div>
          {briefPoint && area === "anticipation" && (
            <button type="button" onClick={() => navigateArea("guidance", "qj-exploration-title")}>
              {t("Next: choose what to check")}
              <ArrowRight size={16} aria-hidden="true" />
            </button>
          )}
        </section>
        <div className="qs-area-intro" hidden={area !== "territory"}>
          <section className="qs-intro" aria-labelledby="qs-title">
            <div className="qs-intro-copy">
              <div className="qs-kicker">
                <span className="qs-dot" />{" "}
                {t("For the land. For the people who care for it.")}
              </div>
              <h1 id="qs-title">
                {t("Start with a place.")}
                <br />
                {t("Find your next step.")}
              </h1>
              <p>
                {mode === "offline"
                  ? t(
                      "Explore a landscape and prepared examples. Choose one useful question for your next visit.",
                    )
                  : t(
                      "Choose the land you care for. Bring what you notice, understand the weather context, and keep one manageable next check.",
                    )}
              </p>
              <div className="qs-hero-actions">
                <a className="qs-hero-cta" href="#qs-landscape">
                  {t("Start with a place")} <MapPin size={18} />
                </a>
                <a className="qs-hero-secondary" href="#qs-scenario">
                  {t("Try a crop example")}
                </a>
              </div>
            </div>
            <TerrainStudy />
          </section>

          <section className="qs-purpose" aria-label={t("Our purpose")}>
            <span className="qs-purpose-symbol">
              <BrandMark />
            </span>
            <div>
              <strong>{t("Healthy land. Resilient communities.")}</strong>
              <p>
                {t(
                  "Water, soil and livelihoods belong in the same conversation. Turn what you know about your land into a practical next observation, with sources and limits you can review.",
                )}
              </p>
            </div>
            <a href="#qs-land-record">
              {t("Make the next visit count")} <ArrowRight size={17} />
            </a>
          </section>

          <nav className="qs-workflow" aria-label={t("Field journey")}>
            <a className="qs-step-water" href="#qs-landscape">
              <span>
                <MapPin size={19} />
              </span>
              <div>
                <small>{t("1. Understand the place")}</small>
                {mode === "service" && bootstrap?.geodata?.enabled
                  ? t("Read the coming weather")
                  : t("See the wider landscape")}
              </div>
            </a>
            <button
              className="qs-step-soil"
              onClick={() => navigateArea("guidance", "qs-exploration")}
            >
              <span>
                <Leaf size={19} />
              </span>
              <div>
                <small>
                  {mode === "offline"
                    ? t("2. Explore a choice")
                    : t("2. Share what matters")}
                </small>
                {mode === "offline"
                  ? t("Connect water, soil & cover")
                  : t("Connect your knowledge to a next check")}
              </div>
            </button>
            <button className="qs-step-decision" onClick={openFieldBrief}>
              <span>
                <FileCheck2 size={19} />
              </span>
              <div>
                <small>{t("3. Keep the brief")}</small>
                {t("Record what you find")}
              </div>
            </button>
          </nav>
        </div>

        <FieldContext
          activeArea={area}
          onNavigate={navigateArea}
          enabled={Boolean(bootstrap?.geodata?.enabled)}
          offline={mode === "offline"}
          csrfToken={bootstrap?.csrf_token ?? ""}
          onAttach={attachContext}
          attachedRef={attachedContext?.reference}
          onPointChange={setBriefPoint}
          onEvidenceContextChange={setEvidenceContextRef}
          externalPoint={requestedPoint}
        />

        <section
          id="qs-guidance"
          role="tabpanel"
          aria-labelledby="qs-area-tab-guidance"
          tabIndex={0}
          data-testid="area-guidance"
          data-beforematch-area="guidance"
          ref={guidancePanelRef}
        >
          <GuidedExploration
            key={
              briefPoint
                ? `guided:${briefPoint.latitude}:${briefPoint.longitude}`
                : "guided:no-place"
            }
            point={briefPoint}
            onChoosePlace={() => navigateArea("territory", "qs-landscape")}
            onWeather={() => navigateArea("anticipation", "qs-anticipation")}
            onRecord={(draft) => {
              setGuidedDraft(draft);
              navigateArea("guidance", "qs-land-record");
            }}
          />
          <LandEvidencePanel
            point={briefPoint}
            contextRef={evidenceContextRef}
            csrfToken={bootstrap?.csrf_token ?? ""}
            enabled={mode === "service" && Boolean(bootstrap) && !connecting}
            onChoosePlace={() => navigateArea("territory", "qs-landscape")}
            onReviewForecast={() => navigateArea("territory", "qs-landscape")}
            onOpenRecord={() => navigateArea("guidance", "qs-land-record")}
          />
          <details
            className="qs-prepared-examples"
            data-testid="prepared-field-examples"
          >
            <summary>
              {t("Prepared crop examples")}{" "}
              <ChevronRight size={16} aria-hidden="true" />
            </summary>
            <div className="qs-prepared-heading">
              <span className="qs-index">
                {t("Keep exploration and examples distinct")}
              </span>
              <p>
                {t(
                  "The guided check above comes from your answers. Prepared crop scenarios below are teaching examples, not properties or recommendations for your selected land.",
                )}
              </p>
            </div>
            {mode === "service" && (
              <DrylandFieldBrief
                key={
                  briefPoint
                    ? `brief:${briefPoint.latitude}:${briefPoint.longitude}`
                    : "brief:unselected"
                }
                point={briefPoint}
                inference={bootstrap?.inference}
                csrf={bootstrap?.csrf_token ?? ""}
                enabled={Boolean(bootstrap) && !connecting}
                onSelectPlace={(point) => {
                  setRequestedPoint({ ...point });
                  navigateArea("territory", "qs-landscape");
                }}
              />
            )}
          </details>

          {mode === "offline" ? (
            <section
              id="qs-question"
              className="qs-assistant qs-offline-summary"
              aria-labelledby="qs-agent-title"
              data-testid="offline-explanation"
            >
              <div className="qs-agent-heading">
                <FileCheck2 size={26} strokeWidth={1.5} />
                <div>
                  <span className="qs-index">
                    {t("A choice worth understanding")}
                  </span>
                  <h2 id="qs-agent-title" tabIndex={-1}>
                    {t("Less guesswork. A clearer next conversation.")}
                  </h2>
                </div>
              </div>
              {report ? (
                <p className="qs-offline-explanation">
                  {t("At")} {number(available)} {t("m³/ha of example water,")}{" "}
                  <strong>
                    {count} {t("of")} {report.plans.length} {t("crop paths")}
                  </strong>{" "}
                  {t(
                    "meet the water and rotation checks. Explore the trade-offs, then record what needs a closer look.",
                  )}
                </p>
              ) : (
                <p className="qs-offline-explanation">
                  {t(
                    "Choose your inputs and compare the crop paths to explore their trade-offs.",
                  )}
                </p>
              )}
              <div className="qs-sample-actions">
                <button type="button" onClick={openComparison}>
                  {t("Explore crop paths")} <ArrowRight size={15} />
                </button>
                <a href="#qs-land-record">{t("Start your field record")}</a>
              </div>
            </section>
          ) : (
            <section
              id="qs-question"
              className="qs-assistant"
              aria-labelledby="qs-agent-title"
            >
              <div className="qs-agent-heading">
                <div className="qs-agent-sign">
                  <BrandMark />
                </div>
                <div>
                  <span className="qs-index">
                    {t("From context to conversation")}
                  </span>
                  <h2 id="qs-agent-title" tabIndex={-1}>
                    {t("Ask about your next field visit.")}
                  </h2>
                </div>
                <span className="qs-mode-label">
                  {liveResponse
                    ? t("Live model response")
                    : inferenceMode === "provider_stub"
                      ? t("Test composition")
                      : providerFallback || budgetStatus
                        ? t("Field rules · always on")
                        : inferenceAvailable
                          ? t("Nemotron ready when you ask")
                          : t("Field rules · always on")}
                </span>
              </div>
              <p className="qs-agent-intro">
                {attachedRecord ? (
                  t(
                    "Your selected observations are attached. Ask which check is most useful with the resources and time you have.",
                  )
                ) : attachedContext ? (
                  t(
                    "Your forecast is attached. Ask what to check about water, soil or living cover, then take the brief into a conversation with your adviser.",
                  )
                ) : (
                  <>
                    {t(
                      "Attach a forecast above to prepare field checks. Without one, questions use the separate",
                    )}{" "}
                    <a href="#qs-scenario">{t("synthetic crop example")}</a>{" "}
                    {t("below.")}
                  </>
                )}
              </p>
              <div className="qs-prompts">
                {(attachedContext
                  ? [
                      t(
                        "What does this forecast mean for my next field checks?",
                      ),
                      t("What should I check about soil cover?"),
                      t("Which field observations should I bring?"),
                    ]
                  : prompts
                ).map((prompt) => (
                  <button
                    key={prompt}
                    disabled={!canAsk || agentBusy}
                    onClick={() => usePrompt(t(prompt))}
                  >
                    {t(prompt)}
                    <ChevronRight size={13} />
                  </button>
                ))}
              </div>
              {attachedContext && (
                <div
                  className="qs-attached-context"
                  data-testid="attached-context"
                >
                  <Leaf size={18} />
                  <div>
                    <strong>{t("Seven-day forecast attached")}</strong>
                    <span>
                      {attachedContext.location.latitude.toFixed(3)},{" "}
                      {attachedContext.location.longitude.toFixed(3)} ·
                      Open-Meteo
                    </span>
                  </div>
                  <button type="button" onClick={() => attachContext(null)}>
                    {t("Remove")}
                  </button>
                </div>
              )}
              {attachedRecord && (
                <div
                  className="qs-attached-context"
                  data-testid="attached-record"
                >
                  <Leaf size={18} />
                  <div>
                    <strong>{t("Your selected field observations")}</strong>
                    <span>
                      {attachedRecord.observed_at}{" "}
                      {t("· Self-reported cover, water access and constraint")}
                    </span>
                  </div>
                  <button type="button" onClick={() => attachRecord(null)}>
                    {t("Remove")}
                  </button>
                </div>
              )}
              <form className="qs-question-form" onSubmit={ask}>
                <label className="qs-sr-only" htmlFor="qs-query">
                  {t("Question about the current scenario")}
                </label>
                <textarea
                  id="qs-query"
                  data-testid="agent-query"
                  ref={queryRef}
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  maxLength={1000}
                  rows={2}
                  disabled={!canAsk || agentBusy}
                  placeholder={
                    attachedContext
                      ? t("What should I check on my next field visit?")
                      : t(
                          "Ask about the crop example, or attach a forecast first.",
                        )
                  }
                />
                <div className="qs-question-bottom">
                  <span>
                    {attachedRecord
                      ? t(
                          "Includes only your selected observations. Private notes stay on this page.",
                        )
                      : attachedContext
                        ? t(
                            "Uses the attached forecast. Crop examples stay separate.",
                          )
                        : inferenceAvailable
                          ? t(
                              "No forecast attached. Uses the synthetic crop example; your question is sent to the connected service. Model wording is used only when available.",
                            )
                          : t(
                              "No forecast attached. Uses the synthetic crop example; answers use field rules in this workspace.",
                            )}
                  </span>
                  {agentBusy ? (
                    <button
                      className="qs-ask"
                      type="button"
                      onClick={() => {
                        agentController.current?.abort();
                        setAgentBusy(false);
                        setAgentError(
                          t(
                            "The request was cancelled. Your scenario is unchanged.",
                          ),
                        );
                      }}
                    >
                      <X size={15} /> {t("Cancel")}
                    </button>
                  ) : (
                    <button
                      className="qs-ask"
                      data-testid="agent-submit"
                      type="submit"
                      disabled={!canAsk || !query.trim()}
                    >
                      <Send size={15} />{" "}
                      {attachedContext ? t("Build field brief") : t("Ask")}
                    </button>
                  )}
                </div>
              </form>
              {agentError && (
                <div className="qs-inline-error" role="alert">
                  <p>{t(agentError)}</p>
                  {agentRecovery && (
                    <button
                      type="button"
                      className="qs-ask"
                      data-testid="agent-reconnect"
                      onClick={reconnectSession}
                    >
                      <RefreshCw size={14} /> {t("Reconnect session")}
                    </button>
                  )}
                </div>
              )}
              {providerFallback && (
                <p
                  className="qs-inline-error"
                  data-testid="agent-fallback-notice"
                  role="status"
                >
                  {t(fallbackStatus)}
                  {t(". Your source-linked checks remain available below.")}
                </p>
              )}
              {agentBusy && (
                <p className="qs-agent-progress" role="status">
                  <LoaderCircle size={14} className="qs-spin" />
                  {t("Checking the scenario, comparison and evidence…")}
                </p>
              )}
              {agentResult && (
                <div
                  className="qs-agent-result"
                  data-testid="agent-result"
                  role="status"
                >
                  <span className="qs-result-kicker">
                    {agentResult.run.inference_mode === "nebius_live"
                      ? t("Live model response")
                      : agentResult.run.inference_mode === "provider_stub"
                        ? t("Test composition")
                        : agentResult.report.field_brief
                          ? t("Field checks from your forecast")
                          : t("Guided comparison checks")}
                  </span>
                  {agentResult.report.synthesis && (
                    <EvidenceSynthesis
                      synthesis={agentResult.report.synthesis}
                      onReview={reviewField}
                    />
                  )}
                  {agentResult.report.field_brief && (
                    <FieldBrief
                      key={
                        agentResult.report.field_brief.context_ref +
                        agentResult.report.field_brief.created_at
                      }
                      brief={agentResult.report.field_brief}
                      onReview={reviewField}
                    />
                  )}
                  <>
                    {agentResult.report.field_brief ? (
                      <details className="qs-comparison-explanation">
                        <summary>
                          {t("About the separate crop example")}
                        </summary>
                        <p>{agentResult.report.explanation}</p>
                      </details>
                    ) : (
                      <details className="qs-comparison-explanation">
                        <summary>
                          {t("How this comparison was prepared")}
                        </summary>
                        <p>{agentResult.report.explanation}</p>
                      </details>
                    )}
                  </>
                  {agentResult.run.trace.length > 0 && (
                    <div
                      className="qs-tool-checks"
                      id="qs-tool-checks"
                      data-testid="tool-checks"
                    >
                      <h3 id="qs-tool-checks-title" tabIndex={-1}>
                        {t("How this answer was checked")}
                      </h3>
                      <ol>
                        {agentResult.run.trace.map((step, i) => (
                          <li key={`${step.tool}-${i}`}>
                            <span className="qs-tool-number">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            {toolLabels[step.tool] ? (
                              <button
                                type="button"
                                className="qs-tool-step-link"
                                data-testid="tool-step-link"
                                onClick={() => openTool(step.tool)}
                              >
                                <span>{t(toolLabels[step.tool])}</span>
                                <ChevronRight size={14} />
                              </button>
                            ) : (
                              <strong>
                                {t("Review the returned tool result")}
                              </strong>
                            )}
                            <span className="qs-tool-status">
                              {step.status === "executed" ? (
                                <>
                                  <Check size={14} />
                                  {t("Checked")}
                                </>
                              ) : (
                                (step.status ?? t("Status not provided"))
                              )}
                            </span>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </div>
              )}
              {proposal && (
                <div className="qs-proposal">
                  <strong>{t("A different scenario was proposed.")}</strong>
                  <p>
                    {t("Your current comparison has not changed. Review")}{" "}
                    {t(PROFILE_LABELS[proposal.profile_id])},{" "}
                    {proposal.water_percent}
                    {t("% water, after")}{" "}
                    {t(CROP_LABELS[proposal.previous_crop]).toLowerCase()}.
                  </p>
                  <button onClick={() => changeScenario(proposal)}>
                    {t("Use these inputs, then compare")}{" "}
                    <ArrowRight size={14} />
                  </button>
                </div>
              )}
            </section>
          )}

          <LandCareRecord
            draft={guidedDraft}
            context={attachedContext}
            agentEnabled={mode === "service" && Boolean(bootstrap)}
            attachedRecord={attachedRecord}
            onAttachRecord={attachRecord}
          />

          <details
            className="qs-prepared-examples"
            data-testid="prepared-comparison-examples"
          >
            <summary>
              {t("Explore the crop example.")}{" "}
              <ChevronRight size={16} aria-hidden="true" />
            </summary>
            <div className="qs-example-intro">
              <div>
                <span className="qs-index">
                  {t("Understand the trade-offs")}
                </span>
                <h2>{t("Explore the crop example.")}</h2>
              </div>
              <p>
                {t(
                  "Work through water availability and crop history in a sample scenario. See which constraint changes the options.",
                )}
              </p>
            </div>

            <div className="qs-workspace">
              <aside className="qs-scenario-rail">
                <section
                  id="qs-scenario"
                  className="qs-scenario"
                  aria-labelledby="qs-scenario-title"
                >
                  <div className="qs-section-heading">
                    <span className="qs-index">{t("Example inputs")}</span>
                    <span className="qs-example-tag">{t("Sample data")}</span>
                  </div>
                  <h2 id="qs-scenario-title">{t("Change the assumptions.")}</h2>
                  <form onSubmit={compare}>
                    <label className="qs-label" htmlFor="qs-profile">
                      {t("Choose an example profile")}
                    </label>
                    <select
                      id="qs-profile"
                      data-testid="profile-select"
                      value={scenario.profile_id}
                      disabled={!bootstrap || connecting}
                      onChange={(event) =>
                        changeScenario({
                          profile_id: event.target
                            .value as Scenario["profile_id"],
                        })
                      }
                    >
                      {(bootstrap?.profiles ?? offlineBootstrap().profiles).map(
                        (value) => (
                          <option key={value.id} value={value.id}>
                            {t(PROFILE_LABELS[value.id])}
                          </option>
                        ),
                      )}
                    </select>
                    <p className="qs-input-hint">
                      {t(profileHints[scenario.profile_id])}
                    </p>

                    <div className="qs-label-line">
                      <label className="qs-label" htmlFor="qs-water">
                        {t("Water in this example")}
                      </label>
                      <output htmlFor="qs-water">
                        {scenario.water_percent}%
                      </output>
                    </div>
                    <input
                      id="qs-water"
                      data-testid="water-input"
                      type="range"
                      min="40"
                      max="120"
                      step="10"
                      value={scenario.water_percent}
                      disabled={!bootstrap || connecting}
                      onChange={(event) =>
                        changeScenario({
                          water_percent: Number(event.target.value),
                        })
                      }
                      aria-valuetext={`${scenario.water_percent} ${t("percent of the profile's example water")}`}
                    />
                    <div className="qs-range-ends">
                      <span>{t("40% · constrained")}</span>
                      <span>120%</span>
                    </div>
                    <div
                      className="qs-water-presets"
                      aria-label={t("Water example presets")}
                    >
                      {[40, 70, 100, 120].map((value) => (
                        <button
                          key={value}
                          type="button"
                          aria-pressed={scenario.water_percent === value}
                          disabled={!bootstrap || connecting}
                          onClick={() =>
                            changeScenario({ water_percent: value })
                          }
                        >
                          {value}%
                        </button>
                      ))}
                    </div>

                    <label className="qs-label" htmlFor="qs-history">
                      {t("What grew before?")}
                    </label>
                    <select
                      id="qs-history"
                      data-testid="history-select"
                      value={scenario.previous_crop}
                      disabled={!bootstrap || connecting}
                      onChange={(event) =>
                        changeScenario({
                          previous_crop: event.target
                            .value as Scenario["previous_crop"],
                        })
                      }
                    >
                      {Object.entries(CROP_LABELS).map(([id, label]) => (
                        <option key={id} value={id}>
                          {t(label)}
                        </option>
                      ))}
                    </select>
                    <p className="qs-input-hint">
                      {t("We check whether the first crop group repeats.")}
                    </p>

                    <button
                      className="qs-primary"
                      data-testid="compare-button"
                      type="submit"
                      disabled={!bootstrap || busy || connecting}
                    >
                      {busy || connecting ? (
                        <LoaderCircle size={16} className="qs-spin" />
                      ) : (
                        <ArrowRight size={16} />
                      )}{" "}
                      {busy
                        ? t("Comparing…")
                        : connecting
                          ? t("Connecting…")
                          : t("Compare crop paths")}
                    </button>
                  </form>
                  <details className="qs-assumptions">
                    <summary>
                      <CircleHelp size={14} /> {t("What is assumed?")}
                    </summary>
                    <p>
                      {t(
                        "Water, soil, priorities and crop demands come from a fixed synthetic example. They are not field measurements or locally validated recommendations.",
                      )}
                    </p>
                    <p>
                      {t(
                        "Changing the controls explores the example without saving a real parcel.",
                      )}
                    </p>
                  </details>
                </section>
                <div className="qs-rail-foot">
                  <ShieldCheck size={17} />
                  <p>
                    {t("Your local knowledge.")}
                    <br />
                    <strong>{t("Your next informed step.")}</strong>
                  </p>
                </div>
              </aside>

              <div className="qs-decision-space">
                {error && (
                  <section className="qs-error" role="alert">
                    <strong>{t("The comparison did not run.")}</strong>
                    <p>{t(error)}</p>
                    <div>
                      <button onClick={reconnectSession}>
                        <RefreshCw size={14} /> {t("Reconnect service")}
                      </button>
                      {!STATIC_STUDIO && mode !== "offline" && (
                        <button onClick={() => setMode("offline")}>
                          {t("Explore a saved example instead")}{" "}
                          <ArrowRight size={14} />
                        </button>
                      )}
                    </div>
                  </section>
                )}

                <section
                  className="qs-comparison"
                  id="qs-comparison"
                  ref={comparisonRef}
                  aria-labelledby="qs-comparison-title"
                  aria-busy={busy || connecting}
                >
                  <div className="qs-section-heading">
                    <span className="qs-index">{t("Example crop paths")}</span>
                    <span className="qs-technical-label">
                      {mode === "offline"
                        ? t("Prepared example results")
                        : t("Calculated from your inputs")}
                    </span>
                  </div>
                  <div className="qs-comparison-heading">
                    <div>
                      <h2 id="qs-comparison-title" tabIndex={-1}>
                        {t("Three paths.")}
                        <br className="qs-mobile-break" />{" "}
                        {t("Visible trade-offs.")}
                      </h2>
                      <p>
                        {t(
                          "See what fits the water and crop-history rules—and what needs a closer look.",
                        )}
                      </p>
                    </div>
                    {report && (
                      <div className="qs-fit" data-testid="fit-count">
                        <strong>
                          {count}
                          <span> / {report.plans.length}</span>
                        </strong>
                        <span>{t("fit this example")}</span>
                      </div>
                    )}
                  </div>

                  {report ? (
                    <>
                      <div className="qs-scenario-strip">
                        <span>
                          <Droplets size={16} />
                          <strong>{number(available)}</strong> m³/ha{" "}
                          <small>{t("example water")}</small>
                        </span>
                        <span>
                          <Sprout size={15} />
                          {t(CROP_LABELS[scenario.previous_crop])}{" "}
                          <small>{t("previous group")}</small>
                        </span>
                        <span>{t(PROFILE_LABELS[scenario.profile_id])}</span>
                      </div>
                      {report.status === "ok" && report.plans.length ? (
                        <div className="qs-plans">
                          {report.plans.map((plan) => (
                            <PlanRow
                              key={plan.id}
                              plan={plan}
                              available={available}
                            />
                          ))}
                        </div>
                      ) : (
                        <div className="qs-empty">
                          <FileCheck2 size={23} />
                          <h3>{t("More information is needed.")}</h3>
                          <p>
                            {t(
                              "The engine did not return comparable alternatives. No plan is being recommended.",
                            )}
                          </p>
                        </div>
                      )}
                      {report.status === "ok" && count === 0 && (
                        <p className="qs-no-match" role="status">
                          <span />
                          {t(
                            "No plan matches both rules. Try a different water or crop-history input to see what changes.",
                          )}
                        </p>
                      )}
                      <p className="qs-chart-note">
                        <span className="qs-water-key" />{" "}
                        {t(
                          "m³/ha = cubic metres of water per hectare. Line = example water. Bar = assumed demand.",
                        )}
                      </p>
                    </>
                  ) : (
                    <div className="qs-empty" role="status">
                      {connecting || busy ? (
                        <LoaderCircle size={25} className="qs-spin" />
                      ) : (
                        <Layers3 size={25} />
                      )}
                      <h3>
                        {connecting
                          ? t("Preparing your workspace.")
                          : busy
                            ? t("Comparing this scenario.")
                            : t("Your scenario is ready to explore.")}
                      </h3>
                      <p>
                        {connecting
                          ? t(
                              "The Studio is checking its local session and engine.",
                            )
                          : t(
                              "Use “Compare crop paths” to see the current inputs. Previous results are never shown as a new comparison.",
                            )}
                      </p>
                    </div>
                  )}
                </section>
              </div>
            </div>
          </details>
        </section>
        <section
          id="qs-evidence-area"
          role="tabpanel"
          aria-labelledby="qs-area-tab-evidence"
          tabIndex={0}
          data-testid="area-evidence"
          data-beforematch-area="evidence"
          ref={evidencePanelRef}
        >
          <section
            className="qs-connections"
            id="qs-connections"
            aria-labelledby="qs-connections-title"
            data-testid="connection-panel"
          >
            <div className="qs-connections-heading">
              <h2 id="qs-connections-title">
                {t("Your evidence, working together")}
              </h2>
              <span>{t("Sources, checks and reasoning")}</span>
            </div>

            <div className="qs-connection-line">
              <div
                className="qs-connection-node qs-weather-node"
                data-testid="connection-weather"
                data-state={
                  attachedContext
                    ? "attached"
                    : mode === "offline"
                      ? "sample"
                      : bootstrap?.geodata?.enabled
                        ? "available"
                        : "disabled"
                }
              >
                <span className="qs-node-icon">
                  <MapPin size={17} />
                </span>
                <div>
                  <h3>{t("Landscape & weather")}</h3>
                  <p className="qs-node-status">
                    {attachedContext
                      ? t("Seven-day forecast attached")
                      : mode === "offline"
                        ? t("Regional landscape explorer")
                        : bootstrap?.geodata?.enabled
                          ? t("Choose a place to load a forecast")
                          : t("Landscape exploration")}
                  </p>
                  <button type="button" onClick={() => reviewField("weather")}>
                    {attachedContext
                      ? t("Review Open-Meteo context")
                      : t("Explore your place")}{" "}
                    <ChevronRight size={13} />
                  </button>
                </div>
              </div>

              <div
                className="qs-connection-node"
                data-testid="connection-engine"
                data-state={
                  connecting
                    ? "connecting"
                    : busy
                      ? "running"
                      : error
                        ? "error"
                        : report
                          ? mode === "offline"
                            ? "saved"
                            : "linked"
                          : bootstrap
                            ? "waiting"
                            : "disconnected"
                }
              >
                <span className="qs-node-icon">
                  <Layers3 size={17} />
                </span>
                <div>
                  <h3>
                    {mode === "offline"
                      ? t("Saved engine report")
                      : t("Comparison API")}
                  </h3>
                  <p className="qs-node-status">
                    {connecting
                      ? t("Connecting…")
                      : busy
                        ? t("Comparing your inputs…")
                        : error
                          ? t("Comparison needs retry")
                          : report
                            ? mode === "offline"
                              ? t("Example snapshot loaded")
                              : t("Current comparison linked")
                            : bootstrap
                              ? t("Ready to compare")
                              : t("Local workspace")}
                  </p>
                  <button
                    type="button"
                    disabled={!report}
                    onClick={openComparison}
                  >
                    {t("View crop paths")} <ChevronRight size={13} />
                  </button>
                </div>
              </div>

              <div
                className="qs-connection-node"
                data-testid="connection-tools"
                data-state={
                  mode === "offline"
                    ? "offline"
                    : agentBusy
                      ? "running"
                      : agentError
                        ? "error"
                        : returnedTools.length
                          ? "returned"
                          : "idle"
                }
              >
                <span className="qs-node-icon">
                  <FileCheck2 size={17} />
                </span>
                <div>
                  <h3>{t("Source & rule checks")}</h3>
                  <p className="qs-node-status">
                    {mode === "offline"
                      ? t("Water and rotation rules")
                      : agentBusy
                        ? t("Running local checks…")
                        : agentError
                          ? t("No completed response")
                          : returnedTools.length
                            ? `${returnedTools.length} tool results returned`
                            : canAsk
                              ? t("Ready when you ask")
                              : t("Waiting for a comparison")}
                  </p>
                  {mode === "offline" ? (
                    <span className="qs-node-caption">
                      {t("Explore the prepared crop paths")}
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={!canAsk || agentBusy}
                      onClick={() => {
                        if (returnedTools.length) {
                          scrollToSection(
                            document.getElementById("qs-tool-checks"),
                          );
                          document
                            .getElementById("qs-tool-checks-title")
                            ?.focus({ preventScroll: true });
                        } else
                          usePrompt(
                            t(
                              "What should I check before using this comparison?",
                            ),
                          );
                      }}
                    >
                      {returnedTools.length
                        ? t("Inspect returned checks")
                        : t("Ask about this example")}{" "}
                      <ChevronRight size={13} />
                    </button>
                  )}
                </div>
              </div>

              <div
                className="qs-connection-node qs-model-node"
                data-testid="connection-ai"
                data-state={
                  mode === "offline"
                    ? "offline"
                    : liveResponse
                      ? "live"
                      : inferenceMode === "provider_stub"
                        ? "stub"
                        : providerFallback
                          ? "unavailable"
                          : inferenceAvailable
                            ? "available"
                            : "disconnected"
                }
              >
                <span className="qs-node-icon">
                  <BrandMark className="qs-model-glyph" />
                </span>
                <div>
                  <h3>
                    {mode === "offline" ? t("Workspace mode") : t("Reasoning")}
                  </h3>
                  <p className="qs-node-status">
                    {mode === "offline" ? t("Sample data") : t(aiStatus)}
                  </p>
                  <span className="qs-node-caption" title={model ?? undefined}>
                    {modelLabel ??
                      (mode === "offline"
                        ? t("Prepared examples and field records")
                        : t("Calculation-backed guidance"))}
                    {providerLabel && mode === "service" && (
                      <>
                        <br />
                        {inferenceAvailable || liveResponse
                          ? providerLabel
                          : `${providerLabel} · not enabled in this demo`}
                      </>
                    )}
                  </span>
                </div>
              </div>
            </div>

            {report && (
              <details className="qs-report-receipt">
                <summary>
                  {t("Inspect the comparison receipt")}{" "}
                  <ChevronRight size={13} />
                </summary>
                <dl>
                  <div>
                    <dt>{t("Comparison")}</dt>
                    <dd>{report.comparison_id}</dd>
                  </div>
                  <div>
                    <dt>{t("Scenario revision")}</dt>
                    <dd>{report.scenario_revision}</dd>
                  </div>
                  <div>
                    <dt>{t("Rule version")}</dt>
                    <dd>{report.rule_version}</dd>
                  </div>
                  {provider && (
                    <div>
                      <dt>{t("Provider identifier")}</dt>
                      <dd>{provider}</dd>
                    </div>
                  )}
                  {model && (
                    <div>
                      <dt>{t("Model identifier")}</dt>
                      <dd>{model}</dd>
                    </div>
                  )}
                </dl>
              </details>
            )}
          </section>

          <section
            className="qs-review"
            id="qs-review"
            ref={reviewRef}
            aria-labelledby="qs-review-title"
          >
            <div className="qs-section-heading">
              <span className="qs-index">{t("Local knowledge matters")}</span>
              <span className="qs-example-tag">{t("Better together")}</span>
            </div>
            <h2 id="qs-review-title">
              {t("Bring local knowledge into the picture.")}
            </h2>
            <div
              className="qs-review-tabs"
              role="tablist"
              aria-label={t("Evidence and decision review")}
            >
              {(
                [
                  { id: "soil", label: t("Soil & cover"), icon: Leaf },
                  { id: "evidence", label: t("Evidence"), icon: Layers3 },
                  { id: "decision", label: t("Next checks"), icon: FileCheck2 },
                ] as const
              ).map(({ id, label, icon: Icon }) => (
                <button
                  id={`qs-tab-${id}`}
                  key={id}
                  role="tab"
                  aria-selected={tab === id}
                  aria-controls={`qs-panel-${id}`}
                  tabIndex={tab === id ? 0 : -1}
                  onKeyDown={(event) => {
                    if (
                      event.key === "ArrowRight" ||
                      event.key === "ArrowLeft"
                    ) {
                      event.preventDefault();
                      const ids: StudioView[] = [
                        "soil",
                        "evidence",
                        "decision",
                      ];
                      const next =
                        ids[
                          (ids.indexOf(tab) +
                            (event.key === "ArrowRight" ? 1 : 2)) %
                            3
                        ];
                      setTab(next);
                      document.getElementById(`qs-tab-${next}`)?.focus();
                    }
                  }}
                  onClick={() => setTab(id)}
                >
                  <Icon size={15} />
                  {t(label)}
                </button>
              ))}
            </div>

            {tab === "soil" && (
              <div
                id="qs-panel-soil"
                role="tabpanel"
                aria-labelledby="qs-tab-soil"
                tabIndex={0}
                data-testid="land-panel"
                className="qs-review-panel"
              >
                <span className="qs-observation-label">
                  {t("Questions for your land")}
                </span>
                <h3>{t("You know more than a water balance.")}</h3>
                <p>
                  {t(
                    "Bring what you notice about soil, vegetation and water paths into a shared review. Your field record keeps those observations in your own words.",
                  )}
                </p>
                <ul>
                  {(
                    displayedReport?.resilience.open_questions ?? [
                      t("What soil cover was observed, and when?"),
                      t("Are signs of erosion documented locally?"),
                      t(
                        "Which soil and land-management information has a source?",
                      ),
                    ]
                  ).map((question) => (
                    <li key={question}>
                      <span className="qs-open-circle" />
                      {t(question)}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {tab === "evidence" && (
              <div
                id="qs-panel-evidence"
                role="tabpanel"
                aria-labelledby="qs-tab-evidence"
                tabIndex={0}
                data-testid="evidence-panel"
                className="qs-review-panel"
              >
                <span className="qs-observation-label">
                  {t("Behind the comparison")}
                </span>
                <h3>{t("Know what the answer is built on.")}</h3>
                <div className="qs-source-list">
                  {displayedReport?.evidence.sources.map((source) => (
                    <div key={source.id}>
                      <span>
                        <Layers3 size={15} />
                        {t(sourceLabels[source.id] ?? source.id)}
                      </span>
                      <strong>{t("Synthetic")}</strong>
                    </div>
                  )) ?? <p>{t("Run a comparison to see its sources.")}</p>}
                </div>
                <h4>{t("Not covered by this example")}</h4>
                <ul>
                  {(
                    displayedReport?.evidence.not_available ?? [
                      t("Verified field weather"),
                      t("Soil and land-cover observations"),
                      t("Agronomic validation"),
                    ]
                  ).map((gap) => (
                    <li key={gap}>
                      <span className="qs-open-circle" />
                      {t(gap)}
                    </li>
                  ))}
                </ul>
                <p className="qs-small-note">
                  {t(
                    "Missing coverage is not evidence that degradation is absent. Sources do not automatically support every claim.",
                  )}
                </p>
                <details className="qs-method">
                  <summary>
                    {t("Where this example comes from")}{" "}
                    <ChevronRight size={15} />
                  </summary>
                  <p>
                    {t(
                      "These comparison profiles are hypothetical teaching inputs, not measured conditions for your selected location. Location-based environmental evidence is kept separate; land degradation is not diagnosed and environmental impact has not been measured.",
                    )}
                  </p>
                </details>
              </div>
            )}

            {tab === "decision" && (
              <div
                id="qs-panel-decision"
                role="tabpanel"
                aria-labelledby="qs-tab-decision"
                tabIndex={0}
                data-testid="decision-panel"
                className="qs-review-panel"
              >
                <span className="qs-observation-label">
                  {t("Your next conversation")}
                </span>
                <h3>{t("Make the next conversation count.")}</h3>
                <ol className="qs-next-checks">
                  {(
                    displayedReport?.decision.pending ?? [
                      t("Compare a scenario."),
                      t("Check assumptions against local evidence."),
                      t("Review the alternatives with agronomic support."),
                    ]
                  ).map((item, index) => (
                    <li key={item}>
                      <span>{String(index + 1).padStart(2, "0")}</span>
                      {t(item)}
                    </li>
                  ))}
                </ol>
                <p className="qs-small-note">
                  {t(
                    "Take your questions to a local adviser or grower group, and agree on one check for your next visit.",
                  )}
                </p>
              </div>
            )}
          </section>
        </section>

        <section
          hidden={area !== "territory"}
          className="qs-about"
          id="qs-about"
          aria-labelledby="qs-about-title"
        >
          <div className="qs-about-symbol" aria-hidden="true">
            <BrandMark />
          </div>
          <div>
            <span className="qs-index">{t("Why we build Solquira")}</span>
            <h2 id="qs-about-title">
              {t("Understand the land.")}
              <br />
              {t("Cultivate the future.")}
            </h2>
            <p>
              {t(
                "Born in Latin America and built for drylands everywhere, Solquira brings landscape evidence and local knowledge together, place by place. It supports growers, advisers and communities facing water stress, a changing climate and desertification as they choose a practical next step for the land they care for.",
              )}
            </p>
          </div>
          <a href="#qs-landscape">
            {t("Start with a place")} <MapPin size={17} />
          </a>
        </section>

        <footer className="qs-footer">
          <div className="qs-footer-identity">
            <BrandMark className="qs-footer-symbol" />
            <span className="qs-footer-brand">
              Solquira{" "}
              <small>{t("Understand the land. Cultivate the future.")}</small>
            </span>
            <p className="qs-footer-tagline">
              {t("Built for drylands, from Latin America.")}
            </p>
          </div>

          <nav className="qs-footer-nav" aria-label={t("Product and methods")}>
            <a href="#qs-about">{t("Our purpose")}</a>
            <button type="button" onClick={() => openReview("evidence")}>
              {t("Explore evidence")}
            </button>
            <details
              className="qs-footer-methods"
              data-testid="methods-version"
            >
              <summary>
                {t("Methods & sources")} <ChevronRight size={14} />
              </summary>
              <div className="qs-footer-method-panel">
                <p className="qs-workspace-basis" data-testid="workspace-basis">
                  {mode === "offline"
                    ? t(
                        "Sample workspace: crop results are precomputed from synthetic example inputs. The regional map and field records stay on this device.",
                      )
                    : liveResponse
                      ? t(
                          "Connected workspace: this answer includes a response from the named model and the tool results shown in its receipt.",
                        )
                      : t(
                          "Connected workspace: comparison and field checks are available. The current model status is shown in the connection receipt.",
                        )}
                </p>
                <h3>
                  {t("Comparison method")}
                  {methodVersion ? ` ${methodVersion}` : ""}
                </h3>
                <p>
                  {t(
                    "Crop examples use fixed water-demand and rotation assumptions; they are not measured farms or validated agronomic recommendations. A matching path does not assess field suitability or environmental impact. The same calculation engine powers comparisons and guided checks.",
                  )}
                </p>
                <dl>
                  <div>
                    <dt>{t("Composition basis")}</dt>
                    <dd>
                      {mode === "offline"
                        ? t(
                            "Precomputed example results, composed by field rules.",
                          )
                        : model
                          ? `${modelLabel} · ${t(aiStatus)}`
                          : t(
                              "Field rules compose the wording; no model provider is configured.",
                            )}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("Rule identifier")}</dt>
                    <dd>
                      {ruleVersion ?? t("Shown when the Studio is ready.")}
                    </dd>
                  </div>
                </dl>
                <button type="button" onClick={() => openReview("evidence")}>
                  {t("Review sources & assumptions")} <ChevronRight size={13} />
                </button>
              </div>
            </details>
            {!STATIC_STUDIO && mode === "offline" && (
              <button type="button" onClick={() => setMode("service")}>
                <RefreshCw size={13} /> {t("Reconnect Studio")}
              </button>
            )}
          </nav>
        </footer>
      </main>
    </div>
  );
}
