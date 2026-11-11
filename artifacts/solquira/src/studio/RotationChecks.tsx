import { usePanelCopy } from "./panel-copy";
import { evaluateRotation, type RotationInputs } from "./rotation-checks";

type Copy = readonly [en: string, es: string];
type Result = ReturnType<typeof evaluateRotation>;
const headings = {
  window: ["Sowing window", "Ventana de siembra"],
  history: ["Crop history", "Historial de cultivo"],
  water: ["Water on hand", "Agua disponible"],
  soil: ["Soil drainage", "Drenaje del suelo"],
} as const;
const statusCopy = {
  attention: ["Review first", "Revisar primero"],
  check: ["Check or estimate", "Comprobar o estimar"],
  reference: ["Data compared", "Datos comparados"],
} as const;

export function rotationMessages(result: Result, previousKnown: boolean, candidateKnown: boolean, previousReported = false) {
  const window: Record<Result["window"], Copy> = {
    unknown: ["Find a local sowing window for this crop and season. Ask a grower or use an extension calendar for your zone.", "Busca una ventana de siembra para este cultivo y temporada. Consulta a un productor o un calendario de tu zona."],
    needDate: ["Enter a tentative sowing date to compare it with the window.", "Anota una fecha tentativa para compararla con la ventana de siembra."],
    invalid: ["Check that the start comes before or on the end date, and use full dates for the intended season.", "Comprueba que el inicio no sea posterior al cierre y usa fechas completas de la temporada prevista."],
    outside: ["Your date is outside the window you entered. Verify the local source before changing the sowing date.", "La fecha queda fuera de la ventana ingresada. Verifica la fuente local antes de cambiar la fecha de siembra."],
    inside: ["Your date is inside the window you entered. Confirm it applies to your variety, site and season.", "La fecha está dentro de la ventana ingresada. Confirma que aplica a tu variedad, terreno y temporada."],
  };
  const history: Record<Result["history"], Copy> = {
    unknown: !previousKnown && previousReported
      ? ["Keep the local name you entered. Ask the grower or a local adviser to identify its family before comparing rotations.", "Conserva el nombre local que escribiste. Consulta a quien cultivó o a un asesor local para identificar su familia antes de comparar rotaciones."]
      : !previousKnown
      ? ["Identify the previous crop and its season from notes or someone who worked the plot. Leave unknown details blank.", "Identifica el cultivo anterior y su temporada con tus notas o con quien trabajó la parcela. Puedes dejar datos en blanco."]
      : !candidateKnown
        ? ["Choose the crop you are considering. If its name is not listed, record it and confirm the family locally.", "Anota el cultivo que estás considerando. Si el nombre no aparece, regístralo y confirma su familia localmente."]
        : ["Confirm which crops were grown on this plot and when before comparing rotation options.", "Confirma qué cultivos hubo en esta parcela y cuándo antes de comparar opciones de rotación."],
    repeat: ["The two crops share a family. Check recent pest and disease records with local advice before repeating it.", "Los dos cultivos comparten familia. Revisa con apoyo local si hubo plagas o enfermedades antes de repetirla."],
    different: ["The families differ. Check whether known local pests or diseases can still affect both crops.", "Las familias son distintas. Revisa si alguna plaga o enfermedad local conocida afecta a ambos cultivos."],
  };
  const water: Record<Result["water"], Copy> = {
    unknown: ["Find out whether you rely on rainfall or can secure irrigation at establishment.", "Averigua si dependes de la lluvia o si puedes contar con riego para el establecimiento."],
    uncertain: ["Ask who manages the water when and for how long it can reach your plot.", "Consulta a quien gestiona el agua cuándo y por cuánto tiempo puede llegar a tu parcela."],
    rain: ["Ask about the local start of reliable rains and check root-zone moisture before sowing.", "Consulta cuándo suelen establecerse las lluvias en tu zona y comprueba la humedad en la zona de raíces antes de sembrar."],
    estimate: ["At a safe outlet, time how long it takes to fill a known volume. Check the minutes you can actually use.", "En una salida segura, cronometra cuánto demora en llenarse un volumen conocido. Confirma los minutos disponibles."],
    volume: ["Compare the estimated supply with a documented volume for the same plot, delivery point and turn.", "Compara la oferta estimada con un volumen documentado para la misma parcela, punto de entrega y turno."],
    below: ["The estimated supply is below the volume you entered. Verify the flow and turn before relying on that water.", "La oferta estimada queda por debajo del volumen ingresado. Verifica el caudal y el turno antes de contar con esa agua."],
    meets: ["The estimate reaches the entered volume for this turn. Confirm reliability; it does not cover the full season.", "La estimación alcanza el volumen ingresado para este turno. Confirma la disponibilidad: no cubre toda la temporada."],
  };
  const soil: Record<Result["soil"], Copy> = {
    requirement: ["Check whether local guidance calls for well-drained soil for this crop.", "Consulta si la guía local indica que este cultivo necesita suelo bien drenado."],
    matchGuide: ["You saw ponding. Check the crop's drainage needs with local guidance before choosing where to sow.", "Observaste agua estancada. Revisa los requisitos de drenaje del cultivo con una guía local antes de elegir dónde sembrar."],
    observe: ["After rain, observe from a safe spot where water collects and how long it remains.", "Después de una lluvia, observa desde un lugar seguro dónde se acumula agua y cuánto dura."],
    ponding: ["You saw ponding and the guide calls for good drainage. Review that area with local advice before sowing.", "Observaste agua estancada y la guía pide buen drenaje. Revisa ese sector con apoyo local antes de sembrar."],
    notSeen: ["No ponding was reported at that visit. Repeat the observation after rain; one visit cannot establish drainage.", "No observaste agua estancada en esa visita. Repite la observación tras una lluvia; una visita no confirma el drenaje."],
  };
  return { window: window[result.window], history: history[result.history], water: water[result.water], soil: soil[result.soil] };
}

