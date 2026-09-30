import type { Language } from "./types";

/**
 * Deteção de marcas de outra variante linguística por listas de marcadores
 * lexicais, ortográficos e gramaticais. Só inclui marcadores praticamente
 * exclusivos de uma variante, para evitar falsos positivos.
 */

export interface VariantMarker {
  term: string;
  kind: "léxico" | "ortografia" | "gramática";
  /** Variante a que o marcador pertence. */
  variant: Language;
  index: number;
}

const PT_BR_LEXICON = [
  "usuário", "usuária", "usuários", "usuárias", "ônibus", "trem", "trens", "celular", "celulares", "café da manhã", "banheiro", "banheiros",
  "geladeira", "sorvete", "açougue", "esporte", "esportes", "esportivo", "esportiva", "registro", "registros", "contato", "contatos",
  "gerenciar", "gerenciamento", "gerente de projeto", "aterrissagem", "aterrissar", "caminhão", "caminhões", "cadastro", "cadastrar",
  "planejamento", "planejar", "treinamento", "conosco", "umidade", "equipe", "equipes", "grampeador", "canadense",
  "xícara", "suco", "faixa de pedestres", "ponto de ônibus", "concreto armado", "aluguel", "torcida", "time de futebol",
  "a gente", "bacana", "galera", "guri", "moleque", "garoto", "garota", 
];

const PT_BR_SPELLING = [
  "gênero", "gêneros", "fenômeno", "fenômenos", "econômico", "econômica", "econômicos", "econômicas", "acadêmico", "acadêmica", "acadêmicos",
  "acadêmicas", "prêmio", "prêmios", "tênis", "anônimo", "anônima", "quilômetro", "quilômetros", "sinônimo", "sinônimos", "gêmeo", "gêmeos",
  "cômodo", "cômoda", "bônus", "pôster", "recepção", "concepção", "aspecto", "detectar", "detecção", "espectador", "expectativa",
  "contracepção", "percepção", "atômico", "vitamínico", "ingênuo", "polêmica", "polêmico", "quatorze", "hidrogênio", "oxigênio", "irônico", "sêmen",
];

const PT_PT_LEXICON = [
  "utilizador", "utilizadora", "utilizadores", "utilizadoras", "ficheiro", "ficheiros", "ecrã", "ecrãs", "equipa", "equipas", "telemóvel",
  "telemóveis", "comboio", "comboios", "autocarro", "autocarros", "pequeno-almoço", "casa de banho", "frigorífico", "gelado", "talho",
  "desporto", "desportos", "desportivo", "desportiva", "registo", "registos", "contacto", "contactos", "facto", "factos", "connosco",
  "humidade", "planeamento", "planear", "gerir a equipa", "rapariga", "raparigas", "miúdo", "miúda", "miúdos", "chávena", "sumo",
  "rebuçado", "passadeira", "paragem de autocarro", "betão", "renda de casa", "claque", "fixe", "giro", "malta", "puto",
  "ementa", "estafeta", "camisola", "casaco de malha", "autoclismo", "sanita", "canalizador", "bilhete de identidade", "cartão de cidadão",
];

const PT_PT_SPELLING = [
  "género", "géneros", "fenómeno", "fenómenos", "económico", "económica", "económicos", "económicas", "académico", "académica", "académicos",
  "académicas", "prémio", "prémios", "ténis", "anónimo", "anónima", "quilómetro", "quilómetros", "sinónimo", "sinónimos", "gémeo", "gémeos",
  "cómodo", "cómoda", "bónus", "receção", "conceção", "aspeto", "detetar", "deteção", "espetador", "expetativa", "contraceção", "perceção",
  "atómico", "vitamínico", "ingénuo", "polémica", "polémico", "catorze", "hidrogénio", "oxigénio", "irónico", "sémen",
];

const EN_US_SPELLING = [
  "color", "colors", "colored", "favor", "favors", "favorite", "favorites", "honor", "honors", "behavior", "behaviors", "neighbor", "neighbors",
  "center", "centers", "centered", "theater", "theaters", "meter", "meters", "liter", "liters", "organize", "organized", "organizes",
  "organizing", "organization", "organizations", "realize", "realized", "realizes", "recognize", "recognized", "analyze", "analyzed",
  "analyzing", "apologize", "apologized", "catalog", "catalogs", "dialog", "traveled", "traveling", "traveler", "canceled", "canceling",
  "modeling", "labeled", "labeling", "defense", "offense", "gray", "tire", "tires", "curb", "aluminum", "airplane", "jewelry", "pajamas",
  "plow", "skeptical", "mustache", "maneuver", "pediatric", "esthetic", "fulfill", "enrollment", "installment", "judgment",
];

const EN_US_LEXICON = [
  "apartment", "elevator", "truck", "trucks", "vacation", "cell phone", "zip code", "math", "soccer", "gotten", "sidewalk", "gas station",
  "flashlight", "diaper", "faucet", "cookie", "candy", "french fries", "chips", "eggplant", "zucchini", "parking lot", "trunk of the car",
  "hood of the car", "line up", "in line", "mailbox", "garbage", "trash can", "sweater", "sneakers", "pants", "undershirt", "fall semester",
];

