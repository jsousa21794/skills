import { describe, expect, it } from "vitest";
import { countWords, splitParagraphs, splitSentences } from "@/lib/text";

describe("text utilities", () => {
  it("conta palavras com acentos, hífens e apóstrofos", () => {
    expect(countWords("O utilizador está-se a rir. Don't stop!")).toBe(7);
    expect(countWords("")).toBe(0);
    expect(countWords("   \n  ")).toBe(0);
  });

  it("divide parágrafos por linhas em branco, ignorando vazios", () => {
    expect(splitParagraphs("Um.\n\nDois.\r\n\r\n\n  \nTrês.")).toEqual(["Um.", "Dois.", "Três."]);
  });

  it("divide frases de forma aproximada", () => {
    const sentences = splitSentences("A reunião foi às 10h. Correu bem! Vamos repetir? Sim.");
    expect(sentences).toHaveLength(4);
    expect(sentences[0]).toBe("A reunião foi às 10h.");
  });
});
