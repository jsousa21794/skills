import { describeAiError } from "@/lib/ai/errors";
import { rewriteDocument, type RewriteRuntime } from "@/lib/ai/rewrite";
import { rewriteRequestSchema } from "@/lib/schemas";
import type { RewriteEvent } from "@/lib/types";

export interface RewriteHandlerDeps {
  createRuntime: (signal?: AbortSignal) => RewriteRuntime;
  /** Devolve uma mensagem quando falta configuração, ou null quando está tudo definido. */
  setupProblem: () => string | null;
  maxInputChars: number;
}

function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

/**
 * Handler do pedido de reescrita, separado da rota para poder ser testado
 * com um runtime simulado. Responde em NDJSON: um evento por linha.
 */
export async function handleRewrite(request: Request, deps: RewriteHandlerDeps): Promise<Response> {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "O pedido não contém JSON válido.", code: "bad_request" }, 400);
  }

  const parsed = rewriteRequestSchema.safeParse(payload);
  if (!parsed.success) {
    const first = parsed.error.issues[0];
    return json({ error: `Pedido inválido: ${first?.path.join(".") || "corpo"} — ${first?.message}`, code: "bad_request" }, 400);
  }

  if (parsed.data.text.length > deps.maxInputChars) {
    return json(
      {
        error: `O texto tem ${parsed.data.text.length} caracteres e o limite é ${deps.maxInputChars}. Divide-o em partes ou aumenta PROSA_MAX_INPUT_CHARS.`,
        code: "too_long",
      },
      413,
    );
  }

  const problem = deps.setupProblem();
  if (problem) {
    return json({ error: problem, code: "not_configured" }, 503);
  }

  const encoder = new TextEncoder();
  const runtime = deps.createRuntime(request.signal);

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: RewriteEvent) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
      try {
        for await (const event of rewriteDocument(parsed.data, runtime)) {
          send(event);
        }
      } catch (error) {
        const described = describeAiError(error);
        send({ type: "error", message: described.message, code: described.code });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Accel-Buffering": "no",
    },
  });
}
