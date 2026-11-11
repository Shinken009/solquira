import rows from "./crop-reference.wcfp-v2.json";
import aliasData from "./crop-aliases.json";

type Language = "en" | "es";
type Aliases = Record<Language, string[]>;
const aliases: Record<string, Aliases> = aliasData;

export interface CropReference {
  id: number;
  scientificName: string;
  family: string;
}

export const CROP_SOURCE = "https://doi.org/10.6084/m9.figshare.31441615.v2";

const normalize = (value: string) => value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim().replace(/\s+/g, " ");

const index = rows.map(([id, scientificName, family]) => {
  const crop = { id: Number(id), scientificName: String(scientificName), family: String(family) };
  const names = aliases[crop.scientificName];
  return { crop, keys: [crop.scientificName, ...(names?.en ?? []), ...(names?.es ?? [])].map(normalize) };
});

export function cropName(crop: CropReference, locale: Language) {
  return aliases[crop.scientificName]?.[locale][0] ?? crop.scientificName;
}

export function searchCrops(input: string) {
  const query = normalize(input);
  if (query.length < 2) return { items: [], total: 0 };
  const matches = index.flatMap(({ crop, keys }) => {
    const rank = keys.includes(query) ? 0 : keys.some(key => key.startsWith(query)) ? 1 : keys.some(key => key.includes(query)) ? 2 : -1;
    return rank < 0 ? [] : [{ crop, rank }];
  }).sort((a, b) => a.rank - b.rank);
  return { items: matches.slice(0, 6).map(match => match.crop), total: matches.length };
}

export function previousCropNote(crop: CropReference | null, text: string, locale: Language) {
  if (crop) {
    const name = cropName(crop, locale);
    const label = name === crop.scientificName ? name : `${name} (${crop.scientificName})`;
    return locale === "es"
      ? `Cultivo anterior declarado: ${label}. Familia: ${crop.family} (WCFP v2, #${crop.id}).`
      : `Reported previous crop: ${label}. Family: ${crop.family} (WCFP v2, #${crop.id}).`;
  }
  const label = text.trim();
  if (!label) return null;
  return locale === "es"
    ? `Cultivo anterior declarado: ${label}. Nombre local, sin identificación taxonómica.`
    : `Reported previous crop: ${label}. Local name, no taxonomic identification.`;
}
