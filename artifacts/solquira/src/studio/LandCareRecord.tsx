import { useEffect, useState, type FormEvent } from 'react';
import { CalendarDays, ChevronDown, Download, Leaf, UsersRound } from 'lucide-react';
import type { AttachedContext } from './geo/client';
import type { FieldRecord } from './types';
import type { GuidedRecordDraft } from './GuidedExploration';
import './land-care-record.css';

const sources = [
  { label: 'UNCCD · Land degradation neutrality principles', url: 'https://www.unccd.int/land-and-life/land-degradation-neutrality/ldn-principles' },
  { label: 'FAO / UNCCD · Living soils, productive lands, resilient landscapes', url: 'https://www.unccd.int/resources/publications/living-soils-productive-lands-resilient-landscapes-guidance-avoiding' },
  { label: 'IPCC · Climate Change and Land, summary for policymakers', url: 'https://www.ipcc.ch/srccl/chapter/summary-for-policymakers/' },
];
const covers = { '': 'Not recorded', mostly_bare: 'Mostly bare ground', patchy: 'Patchy living or residue cover', mostly_covered: 'Mostly covered by plants or residue', unsure: 'Not sure how to describe it' };
const runoffSigns = { '': 'Not recorded', none_seen: 'No signs seen on this visit', channels: 'Small channels or rills', ponding: 'Water collecting or ponding', unsure: 'Not sure what I saw' };
const constraints = { '': 'Choose a constraint, if relevant', water_access: 'Access to water', cost: 'Cost', tools: 'Tools or equipment', advice: 'Access to advice', time: 'Time for follow-up', land_access: 'Access or permission to use land' };
const waterAccess = { unknown: 'Not recorded', reliable: 'Reliable access', limited: 'Limited availability', unreliable: 'Unreliable or unpredictable access' };
const roles = { '': 'Choose a support role', adviser: 'Local adviser', grower_group: 'Grower group', water_association: 'Water association', other: 'Other local support' };
type Cover = keyof typeof covers;
type Constraint = keyof typeof constraints;
const coverQuestions: Record<Exclude<Cover, ''>, string> = {
  mostly_bare: 'What might explain the bare ground recorded on this visit, and which locally suitable ways of protecting soil should we discuss?',
  patchy: 'Where are the cover gaps, and what should we observe to understand whether exposed patches connect to runoff paths?',
  mostly_covered: 'Where is plant or residue cover continuous, and what changes should we document on a repeat visit?',
  unsure: 'How can we describe ground cover consistently together on the next visit?',
};
const constraintQuestions: Record<Exclude<Constraint, ''>, string> = {
  water_access: 'Which water-access or scheduling constraints should we confirm together before planning the next field task?',
  cost: 'What can we check with tools already available, and who could help if cost prevents a follow-up?',
  tools: 'Which tools are available for a simple check, and could a local group share what is missing?',
  advice: 'Which local adviser or peer group could help review these observations before choosing a response?',
  time: 'Which single observation would be most useful to collect in the time available before our next visit?',
  land_access: 'Who can clarify access or permission for the next visit, and whose participation is needed before any land-care work?',
};
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const validDate = (value: string) => /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;

