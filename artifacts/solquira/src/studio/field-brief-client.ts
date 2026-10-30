import type { FieldBrief } from './types';

const object=(v:unknown):v is Record<string,unknown>=>Boolean(v)&&typeof v==='object'&&!Array.isArray(v);
const text=(v:unknown,max=1500):v is string=>typeof v==='string'&&v.trim().length>0&&v.length<=max;
const number=(v:unknown,min:number,max:number):v is number=>typeof v==='number'&&Number.isFinite(v)&&v>=min&&v<=max;
const date=(v:unknown):v is string=>text(v,40)&&Number.isFinite(Date.parse(v));

export function parseFieldBrief(v:unknown):FieldBrief {
  const fail=()=>{throw new Error('The field brief could not be verified. Reload the forecast before asking again.');};
  if(!object(v))return fail();
  if(v.schema_version!=='solquira.field-brief/1'||!text(v.context_ref,100)||!/^slq-context-[A-Za-z0-9_-]{24,80}$/.test(v.context_ref)||!['water','history','evidence','resilience','decision'].includes(String(v.focus))||!date(v.created_at)||!text(v.summary)||!['rain_below_reference_et0','rain_at_or_above_reference_et0'].includes(String(v.pattern))||v.external_actions_executed!==false)return fail();
  const p=v.location,f=v.forecast;
  if(!object(p)||!number(p.latitude,-90,90)||!number(p.longitude,-180,180)||!object(f)||f.source!=='Open-Meteo'||f.kind!=='model_forecast'||f.time_zone!=='UTC'||!date(f.retrieved_at)||!date(f.period_start)||!date(f.period_end)||Date.parse(f.period_end)-Date.parse(f.period_start)!==6*86400000||!number(f.rain_mm,0,14000)||!number(f.reference_et0_mm,0,700)||!number(f.temperature_min_c,-100,80)||!number(f.temperature_max_c,-100,80)||f.temperature_min_c>f.temperature_max_c||!number(f.days_without_rain,0,7)||!Number.isInteger(f.days_without_rain))return fail();
  if(!Array.isArray(v.sources)||v.sources.length!==2||!Array.isArray(v.actions)||v.actions.length!==3||!Array.isArray(v.missing_inputs)||v.missing_inputs.length>10||!v.missing_inputs.every(i=>text(i,300)))return fail();
  const ids=new Set<string>(),actions=new Set<string>();
  for(const s of v.sources){if(!object(s)||!text(s.id,100)||ids.has(s.id)||!text(s.label,200)||!['forecast-7d','field-inputs'].includes(s.id)||(s.id==='forecast-7d'&&(s.url!=='https://open-meteo.com/'||s.kind!=='model_forecast'))||(s.id==='field-inputs'&&(s.url!==undefined||s.kind!=='missing_inputs')))return fail();ids.add(s.id);}
  for(const a of v.actions){if(!object(a)||!text(a.id,100)||actions.has(a.id)||!text(a.title,200)||!text(a.detail)||!['weather','soil','question'].includes(String(a.destination))||!Array.isArray(a.evidence_ids)||a.evidence_ids.length===0||a.evidence_ids.length>2||!a.evidence_ids.every(id=>typeof id==='string'&&ids.has(id)))return fail();actions.add(a.id);}
  return v as unknown as FieldBrief;
}
