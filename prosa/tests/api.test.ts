import { describe, expect, it } from "vitest";
import { handleRewrite } from "@/app/api/rewrite/handler";
import { handleProfile } from "@/app/api/profile/handler";
import { AiError } from "@/lib/ai/errors";
import { parseNdjson } from "@/lib/client/api";
import type { RewriteEvent } from "@/lib/types";
import { extractText, fakeGenerate } from "./helpers/fakeGenerate";

const validBody = { text: "Olá mundo.", language: "pt-PT", mode: "profissional", intensity: "ligeira", length: "manter" };

function post(url: string, body: unknown): Request {
  return new Request(`http://localhost${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

function deps(overrides: Partial<Parameters<typeof handleRewrite>[1]> = {}) {
  const { generate } = fakeGenerate((call) => ({ rewritten: extractText(call.user).toUpperCase(), ambiguities: [], terminology: [] }));
  return {
    createRuntime: (signal?: AbortSignal) => ({ generate, model: "modelo-teste", sectionWords: 650, signal }),
    setupProblem: () => null,
    maxInputChars: 1000,
    ...overrides,
  };
}

async function events(response: Response): Promise<RewriteEvent[]> {
  const out: RewriteEvent[] = [];
  for await (const event of parseNdjson<RewriteEvent>(response.body!)) out.push(event);
  return out;
}

describe("POST /api/rewrite", () => {
  it("rejeita JSON inválido", async () => {
    const response = await handleRewrite(post("/api/rewrite", "{nope"), deps());
    expect(response.status).toBe(400);
  });

  it("rejeita pedidos com campos inválidos com mensagem útil", async () => {
    const response = await handleRewrite(post("/api/rewrite", { ...validBody, language: "fr-FR" }), deps());
    expect(response.status).toBe(400);
    const body = await response.json();
    expect(body.code).toBe("bad_request");
    expect(body.error).toContain("language");
  });

  it("rejeita texto acima do limite", async () => {
    const response = await handleRewrite(post("/api/rewrite", { ...validBody, text: "x".repeat(1001) }), deps());
    expect(response.status).toBe(413);
  });

  it("explica como configurar o fornecedor quando falta, sem simular resultados", async () => {
    const response = await handleRewrite(
      post("/api/rewrite", validBody),
      deps({ setupProblem: () => "Falta a chave da API. Define OPENAI_API_KEY em .env.local." }),
    );
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.code).toBe("not_configured");
    expect(body.error).toContain("OPENAI_API_KEY");
  });

  it("devolve um fluxo NDJSON com progresso e resultado", async () => {
    const response = await handleRewrite(post("/api/rewrite", validBody), deps());
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toContain("application/x-ndjson");
    const list = await events(response);
    expect(list[0]).toEqual({ type: "start", sections: 1, model: "modelo-teste" });
    const done = list.at(-1);
    expect(done?.type).toBe("done");
    expect(done?.type === "done" && done.result.rewritten).toBe("OLÁ MUNDO.");
  });

  it("converte erros do fornecedor num evento de erro legível", async () => {
    const response = await handleRewrite(
      post("/api/rewrite", validBody),
      deps({
        createRuntime: () => ({
          generate: async () => {
            throw new AiError("rate_limit", "Limite de pedidos atingido.", 429);
          },
          model: "m",
          sectionWords: 650,
        }),
      }),
    );
    const list = await events(response);
    expect(list.at(-1)).toEqual({ type: "error", message: "Limite de pedidos atingido.", code: "rate_limit" });
  });
});

describe("POST /api/profile", () => {
  it("cria um perfil a partir de exemplos", async () => {
    const { generate, calls } = fakeGenerate(() => ({
      vocabulary: "simples",
      rhythm: "curto",
      formality: "informal",
      sentenceStructure: "direta",
      avoid: "gerúndios",
    }));
    const response = await handleProfile(
      post("/api/profile", { examples: ["Este é um exemplo suficientemente longo da minha escrita."], language: "pt-PT" }),
      { generate: () => generate, setupProblem: () => null },
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.profile.vocabulary).toBe("simples");
    expect(body.profile.name).toBe("O meu perfil");
    expect(calls[0].user).toContain("<exemplos>");
  });

  it("valida exemplos demasiado curtos", async () => {
    const response = await handleProfile(post("/api/profile", { examples: ["curto"], language: "pt-PT" }), {
      generate: () => fakeGenerate(() => ({})).generate,
      setupProblem: () => null,
    });
    expect(response.status).toBe(400);
  });

  it("devolve 503 quando falta configuração", async () => {
    const response = await handleProfile(post("/api/profile", { examples: ["Exemplo com mais de vinte caracteres."], language: "pt-PT" }), {
      generate: () => fakeGenerate(() => ({})).generate,
      setupProblem: () => "Falta OPENAI_API_KEY.",
    });
    expect(response.status).toBe(503);
  });
});
