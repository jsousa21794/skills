import { describe, expect, it } from "vitest";
import { segmentDocument, tail } from "@/lib/segment";
import { countWords } from "@/lib/text";

const paragraph = (n: number, word = "palavra") => Array.from({ length: n }, (_, i) => `${word}${i}`).join(" ") + ".";

describe("segmentDocument", () => {
  it("devolve uma única secção para textos curtos", () => {
    const segments = segmentDocument("Olá.\n\nAdeus.", 650);
    expect(segments).toHaveLength(1);
    expect(segments[0].text).toBe("Olá.\n\nAdeus.");
  });

  it("agrupa parágrafos até ao limite sem os partir", () => {
    const text = [paragraph(120), paragraph(120), paragraph(120), paragraph(120)].join("\n\n");
    const segments = segmentDocument(text, 250);
    expect(segments).toHaveLength(2);
    for (const segment of segments) {
      expect(segment.words).toBeLessThanOrEqual(250);
      expect(segment.text.split("\n\n")).toHaveLength(2);
    }
  });

  it("parte por frases um parágrafo maior do que o limite", () => {
    const sentences = Array.from({ length: 10 }, (_, i) => `Frase ${i} com algumas palavras dentro.`).join(" ");
    const segments = segmentDocument(sentences, 25);
    expect(segments.length).toBeGreaterThan(1);
    expect(segments.every((s) => countWords(s.text) <= 25)).toBe(true);
    expect(segments.map((s) => s.text).join(" ")).toBe(sentences);
  });

  it("preserva todo o conteúdo pela ordem original", () => {
    const paragraphs = Array.from({ length: 12 }, (_, i) => paragraph(80, `p${i}w`));
    const segments = segmentDocument(paragraphs.join("\n\n"), 200);
    expect(segments.map((s) => s.text).join("\n\n")).toBe(paragraphs.join("\n\n"));
    expect(segments.map((s) => s.index)).toEqual(segments.map((_, i) => i));
  });

  it("tail devolve as últimas palavras com marca de corte", () => {
    expect(tail("a b c d e", 2)).toBe("… d e");
    expect(tail("a b", 5)).toBe("a b");
  });
});
