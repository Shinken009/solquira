import fixtures from './fixtures.json';
import { parseFieldBrief } from './field-brief-client';
import { parseSynthesis, sameFieldRecord } from './synthesis-client';
import type { FieldRecord } from './types';
import { CROP_LABELS, PROFILE_LABELS, sameScenario, type Bootstrap, type ComparisonReport, type CropId, type Envelope, type Plan, type ProfileId, type Scenario } from './types';

const rawBase = (import.meta.env.VITE_API_URL || '/solquira-api').replace(/\/$/, '');
const BASE = `${rawBase}/studio`;
export const STATIC_STUDIO = import.meta.env.VITE_DEMO_MODE === 'true';

export class StudioRequestError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'StudioRequestError';
  }
}

const locations: Record<ProfileId, string> = { valle: 'Elqui Valley · Vicuña', secano: 'Limarí · Punitaqui', precordillera: 'Choapa · Salamanca' };
type FixtureRow = { id: string; crops: string[]; demandM3Ha: number; waterBalanceM3Ha: number; rotationAllowed: boolean; eligibleUnderDemoRules: boolean; score: number };
type FixtureResult = { availableWaterM3Ha: number; ruleVersion: string; status: string; rotations: FixtureRow[] };
const historyRows = fixtures.historyEvaluations as unknown as Record<string, FixtureResult>;

export function offlineBootstrap(): Bootstrap {
  return {
    schema_version: 'solquira.studio/1', csrf_token: '', rule_version: fixtures.ruleVersion, synthetic: true,
    profiles: fixtures.profiles.map(p => ({ id: p.id as ProfileId, name: PROFILE_LABELS[p.id as ProfileId], location: locations[p.id as ProfileId], water_m3_ha: p.waterM3Ha })),
    history_options: Object.entries(CROP_LABELS).map(([id, label]) => ({ id: id as CropId, label })),
    inference: { available: false, mode: 'disabled', provider: 'nebius', model: null },
  };
}

function translatedCrop(value: string): string {
  const id = value.replace(/ \(supuesto\)/g, '').toLowerCase();
  return CROP_LABELS[id as CropId] ?? 'Unknown example category';
}

export function offlineEvaluate(scenario: Scenario): Envelope {
  const row = historyRows[`${scenario.profile_id}:${scenario.water_percent}:${scenario.previous_crop}`];
  if (!row) throw new Error('This combination is outside the saved example.');
  const plans: Plan[] = row.rotations.map(p => ({
    id: p.id, name: p.crops.map(translatedCrop).join(' → '), sequence: p.crops.map(translatedCrop),
    demand_m3_ha: p.demandM3Ha, balance_m3_ha: p.waterBalanceM3Ha, history_ok: p.rotationAllowed,
    eligible: p.eligibleUnderDemoRules, score: p.score,
  }));
  const id = `offline:${scenario.profile_id}:${scenario.water_percent}:${scenario.previous_crop}`;
  const count = plans.filter(p => p.eligible).length;
  const report: ComparisonReport = {
    comparison_id: id, scenario_revision: `${fixtures.engineSha256}:${id}`, scenario: { ...scenario }, rule_version: row.ruleVersion,
    engine_sha256: fixtures.engineSha256, scope: 'demo_coquimbo', synthetic: true, agronomicViability: 'not_assessed',
    status: row.status, available_water_m3_ha: row.availableWaterM3Ha, plans,
    explanation: `At ${row.availableWaterM3Ha.toLocaleString('en-US')} m³/ha of example water, ${count} of ${plans.length} plans match the synthetic rules. Water and previous-crop restrictions are independent. Agronomic viability has not been assessed.`,
    evidence: { sources: [{ id: 'parcel-input', kind: 'synthetic_example' }, { id: 'demo-rules', kind: 'synthetic', rule_version: row.ruleVersion }], not_available: ['Verified field weather', 'Observed soil and land cover', 'Validated field images or sensor readings', 'Agronomic validation'] },
    resilience: { assessment_status: 'not_assessed', environmental_impact: 'not_measured', observations: [], open_questions: ['What soil cover was observed, and when?', 'Are signs of erosion documented by local observation?', 'Which soil and land-management information has a verifiable source?'], note: 'A synthetic scenario does not measure land degradation or demonstrate the absence of desertification.' },
    decision: { decision_status: 'human_review_required', fits_demo_rules: plans.filter(p => p.eligible).map(p => p.id), pending: ['Check water and soil assumptions against local evidence.', 'Review the alternatives with local agronomic support.'], external_actions_executed: false },
  };
  return { schema_version: 'solquira.studio/1', scenario: { ...scenario }, report, run: { inference_mode: 'offline_example', outcome: 'disabled', model: null, trace: [] }, request_id: id };
}

