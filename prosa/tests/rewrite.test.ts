import { describe, expect, it } from "vitest";
import { rewriteDocument } from "@/lib/ai/rewrite";
import type { RewriteEvent, RewriteRequest } from "@/lib/types";
import { extractText, fakeGenerate } from "./helpers/fakeGenerate";

const base: RewriteRequest = { text: "", language: "pt-PT", mode: "profissional", intensity: "ligeira", length: "manter" };

async function collect(gen: AsyncGenerator<RewriteEvent>): Promise<RewriteEvent[]> {
  const events: RewriteEvent[] = [];
  for await (const event of gen) events.push(event);
  return events;
}

const paragraph = (n: number, tag: string) => Array.from({ length: n }, (_, i) => `${tag}${i}`).join(" ") + ".";

describe("rewriteDocument", () => {
  it("processa um texto curto numa única secção e emite os eventos esperados", async () => {
    const { generate, calls } = fakeGenerate((call) => ({
      rewritten: extractText(call.user).toUpperCase(),
      ambiguities: [{ excerpt: "isto", note: "referente pouco claro" }],
      terminology: [],
    }));
    const events = await collect(rewriteDocument({ ...base, text: "Olá mundo." }, { generate, model: "m", sectionWords: 650 }));
    expect(events.map((e) => e.type)).toEqual(["start", "progress", "section", "progress", "done"]);
    const done = events.at(-1);
    expect(done?.type === "done" && done.result.rewritten).toBe("OLÁ MUNDO.");
    expect(done?.type === "done" && done.result.ambiguities).toHaveLength(1);
    expect(calls).toHaveLength(1);
    expect(calls[0].system).toContain("Português de Portugal");
  });

  it("divide documentos longos, mantém a ordem e passa contexto e terminologia entre secções", async () => {
    const text = [paragraph(100, "a"), paragraph(100, "b"), paragraph(100, "c")].join("\n\n");
    const { generate, calls } = fakeGenerate((call, i) => ({
      rewritten: `[S${i}] ` + extractText(call.user),
      ambiguities: [],
      terminology: i === 0 ? [{ source: "stakeholders", target: "partes interessadas" }] : [],
    }));
    const events = await collect(rewriteDocument({ ...base, text }, { generate, model: "m", sectionWords: 150 }));
    const start = events[0];
    expect(start.type === "start" && start.sections).toBe(3);
    expect(calls).toHaveLength(3);
    expect(calls[0].user).toContain("excerto 1 de 3");
    expect(calls[1].user).toContain("<contexto_anterior>");
    expect(calls[1].user).toContain("[S0]");
    expect(calls[1].user).toContain("«stakeholders» → «partes interessadas»");
    expect(calls[2].user).toContain("«stakeholders» → «partes interessadas»");
    const done = events.at(-1);
    expect(done?.type === "done" && done.result.sections).toBe(3);
    expect(done?.type === "done" && done.result.rewritten.startsWith("[S0] a0")).toBe(true);
    expect(done?.type === "done" && done.result.rewritten).toContain("\n\n[S2] c0");
    const progress = events.filter((e) => e.type === "progress");
    expect(progress.at(-1)).toEqual({ type: "progress", completed: 3, total: 3 });
  });

  it("repete uma vez quando um termo bloqueado é alterado e avisa se persistir", async () => {
    const { generate, calls } = fakeGenerate((_call, i) => ({
      rewritten: i === 0 ? "A empresa cresceu." : "A empresa cresceu bastante.",
      ambiguities: [],
      terminology: [],
    }));
    const events = await collect(
      rewriteDocument({ ...base, text: "A ACME cresceu.", lockedTerms: ["ACME"] }, { generate, model: "m", sectionWords: 650 }),
    );
    expect(calls).toHaveLength(2);
    expect(calls[1].user).toContain("não ficaram intactos: «ACME»");
    const done = events.at(-1);
    expect(done?.type === "done" && done.result.warnings.some((w) => w.includes("«ACME»"))).toBe(true);
  });

  it("aceita a segunda tentativa quando o termo bloqueado é reposto", async () => {
    const { generate, calls } = fakeGenerate((_call, i) => ({
      rewritten: i === 0 ? "A empresa cresceu." : "A ACME cresceu.",
      ambiguities: [],
      terminology: [],
    }));
    const events = await collect(
      rewriteDocument({ ...base, text: "A ACME cresceu.", lockedTerms: ["ACME"] }, { generate, model: "m", sectionWords: 650 }),
    );
    expect(calls).toHaveLength(2);
    const done = events.at(-1);
    expect(done?.type === "done" && done.result.warnings).toEqual([]);
  });

  it("passa o contexto de parágrafo apenas em secção única", async () => {
    const { generate, calls } = fakeGenerate((call) => ({ rewritten: extractText(call.user), ambiguities: [], terminology: [] }));
    await collect(
      rewriteDocument({ ...base, text: "Parágrafo alvo.", context: { before: "Antes.", after: "Depois." } }, { generate, model: "m", sectionWords: 650 }),
    );
    expect(calls[0].user).toContain("<contexto_anterior>\nAntes.\n</contexto_anterior>");
    expect(calls[0].user).toContain("<contexto_seguinte>\nDepois.\n</contexto_seguinte>");
  });

  it("emite avisos de preservação quando números desaparecem", async () => {
    const { generate } = fakeGenerate(() => ({ rewritten: "Crescemos muito.", ambiguities: [], terminology: [] }));
    const events = await collect(rewriteDocument({ ...base, text: "Crescemos 12% em 2020." }, { generate, model: "m", sectionWords: 650 }));
    const done = events.at(-1);
    expect(done?.type === "done" && done.result.warnings.length).toBeGreaterThan(0);
  });

  it("interrompe quando o sinal é cancelado", async () => {
    const controller = new AbortController();
    const { generate } = fakeGenerate((call) => ({ rewritten: extractText(call.user), ambiguities: [], terminology: [] }));
    controller.abort();
    await expect(
      collect(rewriteDocument({ ...base, text: "Olá." }, { generate, model: "m", sectionWords: 650, signal: controller.signal })),
    ).rejects.toThrow();
  });
});
