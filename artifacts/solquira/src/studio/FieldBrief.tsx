import { useState } from 'react';
import { ArrowUpRight, ClipboardCheck, Download, Droplets, Leaf, Sun } from 'lucide-react';
import type { FieldBrief as Brief } from './types';
import './field-brief.css';

export default function FieldBrief({brief,onReview}:{brief:Brief;onReview:(destination:'weather'|'soil'|'question')=>void}) {
  const [checked,setChecked]=useState<string[]>([]);
  const download=()=>{
    const text=['Solquira | Field conversation brief',`Location: ${brief.location.latitude.toFixed(3)}, ${brief.location.longitude.toFixed(3)}`,
      `Forecast: ${brief.forecast.period_start} to ${brief.forecast.period_end} UTC`, `Source: Open-Meteo https://open-meteo.com/ | Retrieved ${brief.forecast.retrieved_at}`,
      '',brief.summary,'',...brief.actions.map((a,i)=>`${checked.includes(a.id)?'[x]':'[ ]'} ${i+1}. ${a.title}\n${a.detail}`),
      '', 'Field inputs still needed:',...brief.missing_inputs.map(s=>`- ${s}`),'',
      'Checklist marks are your notes, not verified observations. Reference ET0 is not crop water need. The location forecast does not change the synthetic crop comparison. No agricultural action was executed.'].join('\n');
    const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=`solquira-field-brief-${brief.forecast.period_start}.txt`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  return <section className="qf-brief" data-testid="field-brief" aria-labelledby="qf-title">
    <header><div><span className="qf-eyebrow"><Leaf size={15}/> Your field brief</span><h3 id="qf-title" tabIndex={-1}>From forecast to field.</h3><p>{brief.location.latitude.toFixed(3)}, {brief.location.longitude.toFixed(3)} · {brief.forecast.period_start} — {brief.forecast.period_end} UTC</p></div><button type="button" className="qf-download" onClick={download}><Download size={15}/> Save brief</button></header>
    <div className="qf-forecast" aria-label="Forecast totals supporting these checks"><div><Droplets size={18}/><span>Seven-day rainfall</span><strong>{brief.forecast.rain_mm.toFixed(1)}<small> mm</small></strong></div><div><Sun size={18}/><span>Reference water demand</span><strong>{brief.forecast.reference_et0_mm.toFixed(1)}<small> mm ET₀</small></strong></div><p>Open-Meteo forecast · Reference ET₀ describes atmospheric water demand.</p></div>
    <div className="qf-evidence"><Droplets size={23}/><p data-testid="field-brief-summary">{brief.summary}</p></div>
    <div className="qf-checklist-heading"><h4><ClipboardCheck size={17}/> Take these checks to the field</h4><span>{checked.length} / {brief.actions.length} noted</span></div>
    <ol className="qf-actions">{brief.actions.map((action,index)=><li key={action.id}><label><input type="checkbox" checked={checked.includes(action.id)} onChange={event=>setChecked(items=>event.target.checked?[...items,action.id]:items.filter(id=>id!==action.id))}/><span className="qf-action-copy">{index===0&&<small className="qf-first-check">Start here</small>}<strong>{action.title}</strong><span>{action.detail}</span></span></label><button type="button" onClick={()=>onReview(action.destination)} aria-label={`Open supporting ${action.destination} for step ${index+1}`}>Review <ArrowUpRight size={14}/></button></li>)}</ol>
    <p className="qf-check-note">Keep the checks you want to discuss on your next field visit.</p>
    <details><summary>Sources & inputs to bring</summary><p>Forecast retrieved {new Date(brief.forecast.retrieved_at).toISOString().slice(0,16).replace('T',' ')} UTC. Reference ET₀ is not a crop-specific irrigation amount.</p><ul>{brief.sources.map(source=><li key={source.id}>{source.url?<a href={source.url} target="_blank" rel="noreferrer">{source.label}</a>:source.label}</li>)}</ul><ul>{brief.missing_inputs.map(input=><li key={input}>{input}</li>)}</ul><p>The location forecast informs these checks, not the synthetic crop ranking. NASA imagery was not analysed by the agent.</p></details>
  </section>;
}
