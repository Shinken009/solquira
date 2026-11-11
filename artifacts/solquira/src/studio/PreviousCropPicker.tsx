import { useRef } from "react";
import { Check } from "lucide-react";
import { usePanelCopy } from "./panel-copy";
import { CROP_SOURCE, cropName, searchCrops, type CropReference } from "./crop-reference";

export default function PreviousCropPicker({ text, selection, onText, onSelect, role = "previous" }: {
  role?: "previous" | "candidate";
  text: string;
  selection: CropReference | null;
  onText: (text: string) => void;
  onSelect: (crop: CropReference) => void;
}) {
  const { locale } = usePanelCopy();
  const copy = (en: string, es: string) => locale === "es" ? es : en;
  const id = role === "previous" ? "rotation-crop" : "rotation-candidate";
  const input = useRef<HTMLInputElement>(null);
  const { items, total } = selection ? { items: [], total: 0 } : searchCrops(text);
  return <div className="qj-crop" data-testid={id + "-picker"}>
    <label htmlFor={id}>{role === "previous" ? copy("Previous crop (optional)", "Cultivo anterior (opcional)") : copy("Crop you are considering (optional)", "Cultivo que estás considerando (opcional)")}</label>
    <input
      ref={input}
      type="search"
      id={id}
      maxLength={80}
      value={selection ? cropName(selection, locale) : text}
      onChange={event => onText(event.target.value)}
      placeholder={copy("Search a name or write your own", "Busca un nombre o escribe el tuyo")}
      aria-describedby={id + "-hint"}
      autoComplete="off"
      data-testid={id + "-search"}
    />
    <p id={id + "-hint"} className="qj-retention">{copy(
      "You can leave it blank if you don't know. Select a match to add its family, or keep the name as written.",
      "Si no lo sabes, déjalo en blanco. Elige una coincidencia para añadir su familia o conserva el nombre tal como lo escribiste.",
    )}</p>
    <div aria-live="polite">
      {selection ? <p className="qj-crop-selected" data-testid={id + "-selected"}>
        <Check size={16} aria-hidden="true" /> <i>{selection.scientificName}</i> · {selection.family}
      </p> : text.trim().length > 0 ? <p className="qj-retention" data-testid={id + "-status"}>{total > 0
        ? copy(`Showing ${items.length} of ${total} matches.`, `Se muestran ${items.length} de ${total} coincidencias.`)
        : copy("No match shown. You can continue with the name you wrote.", "No se muestra una coincidencia. Puedes continuar con el nombre que escribiste.")}</p> : null}
    </div>
    {items.length > 0 && <ul className="qj-choices qj-crop-options" data-testid={id + "-options"}>
      {items.map(crop => {
        const name = cropName(crop, locale);
        return <li key={crop.id}><button type="button" className="qj-choice" onClick={() => {
          onSelect(crop);
          input.current?.focus();
        }}>
          <span><strong>{name}</strong>{name !== crop.scientificName && <> · <i>{crop.scientificName}</i></>} · {crop.family}</span>
        </button></li>;
      })}
    </ul>}
    <details className="qj-limit">
      <summary>{copy("Catalogue source", "Fuente del catálogo")}</summary>
      <p>{copy("Taxonomy only. This list does not tell you what to plant here.", "Solo identifica cultivos; no indica qué sembrar aquí.")}{" "}
        <a href={CROP_SOURCE} target="_blank" rel="noreferrer">World Checklist of Food Plants v2</a> · CC BY 4.0
      </p>
    </details>
  </div>;
}
