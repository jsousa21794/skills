import { NextResponse } from "next/server";
import { getServerConfig, isConfigured } from "@/lib/config";
import type { ConfigStatus } from "@/lib/types";

export const dynamic = "force-dynamic";

export function GET() {
  const config = getServerConfig();
  const status: ConfigStatus = {
    configured: isConfigured(config),
    model: config.model,
    effort: config.effort,
    sectionWords: config.sectionWords,
    maxInputChars: config.maxInputChars,
  };
  return NextResponse.json(status, { headers: { "Cache-Control": "no-store" } });
}
