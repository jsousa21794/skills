import { describe, expect, it } from "vitest";
import { computeMetrics } from "@/lib/metrics";

describe("computeMetrics", () => {
  it("calcula contagens básicas", () => {
    const m = computeMetrics("Primeira frase curta. Segunda frase um pouco mais longa do que a primeira.\n\nTerceira.");
    expect(m.words).toBe(14);
    expect(m.sentences).toBe(3);
    expect(m.paragraphs).toBe(2);
    expect(m.longestSentence).toBe(10);
    expect(m.shortestSentence).toBe(1);
  });

  it("deteta repetições de palavras, expressões e aberturas", () => {
    const text =
      "Além disso, a estratégia digital funciona. Além disso, a estratégia digital cresce. Além disso, a estratégia digital convence.";
    const m = computeMetrics(text);
    expect(m.repeatedOpeners[0]).toEqual({ term: "além disso", count: 3 });
    expect(m.repeatedBigrams.some((b) => b.term === "estratégia digital" && b.count === 3)).toBe(true);
    expect(m.repeatedWords.some((w) => w.term === "estratégia")).toBe(true);
  });

  it("lida com texto vazio", () => {
    const m = computeMetrics("");
    expect(m.words).toBe(0);
    expect(m.averageSentenceLength).toBe(0);
    expect(m.repeatedWords).toEqual([]);
  });
});