function isObject(value: unknown): value is Record<string, unknown> { return Boolean(value) && typeof value === 'object' && !Array.isArray(value); }
function finiteNumber(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value); }

function nonempty(value: unknown, max = 1000): value is string { return typeof value === 'string' && value.trim().length > 0 && value.length <= max; }
function strings(value: unknown, max = 40): value is string[] { return Array.isArray(value) && value.length <= max && value.every(item => nonempty(item)); }
function parseScenario(value: unknown): Scenario {
  if (!isObject(value) || !['valle', 'secano', 'precordillera'].includes(String(value.profile_id)) || !['cereal', 'leguminosa', 'cubierta', 'descanso'].includes(String(value.previous_crop)) || !Number.isInteger(value.water_percent) || !finiteNumber(value.water_percent) || value.water_percent < 40 || value.water_percent > 120 || value.water_percent % 10 !== 0) throw new Error('The service returned an unsupported scenario.');
  return { profile_id: value.profile_id as ProfileId, water_percent: value.water_percent, previous_crop: value.previous_crop as CropId };
}

function parseEnvelope(value: unknown): Envelope {
  if (!isObject(value) || value.schema_version !== 'solquira.studio/1' || !nonempty(value.request_id, 200) || !isObject(value.report) || !isObject(value.run)) throw new Error('The service returned an incomplete comparison.');
  const scenario = parseScenario(value.scenario);
  const report = value.report;
  if (report.field_brief !== undefined) report.field_brief = parseFieldBrief(report.field_brief);
  if (report.synthesis !== undefined) report.synthesis = parseSynthesis(report.synthesis);
  if (report.synthetic !== true || report.agronomicViability !== 'not_assessed' || !['ok', 'insufficient_data', 'out_of_scope'].includes(String(report.status)) || !nonempty(report.scope, 100) || !nonempty(report.rule_version, 100) || !nonempty(report.explanation, 6000) || !nonempty(report.scenario_revision, 200) || !nonempty(report.comparison_id, 200) || !Array.isArray(report.plans) || report.plans.length > 3) throw new Error('The comparison could not be verified.');
  const ids = new Set<string>();
  for (const plan of report.plans) {
    if (!isObject(plan) || !nonempty(plan.id, 100) || ids.has(plan.id) || !nonempty(plan.name, 300) || !strings(plan.sequence, 10) || plan.sequence.length === 0 || !finiteNumber(plan.demand_m3_ha) || plan.demand_m3_ha < 0 || !finiteNumber(plan.balance_m3_ha) || typeof plan.eligible !== 'boolean' || typeof plan.history_ok !== 'boolean' || (plan.score !== undefined && !finiteNumber(plan.score))) throw new Error('The service returned an invalid plan.');
    ids.add(plan.id);
  }
  if (report.available_water_m3_ha !== null && (!finiteNumber(report.available_water_m3_ha) || report.available_water_m3_ha < 0)) throw new Error('The water quantity could not be verified.');
  if (report.missing_fields !== undefined && !strings(report.missing_fields)) throw new Error('The missing-input information could not be verified.');
  if (!isObject(report.evidence) || !Array.isArray(report.evidence.sources) || report.evidence.sources.length > 30 || !strings(report.evidence.not_available)) throw new Error('Evidence information is missing.');
  for (const source of report.evidence.sources) if (!isObject(source) || !nonempty(source.id, 100) || !nonempty(source.kind, 100)) throw new Error('A source could not be verified.');
  if (!isObject(report.resilience) || !strings(report.resilience.open_questions) || !Array.isArray(report.resilience.observations) || report.resilience.assessment_status !== 'not_assessed' || report.resilience.environmental_impact !== 'not_measured' || (report.resilience.note !== undefined && !nonempty(report.resilience.note, 3000))) throw new Error('The land-review information could not be verified.');
  if (!isObject(report.decision) || !strings(report.decision.pending) || !strings(report.decision.fits_demo_rules) || report.decision.decision_status !== 'human_review_required' || report.decision.external_actions_executed !== false) throw new Error('The decision-review information could not be verified.');
  const run = value.run;
  if (!nonempty(run.inference_mode, 100) || !nonempty(run.outcome, 100) || !(run.model === null || nonempty(run.model, 200)) || !Array.isArray(run.trace) || run.trace.length > 20) throw new Error('The inference mode could not be verified.');
  for (const step of run.trace) if (!isObject(step) || !nonempty(step.tool, 100) || (step.status !== undefined && !nonempty(step.status, 100))) throw new Error('The execution trace could not be verified.');
  const reportScenario = report.scenario === undefined ? scenario : parseScenario(report.scenario);
  const requested = value.requested_scenario === undefined ? undefined : parseScenario(value.requested_scenario);
  const proposed = value.proposed_scenario === undefined ? undefined : parseScenario(value.proposed_scenario);
  if (requested && !sameScenario(requested, scenario)) throw new Error('The requested scenario is inconsistent.');
  if (!sameScenario(reportScenario, scenario) && (!proposed || !sameScenario(proposed, reportScenario))) throw new Error('An alternative scenario was returned without an explicit proposal.');
  return { ...(value as unknown as Envelope), scenario, report: { ...(report as unknown as ComparisonReport), scenario: reportScenario }, ...(requested ? { requested_scenario: requested } : {}), ...(proposed ? { proposed_scenario: proposed } : {}) };
}

