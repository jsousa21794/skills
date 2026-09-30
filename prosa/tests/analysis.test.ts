import { describe, expect, it } from "vitest";
import { applyDecisions, computeChanges } from "@/lib/changes";
import { findPatterns, summarizePatterns, termsToAvoid } from "@/lib/patterns";
import { computeReadability, countSyllablesEn, countSyllablesPt } from "@/lib/readability";
import { joinTokens, splitSentences, tokenizeSentences } from "@/lib/text";
import { detectForeignVariant, variantProfile } from "@/lib/variant";
import { computeVoiceMetrics, describeVoiceMetrics } from "@/lib/voice";

describe("splitSentences com Intl.Segmenter", () => {
  it("não parte em abreviaturas nem em iniciais", () => {
    const sentences = splitSentences("O Sr. Silva chegou às 10h. Falou com a Dra. Ana e com J. Sousa! Correu bem? Sim.");
    expect(sentences).toEqual(["O Sr. Silva chegou às 10h.", "Falou com a Dra. Ana e com J. Sousa!", "Correu bem?", "Sim."]);
  });

  it("segmenta inglês com abreviaturas comuns", () => {
    expect(splitSentences("Dr. Smith arrived at 5 p.m. He left early. Really?", "en")).toHaveLength(3);
  });

  it("tokeniza e reconstrói o texto com quebras de parágrafo", () => {
    const text = "Primeira frase. Segunda frase.\n\nTerceira frase.";
    const tokens = tokenizeSentences(text);
    expect(tokens.filter((t) => t.kind === "break")).toHaveLength(1);
    expect(joinTokens(tokens)).toBe(text);
  });
});

describe("legibilidade", () => {
  it("conta sílabas de forma aproximada", () => {
    expect(countSyllablesPt("casa")).toBe(2);
    expect(countSyllablesPt("computador")).toBe(4);
    expect(countSyllablesPt("saída")).toBe(3);
    expect(countSyllablesEn("table")).toBe(2);
    expect(countSyllablesEn("the")).toBe(1);
  });

  it("dá índices mais altos a texto simples", () => {
    const simple = computeReadability("O gato dorme. O cão come. A casa é branca.", "pt-PT")!;
    const complex = computeReadability(
      "A implementação sistemática de metodologias interdisciplinares, consubstanciada em paradigmas epistemológicos contemporâneos, potencia a operacionalização das estratégias organizacionais.",
      "pt-PT",
    )!;
    expect(simple.flesch).toBeGreaterThan(complex.flesch);
    expect(simple.formula).toContain("Martins");
    expect(computeReadability("Hello world. This is simple.", "en-US")!.formula).toBe("Flesch Reading Ease");
    expect(computeReadability("", "pt-PT")).toBeNull();
  });
});

describe("variante linguística", () => {
  it("deteta marcas brasileiras num texto pedido em pt-PT", () => {
    const markers = detectForeignVariant("O usuário abriu o arquivo no ônibus e estava fazendo o cadastro.", "pt-PT");
    const terms = markers.map((m) => m.term);
    expect(terms).toContain("usuário");
    expect(terms).toContain("ônibus");
    expect(terms.some((t) => t.includes("gerúndio progressivo"))).toBe(true);
  });

  it("deteta marcas europeias num texto pedido em pt-BR", () => {
    const markers = detectForeignVariant("O utilizador está a ver o ecrã do telemóvel.", "pt-BR");
    expect(markers.map((m) => m.term)).toEqual(expect.arrayContaining(["utilizador", "ecrã", "telemóvel"]));
    expect(markers.some((m) => m.kind === "gramática")).toBe(true);
  });

  it("deteta ortografia americana num texto britânico e vice-versa", () => {
    expect(detectForeignVariant("The color of the center was organized.", "en-GB").map((m) => m.term)).toEqual(["color", "center", "organized"]);
    expect(detectForeignVariant("The colour of the centre was organised.", "en-US")).toHaveLength(3);
  });

  it("não assinala texto limpo", () => {
    expect(variantProfile("O utilizador guardou o ficheiro e foi de comboio.", "pt-PT").foreign).toBe(0);
  });
});