export default function RotationChecks({ input, onChange, result, messages }: {
  input: RotationInputs;
  onChange: (changes: Partial<RotationInputs>) => void;
  result: Result;
  messages: ReturnType<typeof rotationMessages>;
}) {
  const { locale } = usePanelCopy();
  const copy = (en: string, es: string) => locale === "es" ? es : en;
  const label = (value: Copy) => copy(...value);
  const date = (key: "planned" | "start" | "end", title: Copy) => <label>{label(title)}
    <input type="date" value={input[key]} data-testid={`rotation-${key}`} onChange={e => onChange({ [key]: e.target.value })} />
  </label>;
  const quantity = (key: "flowLpm" | "minutes" | "requiredM3", title: Copy) => <label>{label(title)}
    <input type="text" inputMode="decimal" maxLength={16} value={input[key]} data-testid={`rotation-${key}`} onChange={e => onChange({ [key]: e.target.value, sameBasis: false })} />
  </label>;
  return <section className="qj-rotation" data-testid="rotation-checks" aria-label={copy("Rotation checks", "Comprobaciones de rotación")}>
    <p>{copy("Open any check you can answer. You can leave the rest blank and continue. Changing the crop clears its reference dates and requirements.", "Abre la comprobación que puedas responder. Puedes dejar lo demás en blanco. Si cambias el cultivo, se limpian sus fechas de referencia y requisitos.")}</p>
    {(Object.keys(headings) as (keyof typeof headings)[]).map(key => <details className="qj-rotation-card" key={key} id={`rotation-check-${key}`} data-testid={`rotation-check-${key}`}>
      <summary tabIndex={0} data-status={result.statuses[key]}><strong>{label(headings[key])}</strong> <span>{label(statusCopy[result.statuses[key]])}</span></summary>
      <p data-testid={`rotation-result-${key}`} aria-live="polite">{label(messages[key])}</p>
      {key === "window" && <>
        <p>{copy("Use dates for this season from local advice or a calendar covering your crop and zone.", "Usa fechas de esta temporada: consulta a alguien de tu zona o un calendario que cubra tu cultivo y territorio.")}{" "}
          <a href="https://cropcalendar.apps.fao.org/" target="_blank" rel="noreferrer">{copy("FAO Crop Calendar", "Calendario de cultivos de la FAO")}</a></p>
        <div className="qj-rotation-fields">
          {date("planned", ["Your tentative date", "Tu fecha tentativa"])}
          {date("start", ["Local window opens", "Inicio de la ventana local"])}
          {date("end", ["Local window closes", "Fin de la ventana local"])}
        </div>
      </>}
      {key === "water" && <p className="qj-retention">{copy(
        "To estimate a small flow safely: litres collected ÷ seconds × 60 = L/min. For example, 10 L in 30 seconds is 20 L/min. Only measure at an accessible outlet with permission.",
        "Para estimar un caudal pequeño con seguridad: litros recogidos ÷ segundos × 60 = L/min. Por ejemplo, 10 L en 30 segundos son 20 L/min. Mide solo en una salida accesible y con permiso.",
      )}</p>}
      {key === "water" && <div className="qj-rotation-fields">
        <label>{copy("Water source at establishment", "Fuente de agua al establecer el cultivo")}
          <select data-testid="rotation-water" value={input.water} onChange={e => onChange({ water: e.target.value as RotationInputs["water"], sameBasis: false })}>
            <option value="">{copy("I don't know yet", "No lo sé todavía")}</option>
            <option value="rain">{copy("Rain only", "Solo lluvia")}</option>
            <option value="uncertain">{copy("Irrigation not confirmed", "Riego sin confirmar")}</option>
            <option value="turn">{copy("Known irrigation turn", "Turno de riego conocido")}</option>
          </select>
        </label>
        {input.water === "turn" && <>
          {quantity("flowLpm", ["Flow at delivery (L/min, no thousands separator)", "Caudal en la entrega (L/min, sin separador de miles)"])}
          {quantity("minutes", ["Available minutes for this turn", "Minutos disponibles en este turno"])}
          {quantity("requiredM3", ["Documented volume for this turn (m³, optional)", "Volumen documentado para este turno (m³, opcional)"])}
          {result.volume !== null && <p data-testid="rotation-water-estimate">{copy("Estimated supply:", "Oferta estimada:")} {new Intl.NumberFormat(locale, { maximumSignificantDigits: 4 }).format(result.volume)} m³</p>}
          <label className="qj-rotation-confirm"><input type="checkbox" checked={input.sameBasis} data-testid="rotation-same-basis" onChange={e => onChange({ sameBasis: e.target.checked })} />
            {copy("Both volumes refer to the same plot, delivery point and turn (both before application losses).", "Ambos volúmenes corresponden a la misma parcela, punto de entrega y turno (ambos antes de las pérdidas de aplicación).")}
          </label>
        </>}
      </div>}
      {key === "soil" && <div className="qj-rotation-fields">
        <label>{copy("What does local guidance say?", "¿Qué indica la guía local?")}
          <select data-testid="rotation-drainage" value={input.drainage} onChange={e => onChange({ drainage: e.target.value as RotationInputs["drainage"] })}>
            <option value="">{copy("I don't know yet", "No lo sé todavía")}</option>
            <option value="required">{copy("This crop needs well-drained soil", "Este cultivo necesita suelo bien drenado")}</option>
            <option value="notSpecified">{copy("No drainage requirement specified", "No especifica un requisito de drenaje")}</option>
          </select>
        </label>
        <label>{copy("Did you see standing water after rain?", "¿Viste agua estancada después de la lluvia?")}
          <select data-testid="rotation-ponding" value={input.ponding} onChange={e => onChange({ ponding: e.target.value as RotationInputs["ponding"] })}>
            <option value="">{copy("I have not checked", "No lo he observado")}</option>
            <option value="seen">{copy("I saw standing water", "Vi agua estancada")}</option>
            <option value="notSeen">{copy("I did not see it on that visit", "No vi agua estancada en esa visita")}</option>
          </select>
        </label>
      </div>}
    </details>)}
    <details className="qj-limit">
      <summary>{copy("How these checks work", "Cómo funcionan estas comprobaciones")}</summary>
      <p>{copy("Dates and field conditions come from your entries. Family is a screening signal, not a disease diagnosis. The water estimate is flow × time, not crop demand or a watering schedule.", "Las fechas y condiciones vienen de tus datos. La familia es una señal de revisión, no un diagnóstico. El agua estimada es caudal × tiempo, no demanda del cultivo ni un programa de riego.")}</p>
      <ul>
        <li><a href="https://ucanr.edu/site/uc-master-gardeners-sacramento-county/crop-rotation" target="_blank" rel="noreferrer">UC Agriculture and Natural Resources</a></li>
        <li><a href="https://www.fao.org/4/u5835e/u5835e04.htm" target="_blank" rel="noreferrer">{copy("FAO: irrigation need and supply", "FAO: necesidad y suministro de riego")}</a></li>
        <li><a href="https://extension.umd.edu/resource/soil-health-drainage-and-improving-soil" target="_blank" rel="noreferrer">University of Maryland Extension</a></li>
      </ul>
    </details>
  </section>;
}