const EN_GB_SPELLING = [
  "colour", "colours", "coloured", "favour", "favours", "favourite", "favourites", "honour", "honours", "behaviour", "behaviours", "neighbour",
  "neighbours", "centre", "centres", "centred", "theatre", "theatres", "metre", "metres", "litre", "litres", "organise", "organised", "organises",
  "organising", "organisation", "organisations", "realise", "realised", "realises", "recognise", "recognised", "analyse", "analysed", "analysing",
  "apologise", "apologised", "catalogue", "catalogues", "dialogue", "travelled", "travelling", "traveller", "cancelled", "cancelling",
  "modelling", "labelled", "labelling", "defence", "offence", "grey", "tyre", "tyres", "kerb", "aluminium", "aeroplane", "jewellery", "pyjamas",
  "plough", "sceptical", "moustache", "manoeuvre", "paediatric", "aesthetic", "fulfil", "enrolment", "instalment", "judgement", "cheque",
  "whilst", "amongst", "learnt", "spelt", "dreamt", "programme", "programmes",
];

const EN_GB_LEXICON = [
  "lorry", "lorries", "mobile phone", "postcode", "maths", "fortnight", "petrol", "pavement", "torch", "nappy", "nappies", "biscuit", "sweets",
  "crisps", "aubergine", "courgette", "car park", "boot of the car", "bonnet of the car", "queue up", "in the queue", "postbox", "rubbish",
  "dustbin", "jumper", "trainers", "trousers", "waistcoat", "autumn term", "flat to rent", "lift to the",
];

interface MarkerSet {
  lexicon: string[];
  spelling: string[];
  grammar: { re: RegExp; label: string }[];
}

const SETS: Record<Language, MarkerSet> = {
  "pt-BR": {
    lexicon: PT_BR_LEXICON,
    spelling: PT_BR_SPELLING,
    grammar: [
      { re: /\b(?:est(?:ou|á|ás|amos|ão|ava|avam|aria|ariam)|vou|vai|vamos|vão|fico|fica|ficam|continu(?:o|a|am|ava))\s+\p{L}+ndo\b/giu, label: "gerúndio progressivo («está fazendo»)" },
      { re: /(?:^|[.!?…]\s+)(?:Me|Te|Se|Lhe|Nos|Lhes)\s+\p{L}+/gu, label: "próclise em início de frase («Me disse»)" },
    ],
  },
  "pt-PT": {
    lexicon: PT_PT_LEXICON,
    spelling: PT_PT_SPELLING,
    grammar: [
      { re: /\b(?:est(?:ou|ás|á|amos|ão|ava|avam|aria|ariam)|and(?:o|as|a|amos|am)|fico|fica|ficam)\s+a\s+\p{L}+(?:ar|er|ir)\b/giu, label: "«estar a + infinitivo»" },
    ],
  },
  "en-US": { lexicon: EN_US_LEXICON, spelling: EN_US_SPELLING, grammar: [] },
  "en-GB": { lexicon: EN_GB_LEXICON, spelling: EN_GB_SPELLING, grammar: [] },
};

const OTHER: Record<Language, Language> = { "pt-PT": "pt-BR", "pt-BR": "pt-PT", "en-GB": "en-US", "en-US": "en-GB" };

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findTerms(text: string, terms: string[], kind: VariantMarker["kind"], variant: Language): VariantMarker[] {
  const out: VariantMarker[] = [];
  for (const term of terms) {
    const re = new RegExp(`(?<![\\p{L}\\p{N}-])${escapeRe(term)}(?![\\p{L}\\p{N}-])`, "giu");
    for (const match of text.matchAll(re)) out.push({ term: match[0], kind, variant, index: match.index ?? 0 });
  }
  return out;
}

/** Marcas da variante contrária à escolhida, presentes no texto. */
export function detectForeignVariant(text: string, target: Language): VariantMarker[] {
  const other = OTHER[target];
  const set = SETS[other];
  const markers = [
    ...findTerms(text, set.lexicon, "léxico", other),
    ...findTerms(text, set.spelling, "ortografia", other),
  ];
  for (const rule of set.grammar) {
    for (const match of text.matchAll(rule.re)) {
      markers.push({ term: `${match[0].trim()} — ${rule.label}`, kind: "gramática", variant: other, index: match.index ?? 0 });
    }
  }
  return markers.sort((a, b) => a.index - b.index);
}

/** Resumo: marcas de cada variante do par, para o indicador da interface. */
export function variantProfile(text: string, language: Language): { target: number; foreign: number; foreignTerms: string[] } {
  const foreign = detectForeignVariant(text, language);
  const own = detectForeignVariant(text, OTHER[language]);
  const unique = Array.from(new Set(foreign.map((m) => m.term)));
  return { target: own.length, foreign: foreign.length, foreignTerms: unique.slice(0, 12) };
}
