import { createRuntime } from "@/lib/ai/runtime";
import { getServerConfig, isConfigured } from "@/lib/config";
import { handleRewrite } from "./handler";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

export async function POST(request: Request) {
  const config = getServerConfig();
  return handleRewrite(request, {
    createRuntime,
    isConfigured: () => isConfigured(config),
    maxInputChars: config.maxInputChars,
  });
}
