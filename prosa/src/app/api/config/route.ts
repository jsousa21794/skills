import { NextResponse } from "next/server";
import { getServerConfig, probeLanguageTool, probeProvider, setupProblem } from "@/lib/config";
import type { ConfigStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  const config = getServerConfig();
  const staticProblem = setupProblem(config);
  const [probe, languageTool] = await Promise.all([
    staticProblem ? Promise.resolve(null) : probeProvider(config),
    config.languageToolUrl ? probeLanguageTool(config.languageToolUrl) : Promise.resolve(false),
  ]);
  const problem = staticProblem ?? probe?.problem ?? null;
  const status: ConfigStatus = {
    provider: config.provider,
    ready: problem === null,
    problem,
    model: config.model,
    baseURL: config.baseURL,
    sectionWords: config.sectionWords,
    maxInputChars: config.maxInputChars,
    contextLength: config.provider === "ollama" ? config.ollamaNumCtx : null,
    modelContextLength: probe?.modelContextLength ?? null,
    embedModel: config.embedModel,
    languageTool,
    autoPolish: config.autoPolish,
  };
  return NextResponse.json(status, { headers: { "Cache-Control": "no-store" } });
}
