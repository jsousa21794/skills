import { createGenerate } from "@/lib/ai/client";
import { getServerConfig, setupProblem } from "@/lib/config";
import { handleProfile } from "./handler";

export const dynamic = "force-dynamic";
export const maxDuration = 120;

export async function POST(request: Request) {
  const config = getServerConfig();
  return handleProfile(request, {
    generate: () => createGenerate(config),
    setupProblem: () => setupProblem(config),
  });
}
