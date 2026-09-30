import type { Generate } from "@/lib/ai/client";
import { describeAiError } from "@/lib/ai/errors";
import { buildVoiceProfile } from "@/lib/ai/profile";
import { profileRequestSchema } from "@/lib/schemas";

export interface ProfileHandlerDeps {
  generate: () => Generate;
  setupProblem: () => string | null;
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export async function handleProfile(request: Request, deps: ProfileHandlerDeps): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "O pedido não contém JSON válido.", code: "bad_request" }, 400);
  }
  const parsed = profileRequestSchema.safeParse(payload);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    return json({ error: `Pedido inválido: ${first?.message}`, code: "bad_request" }, 400);
  }
  const problem = deps.setupProblem();
  if (problem) {
    return json({ error: problem, code: "not_configured" }, 503);
  }
  try {
    const profile = await buildVoiceProfile(parsed.data.examples, parsed.data.language, deps.generate(), request.signal);
    return json({ profile }, 200);
  } catch (error) {
    const described = describeAiError(error);
    return json({ error: described.message, code: described.code }, described.status >= 400 && described.status < 600 ? described.status : 500);
  }
}