async function request(path: string, signal: AbortSignal, token?: string, body?: unknown): Promise<unknown> {
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  if (signal.aborted) controller.abort();
  signal.addEventListener('abort', cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 30000);
  try {
    let response: Response;
    try {
      response = await fetch(`${BASE}${path}`, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Solquira-CSRF': token ?? '' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    } catch (error) {
      if (signal.aborted) throw error;
      if (timedOut) throw new Error('The request took too long. Your scenario is unchanged; retry when the service is ready.');
      throw new Error('The Studio service could not be reached. Your scenario has not been replaced with example data.');
    }
    if (!response.ok) {
      const messages: Record<number, string> = { 401: 'Your example session has expired. Reconnect to start a new session.', 403: 'The request was not admitted. Reconnect and try again.', 409: 'The attached forecast expired or changed. Reload local context and attach it again.', 413: 'The request is too large.', 422: 'The scenario contains an unsupported value.', 429: 'The Studio is at its request limit. Please wait before trying again.', 503: 'The Studio is busy. Try again in a moment.' };
      throw new StudioRequestError(messages[response.status] ?? `The Studio could not complete this request (${response.status}).`, response.status);
    }
    try { return await response.json(); } catch (error) {
      if (signal.aborted) throw error;
      if (timedOut) throw new Error('The response took too long. No partial explanation has been accepted.');
      throw new Error('The service returned a response that could not be read.');
    }
  } finally { clearTimeout(timer); signal.removeEventListener('abort', cancel); }
}

export async function bootstrapStudio(signal: AbortSignal): Promise<Bootstrap> {
  const value = await request('/bootstrap', signal);
  if (!isObject(value) || value.schema_version !== 'solquira.studio/1' || !nonempty(value.csrf_token, 256) || !Array.isArray(value.profiles) || value.profiles.length === 0 || value.profiles.length > 3 || !Array.isArray(value.history_options) || value.history_options.length !== 4 || !nonempty(value.rule_version, 100) || value.synthetic !== true || !isObject(value.inference) || typeof value.inference.available !== 'boolean' || !nonempty(value.inference.mode, 100) || !(value.inference.model === undefined || value.inference.model === null || nonempty(value.inference.model, 200))) throw new Error('The Studio session could not be initialized.');
  const profiles = new Set<string>();
  if (value.geodata !== undefined && (!isObject(value.geodata) || typeof value.geodata.enabled !== 'boolean')) throw new Error('The geographic source configuration could not be verified.');
  for (const profile of value.profiles) {
    if (!isObject(profile) || !['valle', 'secano', 'precordillera'].includes(String(profile.id)) || !nonempty(profile.name, 200) || !nonempty(profile.location, 200) || !finiteNumber(profile.water_m3_ha) || profile.water_m3_ha < 0 || profiles.has(String(profile.id))) throw new Error('The example profiles could not be verified.');
    profiles.add(String(profile.id));
  }
  const crops = new Set<string>();
  for (const crop of value.history_options) {
    if (!isObject(crop) || !['cereal', 'leguminosa', 'cubierta', 'descanso'].includes(String(crop.id)) || !nonempty(crop.label, 100) || crops.has(String(crop.id))) throw new Error('The previous-crop options could not be verified.');
    crops.add(String(crop.id));
  }
  return value as unknown as Bootstrap;
}

export async function evaluateStudio(scenario: Scenario, csrf: string, signal: AbortSignal): Promise<Envelope> { return parseEnvelope(await request('/evaluate', signal, csrf, scenario)); }
export async function askStudio(scenario: Scenario, query: string, csrf: string, signal: AbortSignal, contextRef?:string, record?:FieldRecord): Promise<Envelope> {
  const value=parseEnvelope(await request('/agent', signal, csrf, { scenario, query, ...(contextRef?{context_ref:contextRef}:{}), ...(record?{field_record:record}:{}) }));
  if(value.report.field_brief?.context_ref!==contextRef)throw new Error('The response does not match the attached forecast. Reload its context and ask again.');
  if(!sameFieldRecord(value.report.synthesis?.record, record))throw new Error('The response does not match your selected field observations. Review and attach them again.');
  return value;
}
