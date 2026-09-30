import { afterEach, describe, expect, it, vi } from "vitest";
import { getServerConfig, probeProvider, setupProblem } from "@/lib/config";
import { extractJson } from "@/lib/ai/client";

const ENV_KEYS = ["PROSA_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "PROSA_SECTION_WORDS"];

afterEach(() => {
  for (const key of ENV_KEYS) delete process.env[key];
  vi.unstubAllGlobals();
});

describe("getServerConfig", () => {
  it("usa o Ollama por defeito, sem exigir chave", () => {
    const config = getServerConfig();
    expect(config.provider).toBe("ollama");
    expect(config.baseURL).toBe("http://localhost:11434/v1");
    expect(config.sectionWords).toBe(400);
    expect(setupProblem(config)).toBeNull();
  });

  it("exige OPENAI_API_KEY com o fornecedor openai", () => {
    process.env.PROSA_PROVIDER = "openai";
    expect(setupProblem(getServerConfig())).toContain("OPENAI_API_KEY");
    process.env.OPENAI_API_KEY = "sk-teste";
    process.env.OPENAI_BASE_URL = "https://exemplo.local/v1/";
    const config = getServerConfig();
    expect(setupProblem(config)).toBeNull();
    expect(config.baseURL).toBe("https://exemplo.local/v1");
    expect(config.sectionWords).toBe(650);
  });
});

describe("probeProvider", () => {
  it("assinala Ollama inacessível com instruções", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const status = await probeProvider(getServerConfig());
    expect(status.reachable).toBe(false);
    expect(status.problem).toContain("ollama serve");
  });

  it("assinala modelo em falta no Ollama e sugere o pull", async () => {
    process.env.OLLAMA_MODEL = "gemma3:12b";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ data: [{ id: "llama3.1:latest" }] }), { status: 200 })));
    const status = await probeProvider(getServerConfig());
    expect(status.reachable).toBe(true);
    expect(status.modelAvailable).toBe(false);
    expect(status.problem).toContain("ollama pull gemma3:12b");
  });

  it("aceita o modelo quando existe, com ou sem etiqueta", async () => {
    process.env.OLLAMA_MODEL = "llama3.1";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ data: [{ id: "llama3.1:latest" }] }), { status: 200 })));
    const status = await probeProvider(getServerConfig());
    expect(status.problem).toBeNull();
    expect(status.modelAvailable).toBe(true);
  });
});

describe("extractJson", () => {
  it("aceita JSON puro, cercas de código e texto à volta", () => {
    expect(extractJson('{"a":1}')).toEqual({ a: 1 });
    expect(extractJson('```json\n{"a":1}\n```')).toEqual({ a: 1 });
    expect(extractJson('Aqui está:\n{"a":1}\nObrigado.')).toEqual({ a: 1 });
    expect(() => extractJson("nada")).toThrow();
  });
});
