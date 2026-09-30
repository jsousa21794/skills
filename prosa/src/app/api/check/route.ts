import { getServerConfig } from "@/lib/config";
import { checkWithLanguageTool } from "@/lib/languagetool";
import { checkRequestSchema } from "@/lib/schemas";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });
}

/** Verificação gramatical e de variante através de um servidor LanguageTool configurado. */
export async function POST(request: Request) {
  const config = getServerConfig();
  if (!config.languageToolUrl) {
    return json({ error: "Não há servidor LanguageTool configurado. Define LANGUAGETOOL_URL (vê o .env.example).", code: "not_configured" }, 503);
  }
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "O pedido não contém JSON válido.", code: "bad_request" }, 400);
  }
  const parsed = checkRequestSchema.safeParse(payload);
  if (!parsed.success) return json({ error: `Pedido inválido: ${parsed.error.issues[0]?.message}`, code: "bad_request" }, 400);
  try {
    const issues = await checkWithLanguageTool(config.languageToolUrl, parsed.data.text, parsed.data.language, request.signal);
    return json({ issues }, 200);
  } catch (error) {
    return json({ error: error instanceof Error ? error.message : "Falha ao contactar o LanguageTool.", code: "api" }, 502);
  }
}
