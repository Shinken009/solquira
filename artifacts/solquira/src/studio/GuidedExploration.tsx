import { useState } from "react";
import { ArrowRight, Check, CloudSun, Droplets, Leaf, MapPin, Sprout, UsersRound, Waves } from "lucide-react";
import type { Point } from "./geo/client";

type Concern = "water" | "soil" | "erosion" | "vegetation" | "explore";
type Choice = { id: string; label: string };
export interface GuidedRecordDraft {
  id: number;
  note: string;
  nextCheck: string;
  constraint: "" | "water_access" | "cost" | "advice" | "land_access";
}
const landUses: Choice[] = [
  { id: "crops", label: "Crops or a garden" }, { id: "grazing", label: "Livestock or grazing" },
  { id: "restoration", label: "Land I want to restore" }, { id: "natural", label: "Natural or community space" },
  { id: "unsure", label: "I am not sure yet" },
];
const concerns: { id: Concern; label: string; icon: typeof Leaf }[] = [
  { id: "water", label: "Water is a concern", icon: Droplets },
  { id: "soil", label: "The ground looks bare", icon: Leaf },
  { id: "erosion", label: "Rain carries soil away", icon: Waves },
  { id: "vegetation", label: "Plants do not look well", icon: Sprout },
  { id: "explore", label: "I just want to understand", icon: CloudSun },
];
const signals: Record<Exclude<Concern, "explore">, Choice[]> = {
  water: [{ id: "water_sometimes", label: "Sometimes there is not enough water" }, { id: "water_often", label: "Water is usually scarce" }, { id: "water_after_rain", label: "The ground dries soon after rain" }, { id: "water_unknown", label: "I am not sure" }],
  soil: [{ id: "soil_patches", label: "There are patches without plants or residue" }, { id: "soil_widespread", label: "Most ground is bare" }, { id: "soil_crust", label: "The surface looks hard or crusted" }, { id: "soil_unknown", label: "I am not sure" }],
  erosion: [{ id: "erosion_marks", label: "I see small channels or marks" }, { id: "erosion_much", label: "A lot of soil has moved" }, { id: "erosion_water", label: "Water runs strongly in some areas" }, { id: "erosion_unknown", label: "I am not sure" }],
  vegetation: [{ id: "plants_few", label: "Some plants look weak or dry" }, { id: "plants_many", label: "Many plants look weak or dry" }, { id: "plants_change", label: "The plants have changed recently" }, { id: "plants_unknown", label: "I am not sure" }],
};
const goals: Choice[] = [{ id: "explore_forecast", label: "What weather is expected?" }, { id: "explore_history", label: "How does recent rainfall compare?" }, { id: "explore_care", label: "What could I observe first?" }, { id: "explore_unknown", label: "I am still finding my question" }];
const durations: Choice[] = [{ id: "days", label: "A few days" }, { id: "weeks", label: "Several weeks" }, { id: "months", label: "Months or longer" }, { id: "unknown", label: "I am not sure" }];
const barriers: Choice[] = [{ id: "water", label: "Not enough water" }, { id: "resources", label: "Time, money or materials" }, { id: "support", label: "I need help or permission" }, { id: "none", label: "I could try a simple check" }, { id: "unknown", label: "I do not know yet" }];
const nextChecks: Record<Concern, string> = {
  water: "Check when water is actually available and how it reaches the land. Compare that record with the forecast before discussing irrigation with local support.",
  soil: "Choose an accessible bare patch and describe its plant or residue cover. Repeat the same observation later; discuss locally suitable protection before changing management.",
  erosion: "From a safe, accessible place, record where water leaves marks or moves soil. Do not enter flowing water or unstable ground; bring the observations to local support.",
  vegetation: "Compare affected and unaffected plants and note when the change began. Several causes are possible; do not assume a lack of water or choose a treatment from appearance alone.",
  explore: "Choose one accessible place to observe again. Note what is present, what is uncertain and which local information would make the next conversation more useful.",
};
const limits: Record<Concern, string> = {
  water: "A rainfall forecast does not measure soil moisture or calculate irrigation for this land.",
  soil: "Bare ground is a reported observation, not a measurement of soil health or degradation.",
  erosion: "These answers cannot quantify erosion, diagnose its cause or establish a safe intervention.",
  vegetation: "These answers are not a plant diagnosis. Weather alone cannot explain every change.",
  explore: "Environmental sources describe modeled conditions; they do not measure every part of your land.",
};
const choiceLabel = (list: Choice[], id: string) => list.find(item => item.id === id)?.label ?? "Not recorded";

