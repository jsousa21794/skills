import { afterEach, describe, expect, it, vi } from "vitest";
import { checkWithLanguageTool } from "@/lib/languagetool";
import { buildSystemPrompt } from "@/lib/prompt";

afterEach(() => vi.unstubAllGlobals());

describe("checkWithLanguageTool", () => {
  it("envia o texto em formulário e normaliza as ocorrências", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      const body = init?.body as URLSearchParams;
      expect(body.get("language")).toBe("pt-PT");
      expect(body.get("text")).toBe("O usuário chegou.");
      return new Response(
        JSON.stringify({
          matches: [
            {
              message: "Palavra do português do Brasil.",
              shortMessage: "Brasileirismo",
              offset: 2,
              length: 7,
              replacements: [{ value: "utilizador" }],
              rule: { id: "PT_BR_WORDS", category: { id: "REGIONALISMS", name: "Regionalismos" } },
              context: { text: "O usuário chegou.", offset: 2, length: 7 },
            },
          ],
        }),
        { status: 200 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    const issues = await checkWithLanguageTool("http://lt.local", "O usuário chegou.", "pt-PT");
    expect(issues).toEqual([
      { message: "Palavra do português do Brasil.", shortMessage: "Brasileirismo", offset: 2, length: 7, category: "Regionalismos", ruleId: "PT_BR_WORDS", replacements: ["utilizador"], context: "O usuário chegou." },
    ]);
  });

  it("divide textos longos em blocos e ajusta os desvios", async () => {
    const calls: number[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        const text = (init?.body as URLSearchParams).get("text") ?? "";
        calls.push(text.length);
        return new Response(JSON.stringify({ matches: [{ message: "x", offset: 1, length: 1, rule: {}, context: {} }] }), { status: 200 });
      }),
    );
    const paragraph = "a".repeat(5000);
    const text = [paragraph, paragraph, paragraph, paragraph].join("\n\n");
    const issues = await checkWithLanguageTool("http://lt.local", text, "en-GB");
    expect(calls.length).toBeGreaterThan(1);
    expect(issues[0].offset).toBe(1);
    expect(issues[1].offset).toBeGreaterThan(5000);
  });

  it("propaga erros do servidor com o estado", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("boom", { status: 500 })));
    await expect(checkWithLanguageTool("http://lt.local", "x", "en-US")).rejects.toThrow(/500/);
  });
});

describe("perfil com métricas e guia de estilo no prompt", () => {
  it("injeta métricas, palavras proibidas e substituições", () => {
    const prompt = buildSystemPrompt({
      language: "pt-PT",
      mode: "personalizado",
      intensity: "ligeira",
      length: "manter",
      profile: {
        name: "x",
        vocabulary: "",
        rhythm: "",
        formality: "",
        sentenceStructure: "",
        avoid: "",
        avoidWords: ["alavancar", "sinergia"],
        preferredTerms: [{ from: "feedback", to: "comentários" }],
        metrics: {
          sampleWords: 300,
          averageSentenceLength: 14.2,
          sentenceLengthStdDev: 9.5,
          longSentenceShare: 0.1,
          shortSentenceShare: 0.2,
          averageParagraphWords: 60,
          commasPer100Words: 6,
          semicolonsPer100Words: 0.5,
          colonsPer100Words: 0.3,
          dashesPer100Words: 0.8,
          parenthesesPer100Words: 0.2,
          exclamationShare: 0,
          questionShare: 0.1,
          firstPersonPer100Words: 3,
          typeTokenRatio: 0.6,
          connectors: [{ term: "mas", count: 5 }],
          address: "tu",
          enclisisPer100Words: 1.2,
          contractionsPer100Words: null,
        },
      },
    });
    expect(prompt).toContain("Métricas observadas");
    expect(prompt).toContain("14.2 palavras");
    expect(prompt).toContain("«alavancar», «sinergia»");
    expect(prompt).toContain("«feedback» → «comentários»");
    expect(prompt).toContain("tratamento do leitor: tu");
  });
});
