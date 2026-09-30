import { NextResponse } from "next/server";
import { getServerConfig, probeProvider, setupProblem } from "@/lib/config";
import type { ConfigStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function GET() {
  const config = getServerConfig();
  const staticProblem = setupProblem(config);
  const probe = staticProblem ? null : await probeProvider(config);
  const problem = staticProblem ?? probe?.problem ?? null;
  const status: ConfigStatus = {
    provider: config.provider,
    ready: problem === null,
    problem,
    model: config.model,
    baseURL: config.baseURL,
    sectionWords: config.sectionWords,
    maxInputChars: config.maxInputChars,
  };
  return NextResponse.json(status, { headers: { "Cache-Control": "no-store" } });
}
