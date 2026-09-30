import { describe, expect, it } from "vitest";
import { rewriteDocument } from "@/lib/ai/rewrite";
import type { RewriteEvent, RewriteRequest } from "@/lib/types";
import { extractText, fakeGenerate } from "./helpers/fakeGenerate";

const base: RewriteRequest = { text: "", language: "pt-PT", mode: "profissional", intensity: "moderada", length: "manter" };

async function collect(gen: AsyncGenerator<RewriteEvent>): Promise<RewriteEvent[]> {
  const events: RewriteEvent[] = [];
  for await (const event of gen) events.push(event);
  return events;
}

function done(events: RewriteEvent[]) {
  const last = events.at(-1);
  if (!last || last.type !== "done") throw new Error("sem evento done");
  return last.result;
}

describe("re-revisão automática de parágrafos", () => {
  it("re-revê um parágrafo com marcas de outra variante e aceita a versão melhor", async () => {
    const { generate, calls } = fakeGenerate((call) => {
      const text = extractText(call.user);
      // Primeira chamada: secção inteira, com um parágrafo brasileiro. Segunda: polimento.
      if (call.user.includes("Revê de novo apenas este parágrafo")) {
        return { rewritten: text.replace("usuário", "utilizador").replace("ônibus", "autocarro"), ambiguities: [], terminology: [] };
      }
      return { rewritten: "Primeiro parágrafo limpo.\n\nO usuário apanhou o ônibus.", ambiguities: [], terminology: [] };
    });
    const events = await collect(rewriteDocument({ ...base, text: "Primeiro parágrafo.\n\nSegundo parágrafo." }, { generate, model: "m", sectionWords: 650, autoPolish: true }));
    expect(calls).toHaveLength(2);
    expect(calls[1].user).toContain("marcas de outra variante");
    expect(calls[1].user).toContain("«usuário»");
    const result = done(events);
    expect(result.rewritten).toBe("Primeiro parágrafo limpo.\n\nO utilizador apanhou o autocarro.");
    expect(result.polishedParagraphs).toBe(1);
    expect(result.checks[1].polished).toBe(true);
    expect(result.checks[1].foreignVariant).toEqual([]);
    expect(events.some((e) => e.type === "status" && e.message.includes("parágrafo 2"))).toBe(true);
  });

  it("rejeita o polimento quando não melhora ou quebra um termo bloqueado", async () => {
    const { generate, calls } = fakeGenerate((call) => {
      if (call.user.includes("Revê de novo")) return { rewritten: "No mundo atual, a Empresa X é crucial e fundamental para tudo.", ambiguities: [], terminology: [] };
      return { rewritten: "No mundo atual, a ACME é crucial. Além disso, alavanca sinergias.", ambiguities: [], terminology: [] };
    });
    const events = await collect(rewriteDocument({ ...base, text: "A ACME é importante.", lockedTerms: ["ACME"] }, { generate, model: "m", sectionWords: 650, autoPolish: true }));
    expect(calls).toHaveLength(2);
    const result = done(events);
    expect(result.rewritten).toContain("ACME");
    expect(result.polishedParagraphs).toBe(0);
    expect(result.checks[0].patterns).toBeGreaterThanOrEqual(2);
  });

  it("não re-revê quando a opção está desligada", async () => {
    const { generate, calls } = fakeGenerate(() => ({ rewritten: "No mundo atual, isto é crucial. Além disso, é fundamental.", ambiguities: [], terminology: [] }));
    await collect(rewriteDocument({ ...base, text: "Isto importa." }, { generate, model: "m", sectionWords: 650, autoPolish: false }));
    expect(calls).toHaveLength(1);
  });

  it("respeita o orçamento de parágrafos por secção", async () => {
    const { generate, calls } = fakeGenerate((call) => {
      if (call.user.includes("Revê de novo")) return { rewritten: "Parágrafo limpo.", ambiguities: [], terminology: [] };
      return { rewritten: Array.from({ length: 5 }, () => "No mundo atual, isto é crucial e fundamental.").join("\n\n"), ambiguities: [], terminology: [] };
    });
    await collect(rewriteDocument({ ...base, text: "a\n\nb\n\nc\n\nd\n\ne" }, { generate, model: "m", sectionWords: 650, autoPolish: true, maxPolishPerSection: 2 }));
    expect(calls).toHaveLength(3);
  });
});

describe("verificação de sentido por embeddings", () => {
  const vec = (x: number, y: number) => [x, y];

  it("calcula a semelhança por parágrafo e avisa abaixo do limiar", async () => {
    const { generate } = fakeGenerate(() => ({ rewritten: "Um parágrafo.\n\nOutro parágrafo diferente.", ambiguities: [], terminology: [] }));
    const embed = async (texts: string[]) => texts.map((t, i) => (i < 2 ? vec(1, 0) : i === 2 ? vec(1, 0) : vec(0, 1)));
    const events = await collect(rewriteDocument({ ...base, text: "Um.\n\nOutro." }, { generate, model: "m", sectionWords: 650, autoPolish: false, embed, similarityMin: 0.72 }));
    const result = done(events);
    expect(result.meaningCheck).toBe("ok");
    expect(result.checks.map((c) => c.similarity)).toEqual([1, 0]);
    expect(result.warnings.some((w) => w.includes("parágrafo 2") && w.includes("semelhança 0"))).toBe(true);
  });

  it("continua sem falhar quando os embeddings não estão disponíveis", async () => {
    const { generate } = fakeGenerate(() => ({ rewritten: "Texto.", ambiguities: [], terminology: [] }));
    const embed = async () => {
      throw new Error("modelo bge-m3 não instalado");
    };
    const result = done(await collect(rewriteDocument({ ...base, text: "Texto." }, { generate, model: "m", sectionWords: 650, autoPolish: false, embed })));
    expect(result.meaningCheck).toBe("unavailable");
    expect(result.warnings.some((w) => w.includes("bge-m3"))).toBe(true);
    expect(result.rewritten).toBe("Texto.");
  });

  it("reporta off quando não há função de embeddings", async () => {
    const { generate } = fakeGenerate(() => ({ rewritten: "Texto.", ambiguities: [], terminology: [] }));
    const result = done(await collect(rewriteDocument({ ...base, text: "Texto." }, { generate, model: "m", sectionWords: 650, autoPolish: false })));
    expect(result.meaningCheck).toBe("off");
    expect(result.checks[0].similarity).toBeNull();
  });
});