describe("padrões típicos de IA", () => {
  it("encontra aberturas, transições em início de frase, vocabulário e estruturas", () => {
    const text = "No mundo atual, a estratégia é crucial. Além disso, permite alavancar sinergias. Não só melhora a eficiência, mas também reduz custos.\n\nEm suma, é fundamental para o sucesso.";
    const hits = findPatterns(text, "pt-PT");
    const categories = new Set(hits.map((h) => h.category));
    expect(categories).toEqual(new Set(["abertura", "transição", "vocabulário", "estrutura", "fecho"]));
    const summary = summarizePatterns(hits, 30, 2);
    expect(summary.total).toBe(hits.length);
    expect(summary.byParagraph).toHaveLength(2);
    expect(termsToAvoid(hits, 0)).toContain("crucial");
  });

  it("só conta transições e fechos em início de frase", () => {
    expect(findPatterns("Foi bom. Além disso, foi rápido.", "pt-PT").some((h) => h.term === "além disso")).toBe(true);
    expect(findPatterns("Foi bom e, além disso, rápido.", "pt-PT").some((h) => h.term === "além disso")).toBe(false);
  });

  it("usa a lista inglesa para en-GB e en-US", () => {
    const hits = findPatterns("In today's fast-paced world, we must delve into the tapestry of data. Moreover, it is a testament to progress.", "en-GB");
    expect(hits.map((h) => h.term.toLowerCase())).toEqual(expect.arrayContaining(["in today's fast-paced world", "delve", "tapestry", "moreover", "testament to"]));
  });
});

describe("métricas de voz", () => {
  it("mede ritmo, pontuação, tratamento e ênclise", () => {
    const sample = [
      "Disse-te que vinha. Não vim, e sei que ficaste chateado; mas a culpa foi do comboio, que se atrasou quase uma hora — como sempre. Tu sabes como é.",
      "Amanhã falamos com calma. Combinado?",
    ];
    const m = computeVoiceMetrics(sample, "pt-PT");
    expect(m.sampleWords).toBeGreaterThan(20);
    expect(m.address).toBe("tu");
    expect(m.enclisisPer100Words).toBeGreaterThan(0);
    expect(m.semicolonsPer100Words).toBeGreaterThan(0);
    expect(m.questionShare).toBeGreaterThan(0);
    expect(m.contractionsPer100Words).toBeNull();
    const description = describeVoiceMetrics(m);
    expect(description).toContain("tratamento do leitor: tu");
    expect(description).toContain("vírgulas");
  });

  it("mede contrações em inglês", () => {
    const m = computeVoiceMetrics(["I don't think we'll make it, but let's try. It's fine."], "en-US");
    expect(m.contractionsPer100Words).toBeGreaterThan(0);
    expect(m.address).toBeNull();
  });
});

describe("alterações frase a frase", () => {
  const base = "A reunião foi longa. Decidimos adiar o projeto. Todos concordaram.\n\nFim.";
  const revised = "A reunião foi longa. Decidimos adiar o projeto para março. Todos concordaram.\n\nFim.";

  it("identifica a substituição e permite rejeitá-la", () => {
    const model = computeChanges(base, revised, "pt");
    expect(model.hunks).toHaveLength(1);
    expect(model.hunks[0].kind).toBe("replace");
    expect(applyDecisions(model, new Set())).toBe(revised);
    expect(applyDecisions(model, new Set([model.hunks[0].id]))).toBe(base);
  });

  it("distingue inserções e remoções", () => {
    const inserted = computeChanges("Uma. Duas.", "Uma. Nova. Duas.", "pt");
    expect(inserted.hunks[0].kind).toBe("insert");
    const deleted = computeChanges("Uma. Duas. Três.", "Uma. Três.", "pt");
    expect(deleted.hunks[0].kind).toBe("delete");
    expect(applyDecisions(deleted, new Set([deleted.hunks[0].id]))).toBe("Uma. Duas. Três.");
  });
});
