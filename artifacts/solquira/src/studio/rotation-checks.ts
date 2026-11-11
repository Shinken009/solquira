export interface RotationInputs {
  planned: string;
  start: string;
  end: string;
  water: "" | "rain" | "turn" | "uncertain";
  flowLpm: string;
  minutes: string;
  requiredM3: string;
  sameBasis: boolean;
  drainage: "" | "required" | "notSpecified";
  ponding: "" | "seen" | "notSeen";
}

export const emptyRotationInputs: RotationInputs = {
  planned: "", start: "", end: "", water: "", flowLpm: "", minutes: "",
  requiredM3: "", sameBasis: false, drainage: "", ponding: "",
};

type Check = "window" | "history" | "water" | "soil";
type Status = "attention" | "check" | "reference";

function dateOk(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function decimal(value: string) {
  if (!/^\d{1,9}(?:[.,]\d{1,6})?$/.test(value.trim())) return null;
  return Number(value.trim().replace(",", "."));
}

export function evaluateRotation(input: RotationInputs, previousFamily?: string, candidateFamily?: string) {
  const { planned, start, end } = input;
  const window = [planned, start, end].some(date => date && !dateOk(date)) || (start && end && start > end)
    ? "invalid" : !start || !end ? "unknown" : !planned ? "needDate" : planned < start || planned > end ? "outside" : "inside";
  const history = !previousFamily || !candidateFamily ? "unknown" : previousFamily === candidateFamily ? "repeat" : "different";
  const flow = decimal(input.flowLpm);
  const minutes = decimal(input.minutes);
  const required = decimal(input.requiredM3);
  // Estimate delivered supply only. No crop demand, effective rain or irrigation efficiency is inferred.
  const volume = input.water === "turn" && flow !== null && minutes !== null ? flow * (minutes / 1000) : null;
  const water = input.water !== "turn" ? input.water || "unknown"
    : volume === null ? "estimate"
    : !input.sameBasis || required === null || required <= 0 ? "volume"
    : volume < required && required - volume > Number.EPSILON * Math.max(required, volume) * 4 ? "below" : "meets";
  const soil = input.drainage === "required"
    ? input.ponding === "seen" ? "ponding" : input.ponding === "notSeen" ? "notSeen" : "observe"
    : input.ponding === "seen" ? "matchGuide" : "requirement";
  const statuses: Record<Check, Status> = {
    window: window === "outside" || window === "invalid" ? "attention" : window === "inside" ? "reference" : "check",
    history: history === "repeat" ? "attention" : history === "different" ? "reference" : "check",
    water: water === "below" ? "attention" : water === "meets" ? "reference" : "check",
    soil: soil === "ponding" ? "attention" : soil === "notSeen" ? "reference" : "check",
  };
  const order: Check[] = ["window", "water", "history", "soil"];
  const collect = !planned && !start && !end ? (["history", ...order.filter(key => key !== "history")] as Check[]) : order;
  const next = order.find(key => statuses[key] === "attention") ?? collect.find(key => statuses[key] === "check") ?? "window";
  return { window, history, water, soil, volume, statuses, next };
}
