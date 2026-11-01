import { ArrowUpRight, ClipboardCheck, UsersRound } from 'lucide-react';
import type { EvidenceSynthesis as Synthesis } from './types';
import './evidence-synthesis.css';

export default function EvidenceSynthesis({ synthesis, onReview }: { synthesis: Synthesis; onReview: (destination: 'weather' | 'soil' | 'question') => void }) {
  const sourceLabel = (id: string) => synthesis.sources.find(s => s.id === id)?.label ?? id;
  return <section className="qes-panel" aria-labelledby="qes-title" data-testid="evidence-synthesis">
    <header><span><ClipboardCheck size={18}/> Your next field conversation</span><small>{synthesis.origin === 'model_selected' ? 'Organised with Nemotron' : 'Evidence-based checklist'}</small></header>
    <h3 id="qes-title">A practical place to start.</h3>
    <p className="qes-summary" data-testid="synthesis-summary">{synthesis.summary}</p>
    {synthesis.record && <p className="qes-record"><UsersRound size={16}/> Includes your selected observations from {synthesis.record.observed_at}.</p>}
    <ol className="qes-checks">{synthesis.checks.map((check, index) => <li key={check.id}>
      <span className="qes-step" aria-hidden="true">{index + 1}</span><div><h4>{check.title}</h4><p>{check.detail}</p><ul aria-label="Supporting evidence">{check.evidence_ids.map(id => <li key={id}>{sourceLabel(id)}</li>)}</ul></div>
      <button type="button" onClick={() => onReview(check.destination)} aria-label={`Review support for ${check.title}`}><ArrowUpRight size={18}/></button>
    </li>)}</ol>
    <div className="qes-conversation"><div><strong>One question to bring</strong><p>{synthesis.missing_input_question}</p></div><div><strong>Keep the conversation going</strong><p>{synthesis.follow_up}</p></div></div>
    <details><summary>How this next step was prepared</summary><p>{synthesis.origin === 'model_selected' ? 'Nemotron selected and ordered supported checks from the evidence available in this session.' : 'These checks were assembled directly from the evidence available in this session.'} Your selected observations are self-reported. Private notes are not included.</p><ul>{synthesis.sources.map(source => <li key={source.id}>{source.label}</li>)}</ul></details>
  </section>;
}