function Choices({ name, options, value, onChange }: { name: string; options: Choice[]; value: string; onChange: (value: string) => void }) {
  return <div className="qj-choices">{options.map(option => <label key={option.id} className={value === option.id ? "qj-choice qj-choice-selected" : "qj-choice"}>
    <input type="radio" name={name} value={option.id} checked={value === option.id} onChange={() => onChange(option.id)} />
    <span>{option.label}</span><Check size={16} aria-hidden="true" />
  </label>)}</div>;
}

export default function GuidedExploration({ point, onChoosePlace, onWeather, onRecord }: {
  point: Point | null;
  onChoosePlace: () => void;
  onWeather: () => void;
  onRecord: (draft: GuidedRecordDraft) => void;
}) {
  const [landUse, setLandUse] = useState("");
  const [concern, setConcern] = useState<Concern | "">("");
  const [signal, setSignal] = useState("");
  const [duration, setDuration] = useState("");
  const [barrier, setBarrier] = useState("");
  const [ready, setReady] = useState(false);
  const [transferred, setTransferred] = useState(false);
  const explore = concern === "explore";
  const complete = Boolean(point && landUse && concern && signal && (explore || duration && barrier));
  const answered = 1 + Number(Boolean(landUse)) + Number(Boolean(concern)) + Number(Boolean(signal)) + (explore ? 0 : Number(Boolean(duration)) + Number(Boolean(barrier)));
  const edit = () => { setReady(false); setTransferred(false); };
  const nextCheck = concern ? nextChecks[concern] : "";
  const adjust = barrier === "water" ? "Start with observation, not a water-consuming intervention."
    : barrier === "resources" ? "Keep the check short and use what is already available."
    : barrier === "support" ? "Make the first step a conversation about help and permission."
    : "Choose one manageable check, not an entire management plan.";
  const record = () => {
    if (!concern || !complete) return;
    const options = explore ? goals : signals[concern as Exclude<Concern, "explore">];
    const note = `${choiceLabel(landUses, landUse)}. ${choiceLabel(options, signal)}${explore ? "" : `. Duration: ${choiceLabel(durations, duration)}`}. Self-reported; not a field measurement.`;
    const recordChecks: Record<Concern, string> = {
      water: "Record when water is available and how it reaches the land; discuss this with local support before deciding irrigation.",
      soil: "Describe plant and residue cover in an accessible bare patch, then repeat the same observation on a later visit.",
      erosion: "From a safe place, record where runoff leaves marks or moves soil; discuss these observations with local support.",
      vegetation: "Compare affected and unaffected plants and note when changes began; ask local support before choosing a treatment.",
      explore: "Choose one accessible place to observe again and record what is present, uncertain and useful to check next.",
    };
    onRecord({ id: Date.now(), note, nextCheck: recordChecks[concern], constraint: barrier === "water" ? "water_access" : "" });
    setTransferred(true);
  };
  return <section className="qj-exploration" id="qs-exploration" aria-labelledby="qj-exploration-title" data-testid="guided-exploration">
    <header className="qj-heading"><div><span className="qs-index">Your place. Your knowledge.</span><h2 id="qj-exploration-title" tabIndex={-1}>What matters here?</h2><p>A few choices connect what you notice with one practical next check.</p></div><span className="qj-local"><UsersRound size={16} aria-hidden="true" /> No AI request</span></header>
    {!point ? <div className="qj-empty" data-testid="guided-no-point"><MapPin size={28} aria-hidden="true" /><div><h3>Start with the place you mean.</h3><p>Mark it on the map or use your location, then confirm the point. You do not have to share your device location.</p><button type="button" className="qj-primary" onClick={onChoosePlace}>Choose a place <ArrowRight size={17} /></button></div></div> : <>
      <div className="qj-place"><MapPin size={17} aria-hidden="true" /><span>Confirmed analysis point <strong>{point.latitude.toFixed(4)}, {point.longitude.toFixed(4)}</strong></span><button type="button" onClick={onChoosePlace}>Change place</button></div>
      <div className="qj-progress"><span>{ready ? "A starting point, not a diagnosis" : `${answered} of ${explore ? 4 : 6} choices, including your place`}</span><span>Your answers stay in this open page</span></div>
      <form onSubmit={event => { event.preventDefault(); if (complete) setReady(true); }}>
        <div className="qj-question-grid">
          <fieldset><legend><span>02</span> What do you care for here?</legend><Choices name="guided-land-use" options={landUses} value={landUse} onChange={value => { edit(); setLandUse(value); }} /></fieldset>
          {landUse && <fieldset><legend><span>03</span> What brings you here today?</legend><div className="qj-choices">{concerns.map(item => <label key={item.id} className={concern === item.id ? "qj-choice qj-choice-selected" : "qj-choice"}><input type="radio" name="guided-concern" value={item.id} checked={concern === item.id} onChange={() => { edit(); setConcern(item.id); setSignal(""); setDuration(""); setBarrier(""); }} /><item.icon size={18} aria-hidden="true" /><span>{item.label}</span><Check size={16} aria-hidden="true" /></label>)}</div></fieldset>}
          {concern && <fieldset><legend><span>04</span> {explore ? "What would you like to understand?" : "What have you noticed?"}</legend><p>Not knowing is a useful answer, too.</p><Choices name="guided-signal" options={explore ? goals : signals[concern]} value={signal} onChange={value => { edit(); setSignal(value); }} /></fieldset>}
          {concern && !explore && signal && <fieldset><legend><span>05</span> How long have you noticed it?</legend><Choices name="guided-duration" options={durations} value={duration} onChange={value => { edit(); setDuration(value); }} /></fieldset>}
          {concern && !explore && duration && <fieldset><legend><span>06</span> What could make a next step difficult?</legend><Choices name="guided-barrier" options={barriers} value={barrier} onChange={value => { edit(); setBarrier(value); }} /></fieldset>}
        </div>
        {!ready && <div className="qj-submit"><button type="submit" className="qj-primary" data-testid="guided-prepare" disabled={!complete}>Find a place to start <ArrowRight size={17} /></button><p>Uses simple observation prompts. No data is sent to Kobo or a model.</p></div>}
      </form>
      {ready && concern && <div className="qj-result" data-testid="guided-result" role="status"><div className="qj-result-title"><Leaf size={23} aria-hidden="true" /><div><span>One manageable next check</span><h3>Start by looking, not by guessing.</h3></div></div><p className="qj-next-check">{nextCheck}</p>{!explore && <p className="qj-adjustment"><UsersRound size={17} aria-hidden="true" />{adjust}</p>}<p className="qj-limit">{limits[concern]} This is orientation from your answers, not a forecast or a diagnosis.</p><div className="qj-actions"><button type="button" className="qj-primary" onClick={onWeather}>{signal === "explore_history" ? "Review climate & request history" : "Compare with the coming weather"}<CloudSun size={17} /></button><button type="button" className="qj-secondary" data-testid="guided-to-record" onClick={record}>{transferred ? "Continue your land record" : "Keep this in my land record"}<ArrowRight size={16} /></button></div><p className="qj-retention">The existing land record keeps your notes and follow-up date. Nothing is saved remotely or measured automatically.</p></div>}
    </>}
  </section>;
}
