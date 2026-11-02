import { usePanelCopy } from "./panel-copy";
import { useState } from 'react';
import { ArrowUpRight, ClipboardCheck, Download, Droplets, Leaf, Sun } from 'lucide-react';
import type { FieldBrief as Brief } from './types';
import './field-brief.css';

export default function FieldBrief({brief,onReview}:{brief:Brief;onReview:(destination:'weather'|'soil'|'question')=>void}) {
  const { t, locale, number, date } = usePanelCopy();
  const [checked,setChecked]=useState<string[]>([]);
  const download=()=>{
    const text=[t('Solquira | Field conversation brief'),`${locale === 'es' ? 'Ubicación' : 'Location'}: ${brief.location.latitude.toFixed(3)}, ${brief.location.longitude.toFixed(3)}`,
      `${locale === 'es' ? 'Pronóstico' : 'Forecast'}: ${brief.forecast.period_start} — ${brief.forecast.period_end} UTC`, `${t('Source')}: Open-Meteo https://open-meteo.com/ | ${t('Retrieved')} ${brief.forecast.retrieved_at}`,
      '',t(brief.summary),'',...brief.actions.map((a,i)=>`${checked.includes(a.id)?'[x]':'[ ]'} ${i+1}. ${t(a.title)}\n${t(a.detail)}`),
      '', t('Field inputs still needed:'),...brief.missing_inputs.map(s=>`- ${t(s)}`),'',
      t('Checklist marks are your notes, not verified observations. Reference ET0 is not crop water need. The location forecast does not change the synthetic crop comparison. No agricultural action was executed.')].join('\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=`solquira-field-brief-${brief.forecast.period_start}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  return <section className="qf-brief" data-testid="field-brief" aria-labelledby="qf-title">
    <header><div><span className="qf-eyebrow"><Leaf size={15}/>{t(" Your field brief")}</span><h3 id="qf-title" tabIndex={-1}>{t("From forecast to field.")}</h3><p>{brief.location.latitude.toFixed(3)}, {brief.location.longitude.toFixed(3)} · {brief.forecast.period_start} — {brief.forecast.period_end} UTC</p></div><button type="button" className="qf-download" onClick={download}><Download size={15}/>{t(" Save brief")}</button></header>
    <div className="qf-forecast" aria-label={t("Forecast totals supporting these checks")}><div><Droplets size={18}/><span>{t("Seven-day rainfall")}</span><strong>{number(brief.forecast.rain_mm)}<small> mm</small></strong></div><div><Sun size={18}/><span>{t("Reference water demand")}</span><strong>{number(brief.forecast.reference_et0_mm)}<small> mm ET₀</small></strong></div><p>{t("Open-Meteo forecast · Reference ET₀ describes atmospheric water demand.")}</p></div>
    <div className="qf-evidence"><Droplets size={23}/><p data-testid="field-brief-summary">{t(brief.summary)}</p></div>
    <div className="qf-checklist-heading"><h4><ClipboardCheck size={17}/>{t(" Take these checks to the field")}</h4><span>{checked.length} / {brief.actions.length}{t(" noted")}</span></div>
    <ol className="qf-actions">{brief.actions.map((action,index)=><li key={action.id}><label><input type="checkbox" checked={checked.includes(action.id)} onChange={event=>setChecked(items=>event.target.checked?[...items,action.id]:items.filter(id=>id!==action.id))}/><span className="qf-action-copy">{index===0&&<small className="qf-first-check">{t("Start here")}</small>}<strong>{t(action.title)}</strong><span>{t(action.detail)}</span></span></label><button type="button" onClick={()=>onReview(action.destination)} aria-label={locale === "es" ? `Revisar apoyo del paso ${index+1}` : `Open supporting ${action.destination} for step ${index+1}`}>{t("Review ")}<ArrowUpRight size={14}/></button></li>)}</ol>
    <p className="qf-check-note">{t("Keep the checks you want to discuss on your next field visit.")}</p>
    <details><summary>{t("Sources & inputs to bring")}</summary><p>{t("Forecast retrieved ")}{new Date(brief.forecast.retrieved_at).toISOString().slice(0,16).replace('T',' ')}{t(" UTC. Reference ET₀ is not a crop-specific irrigation amount.")}</p><ul>{brief.sources.map(source=><li key={source.id}>{source.url?<a href={source.url} target="_blank" rel="noreferrer">{t(source.label)}</a>:t(source.label)}</li>)}</ul><ul>{brief.missing_inputs.map(input=><li key={t(input)}>{t(input)}</li>)}</ul><p>{t("The location forecast informs these checks, not the synthetic crop ranking. NASA imagery was not analysed by the agent.")}</p></details>
  </section>;
}
