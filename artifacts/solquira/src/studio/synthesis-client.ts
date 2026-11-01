import type { EvidenceSynthesis, FieldRecord } from './types';

const object = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown, max = 1200): v is string => typeof v === 'string' && v.trim().length > 0 && v.length <= max;
const list = (v: unknown, max = 12): v is string[] => Array.isArray(v) && v.length <= max && v.every(x => text(x, 200));
export const sameFieldRecord = (a?: FieldRecord | null, b?: FieldRecord | null) => !a || !b ? !a && !b : a.observed_at === b.observed_at && a.soil_cover === b.soil_cover && a.water_access === b.water_access && a.constraint === b.constraint;

export function parseSynthesis(value: unknown): EvidenceSynthesis {
  const fail = () => { throw new Error('The next-check response could not be verified. Please ask again.'); };
  if (!object(value) || value.schema_version !== 'solquira.synthesis/1' || !['model_selected', 'deterministic'].includes(String(value.origin)) || !text(value.summary, 2500) || !list(value.evidence_ids) || !text(value.missing_input_question) || !text(value.follow_up) || value.external_actions_executed !== false || !Array.isArray(value.sources) || value.sources.length > 12 || !Array.isArray(value.checks) || value.checks.length < 1 || value.checks.length > 4) return fail();
  const ids = new Set<string>();
  for (const source of value.sources) {
    if (!object(source) || !text(source.id, 200) || ids.has(source.id) || !text(source.label, 300) || !text(source.kind, 100)) return fail();
    ids.add(source.id);
  }
  const supported = (items: unknown) => list(items) && items.length > 0 && items.every(id => ids.has(id));
  if (!supported(value.evidence_ids)) return fail();
  const checkIds = new Set<string>();
  for (const check of value.checks) {
    if (!object(check) || !text(check.id, 100) || checkIds.has(check.id) || !text(check.title, 300) || !text(check.detail) || !supported(check.evidence_ids) || !['weather', 'soil', 'question'].includes(String(check.destination))) return fail();
    checkIds.add(check.id);
  }
  if (value.record !== undefined) {
    const r = value.record;
    if (!object(r) || r.provenance !== 'self_reported' || !list(r.missing_fields) || typeof r.observed_at !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(r.observed_at) || !Number.isFinite(Date.parse(r.observed_at)) || new Date(r.observed_at).toISOString().slice(0,10) !== r.observed_at || !['unknown','mostly_bare','patchy','well_covered'].includes(String(r.soil_cover)) || !['unknown','reliable','limited','unreliable'].includes(String(r.water_access)) || !['none','water','cost','tools','time','advice','permission'].includes(String(r.constraint))) return fail();
  }
  return value as unknown as EvidenceSynthesis;
}
