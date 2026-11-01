export type ProfileId = 'valle' | 'secano' | 'precordillera';
export type CropId = 'cereal' | 'leguminosa' | 'cubierta' | 'descanso';
export type StudioView = 'soil' | 'evidence' | 'decision';

export interface Scenario {
  profile_id: ProfileId;
  water_percent: number;
  previous_crop: CropId;
}

export interface Profile {
  id: ProfileId;
  name: string;
  location: string;
  water_m3_ha: number;
}

export interface Bootstrap {
  geodata?: { enabled: boolean };
  schema_version: string;
  csrf_token: string;
  profiles: Profile[];
  history_options: { id: CropId; label: string }[];
  rule_version: string;
  synthetic: boolean;
  inference: { available: boolean; mode: string; provider?: string; model?: string | null };
}

export interface Plan {
  id: string;
  name: string;
  sequence: string[];
  sequence_ids?: string[];
  demand_m3_ha: number;
  balance_m3_ha: number;
  history_ok: boolean;
  eligible: boolean;
  score?: number;
}

export interface Evidence {
  sources: { id: string; kind: string; rule_version?: string }[];
  not_available: string[];
  agronomicViability?: string;
}

export interface LandReview {
  assessment_status: string;
  environmental_impact: string;
  observations: unknown[];
  open_questions: string[];
  note?: string;
}

export interface Decision {
  decision_status: string;
  fits_demo_rules: string[];
  pending: string[];
  external_actions_executed: boolean;
}

export interface ComparisonReport {
  field_brief?: FieldBrief;
  synthesis?: EvidenceSynthesis;
  comparison_id: string;
  scenario_revision: string;
  scenario?: Scenario;
  rule_version: string;
  engine_sha256?: string;
  scope: string;
  synthetic: boolean;
  agronomicViability: string;
  status: string;
  available_water_m3_ha: number | null;
  missing_fields?: string[];
  plans: Plan[];
  explanation: string;
  evidence: Evidence;
  resilience: LandReview;
  decision: Decision;
}

export interface FieldRecord {
  observed_at: string;
  soil_cover: 'unknown' | 'mostly_bare' | 'patchy' | 'well_covered';
  water_access: 'unknown' | 'reliable' | 'limited' | 'unreliable';
  constraint: 'none' | 'water' | 'cost' | 'tools' | 'time' | 'advice' | 'permission';
}

export interface EvidenceSynthesis {
  schema_version: 'solquira.synthesis/1';
  origin: 'model_selected' | 'deterministic';
  summary: string;
  evidence_ids: string[];
  checks: { id: string; title: string; detail: string; evidence_ids: string[]; destination: 'weather' | 'soil' | 'question' }[];
  missing_input_question: string;
  follow_up: string;
  sources: { id: string; label: string; kind: string }[];
  record?: FieldRecord & { provenance: 'self_reported'; missing_fields: string[] };
  external_actions_executed: false;
}

export interface FieldBrief {
  schema_version: 'solquira.field-brief/1'; context_ref: string; focus: string;
  location: {latitude:number;longitude:number}; created_at:string; summary:string; pattern:string;
  forecast: {source:string;kind:string;time_zone:string;retrieved_at:string;period_start:string;period_end:string;rain_mm:number;reference_et0_mm:number;temperature_min_c:number;temperature_max_c:number;days_without_rain:number};
  actions: {id:string;title:string;detail:string;evidence_ids:string[];destination:'weather'|'soil'|'question'}[];
  sources: {id:string;label:string;kind:string;url?:string}[];
  missing_inputs:string[]; external_actions_executed:false;
}

export interface Run {
  inference_mode: string;
  outcome: string;
  model: string | null;
  trace: { tool: string; status?: string; comparison_id?: string }[];
}

export interface Envelope {
  schema_version: string;
  scenario: Scenario;
  report: ComparisonReport;
  run: Run;
  request_id: string;
  requested_scenario?: Scenario;
  proposed_scenario?: Scenario;
}

export const INITIAL_SCENARIO: Scenario = { profile_id: 'valle', water_percent: 100, previous_crop: 'cereal' };
export const CROP_LABELS: Record<CropId, string> = { cereal: 'Cereal', leguminosa: 'Legume', cubierta: 'Cover crop', descanso: 'Fallow' };
export const PROFILE_LABELS: Record<ProfileId, string> = { valle: 'Irrigated valley', secano: 'Semi-arid dryland', precordillera: 'Andean foothills' };
export const sameScenario = (a: Scenario, b: Scenario) => a.profile_id === b.profile_id && a.water_percent === b.water_percent && a.previous_crop === b.previous_crop;