export default function LandCareRecord({ context, draft = null, agentEnabled = false, attachedRecord = null, onAttachRecord }: { context: AttachedContext | null; draft?: GuidedRecordDraft | null; agentEnabled?: boolean; attachedRecord?: FieldRecord | null; onAttachRecord?: (record: FieldRecord | null) => void }) {
  const [open, setOpen] = useState(false), [started, setStarted] = useState(false);
  const [snapshot, setSnapshot] = useState<AttachedContext | null>(null), [includePoint, setIncludePoint] = useState(false);
  const [observed, setObserved] = useState(''), [followUp, setFollowUp] = useState('');
  const [cover, setCover] = useState<Cover>(''), [runoff, setRunoff] = useState<keyof typeof runoffSigns>('');
  const [constraint, setConstraint] = useState<Constraint>(''), [support, setSupport] = useState<keyof typeof roles>('');
  const [water, setWater] = useState<FieldRecord['water_access']>('unknown');
  const invalidateShared = () => { if (attachedRecord) onAttachRecord?.(null); };
  const dateAge = (Date.parse(new Date().toISOString().slice(0, 10)) - Date.parse(observed)) / 86400000;
  const shareReady = validDate(observed) && dateAge >= 0 && dateAge <= 365;
  const shareRecord = () => {
    if (!agentEnabled || !shareReady) return;
    const soil: FieldRecord['soil_cover'] = cover === 'mostly_covered' ? 'well_covered' : cover === 'mostly_bare' || cover === 'patchy' ? cover : 'unknown';
    const barrier: FieldRecord['constraint'] = constraint === 'water_access' ? 'water' : constraint === 'land_access' ? 'permission' : constraint || 'none';
    onAttachRecord?.({ observed_at: observed, soil_cover: soil, water_access: water, constraint: barrier });
  };
  const [note, setNote] = useState(''), [nextCheck, setNextCheck] = useState(''), [downloadNotice, setDownloadNotice] = useState('');
  useEffect(() => {
    if (!draft) return;
    setStarted(true); setOpen(true);
    // The person explicitly transferred these choices. Never overwrite an
    // existing note, date, constraint or planned check from a previous visit.
    setNote(value => value || draft.note.slice(0, 280));
    setNextCheck(value => value || draft.nextCheck);
    setConstraint(value => value || draft.constraint);
    setDownloadNotice('Exploration choices added only to empty fields. Confirm the observation and follow-up dates; existing notes stay unchanged.');
  }, [draft]);
  const questions = [cover ? coverQuestions[cover] : '', constraint ? constraintQuestions[constraint] : ''].filter(Boolean);
  const ready = validDate(observed) && observed <= localToday() && validDate(followUp) && followUp >= observed && nextCheck.trim().length > 0;
  const toggle = () => {
    if (!started) {
      setStarted(true); setObserved(localToday());
      setSnapshot(context ? { ...context, location: { ...context.location } } : null);
    }
    setOpen(value => !value);
  };
  const clear = () => {
    onAttachRecord?.(null); setWater('unknown');
    setOpen(false); setStarted(false); setSnapshot(null); setIncludePoint(false); setObserved(''); setFollowUp('');
    setCover(''); setRunoff(''); setConstraint(''); setSupport(''); setNote(''); setNextCheck(''); setDownloadNotice('');
  };
  const download = (event: FormEvent) => {
    event.preventDefault(); if (!ready) return;
    const lines = ['Solquira | Land & livelihoods record', `Observation date: ${observed}`, 'Entries are your field notes; not a measured land assessment.',
      ...(includePoint && snapshot ? [`Optional rounded reference point: ${snapshot.location.latitude.toFixed(3)}, ${snapshot.location.longitude.toFixed(3)}`, `Point snapshot from attached forecast retrieved: ${snapshot.retrievedAt}`, 'This point was chosen when the record started and is not updated by later forecast changes.'] : ['Location: not included']),
      '', `Ground cover: ${covers[cover]}`, `Water access: ${waterAccess[water]}`, `Runoff signs: ${runoffSigns[runoff]}`, `Field note: ${note.trim() || 'Not recorded'}`,
      '', `Constraint to discuss: ${constraint ? constraints[constraint] : 'Not selected'}`, `Support role: ${support ? roles[support] : 'Not selected'}`,
      '', 'Questions to bring:', ...(questions.length ? questions.map(question => `- ${question}`) : ['- No discussion prompt selected.']),
      '', `One next check: ${nextCheck.trim()}`, `Follow-up date: ${followUp}`, '',
      'This download contains your own record. Selected date, cover, water-access and constraint choices can separately be attached to a question. Free-text notes remain in this page until downloaded.',
      '', 'Background sources (not an assessment of this location):', ...sources.map(source => `${source.label}\n${source.url}`)];
    const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `solquira-land-record-${observed}.txt`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); setDownloadNotice('Download prepared. Keep the file for your next conversation.');
  };
  return <section className="qlr-record" id="qs-land-record" aria-labelledby="qlr-title" data-testid="land-care-record">
    <div className="qlr-heading"><div><span className="qlr-label"><UsersRound size={17}/> Land & livelihoods</span><h2 id="qlr-title" tabIndex={-1}>Land care is people care.</h2><p>Keep what you notice, what makes follow-up difficult, and one check for the next visit. Make room for local knowledge and the people who can help.</p></div><button className="qlr-toggle" type="button" data-testid="land-record-start" aria-expanded={open} aria-controls="qlr-form-area" onClick={toggle}>{!started ? 'Start a land record' : open ? 'Fold this record' : 'Continue this record'}<ChevronDown size={17}/></button></div>
    <ol className="qlr-rail" aria-label="Land and livelihoods record journey"><li><Leaf size={21}/><div><strong>Land</strong><span>Notice cover and water paths.</span></div></li><li><UsersRound size={21}/><div><strong>People</strong><span>Name a constraint and a support role.</span></div></li><li><CalendarDays size={21}/><div><strong>Next visit</strong><span>Keep one check and a date.</span></div></li></ol>
    <p className="qlr-retention">Download your record for the next visit.</p>
    {open && <div id="qlr-form-area" className="qlr-form-area"><form onSubmit={download} data-testid="land-record-form">
      <fieldset><legend>What you noticed</legend><p className="qlr-hint">Your observations, in your own words. Leave anything you did not observe unrecorded.</p>
        <div className="qlr-fields"><label>Observation date<input type="date" data-testid="land-record-observed" value={observed} max={localToday()} required onChange={event => { invalidateShared(); setObserved(event.target.value); }}/></label><label>Ground cover<select data-testid="land-record-cover" value={cover} onChange={event => { invalidateShared(); setCover(event.target.value as Cover); }}>{Object.entries(covers).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Water access<select data-testid="land-record-water" value={water} onChange={event => { invalidateShared(); setWater(event.target.value as FieldRecord['water_access']); }}>{Object.entries(waterAccess).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Runoff signs<select data-testid="land-record-runoff" value={runoff} onChange={event => setRunoff(event.target.value as keyof typeof runoffSigns)}>{Object.entries(runoffSigns).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label className="qlr-wide">Field note <small>No names or contact details.</small><textarea data-testid="land-record-note" value={note} maxLength={280} rows={2} onChange={event => setNote(event.target.value.slice(0, 280))} placeholder="What did you see, and where would you look again?"/></label></div>
      </fieldset>
      <fieldset><legend>Who and what could help</legend><p className="qlr-hint">Land care also depends on access, resources and time. Choose a role, not a person's name.</p><div className="qlr-fields"><label>Constraint to discuss<select data-testid="land-record-constraint" value={constraint} onChange={event => { invalidateShared(); setConstraint(event.target.value as Constraint); }}>{Object.entries(constraints).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Support role<select data-testid="land-record-support" value={support} onChange={event => setSupport(event.target.value as keyof typeof roles)}>{Object.entries(roles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label></div>
        {questions.length > 0 && <div className="qlr-questions" data-testid="land-record-questions" aria-live="polite"><h3>{questions.length > 1 ? 'Questions to bring' : 'A question to bring'}</h3><ul>{questions.map(question => <li key={question}>{question}</li>)}</ul></div>}
      </fieldset>
      <fieldset><legend>One manageable next step</legend><div className="qlr-fields"><label className="qlr-wide">One next check<textarea data-testid="land-record-next-check" value={nextCheck} maxLength={160} rows={2} required onChange={event => setNextCheck(event.target.value.slice(0, 160))} placeholder="For example: walk the bare patch with a local adviser."/></label><label>Follow-up date<input type="date" data-testid="land-record-followup" value={followUp} min={observed || undefined} required onChange={event => setFollowUp(event.target.value)}/></label></div>
        {snapshot && <div className="qlr-point"><label><input type="checkbox" data-testid="land-record-point" checked={includePoint} onChange={event => setIncludePoint(event.target.checked)}/> Include the rounded reference point from the forecast attached when this record started: {snapshot.location.latitude.toFixed(3)}, {snapshot.location.longitude.toFixed(3)}</label><p>Later forecast changes do not update this snapshot. Clear the record to start with another point.</p></div>}
      </fieldset>
      {agentEnabled && <div className="qlr-share" data-testid="land-record-share-panel"><div><h3>Bring your field context into the conversation.</h3><p>Share only the observation date, ground cover, water access and constraint you selected. Notes, names, runoff descriptions and coordinates are not included.</p><p className="qlr-share-preview">{observed || 'Date needed'} · {covers[cover]} · {waterAccess[water]} · {constraint ? constraints[constraint] : 'No constraint selected'}</p></div><div><button type="button" className="qlr-download" data-testid="land-record-attach" disabled={!shareReady || Boolean(attachedRecord)} onClick={shareRecord}>{attachedRecord ? 'Attached to your next question' : 'Attach selected observations'}</button>{attachedRecord && <button type="button" className="qlr-clear" data-testid="land-record-detach" onClick={() => onAttachRecord?.(null)}>Remove from question</button>}</div><p role="status">{attachedRecord ? 'Your next submitted question will include these choices. Editing a choice removes the attachment.' : shareReady ? 'Nothing is sent until you submit your next question. Selected choices go to the connected service and, when available, Nemotron on Nebius.' : 'Use a valid observation date within the last year to attach these choices.'}</p></div>}
      <div className="qlr-actions"><button type="submit" className="qlr-download" data-testid="land-record-download" disabled={!ready}><Download size={17}/> Download land record</button><button type="button" className="qlr-clear" onClick={clear}>Clear this record</button><p>Your private notes stay in this open page until you download them.</p></div><p className="qlr-download-notice" role="status">{downloadNotice}</p>
    </form></div>}
    <details className="qlr-sources"><summary>Why land and livelihoods belong together</summary><p>Land-care planning includes local knowledge and social and economic conditions. <a href={sources[0].url} target="_blank" rel="noreferrer">UNCCD principles</a>.</p><p>FAO / UNCCD guidance connects land stewardship with tenure, inclusion and livelihoods. <a href={sources[1].url} target="_blank" rel="noreferrer">Read the guidance</a>.</p><a href={sources[2].url} target="_blank" rel="noreferrer">IPCC: land, water and collective action</a></details>
  </section>;
}
