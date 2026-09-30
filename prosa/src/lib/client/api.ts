import type { ConfigStatus, Language, LanguageToolIssue, RewriteEvent, RewriteRequest, VoiceProfile } from "../types";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(message: string, code = "api", status = 500) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

async function readError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { error?: string; code?: string };
    return new ApiError(body.error ?? `Erro ${response.status}.`, body.code ?? "api", response.status);
  } catch {
    return new ApiError(`O servidor respondeu com o estado ${response.status}.`, "api", response.status);
  }
}

export async function fetchConfig(): Promise<ConfigStatus> {
  const response = await fetch("/api/config", { cache: "no-store" });
  if (!response.ok) throw await readError(response);
  return (await response.json()) as ConfigStatus;
}

/** Lê uma resposta NDJSON e entrega cada evento à medida que chega. */
export async function* parseNdjson<T>(body: ReadableStream<Uint8Array>): AsyncGenerator<T> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) yield JSON.parse(line) as T;
        newline = buffer.indexOf("\n");
      }
    }
    const rest = buffer.trim();
    if (rest) yield JSON.parse(rest) as T;
  } finally {
    reader.releaseLock();
  }
}

export async function* streamRewrite(request: RewriteRequest, signal?: AbortSignal): AsyncGenerator<RewriteEvent> {
  const response = await fetch("/api/rewrite", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw await readError(response);
  if (!response.body) throw new ApiError("O servidor não devolveu conteúdo.", "api", 502);
  yield* parseNdjson<RewriteEvent>(response.body);
}

export async function requestProfile(examples: string[], language: Language, signal?: AbortSignal): Promise<VoiceProfile> {
  const response = await fetch("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ examples, language }),
    signal,
  });
  if (!response.ok) throw await readError(response);
  const body = (await response.json()) as { profile: VoiceProfile };
  return body.profile;
}

export async function requestCheck(text: string, language: Language, signal?: AbortSignal): Promise<LanguageToolIssue[]> {
  const response = await fetch("/api/check", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, language }),
    signal,
  });
  if (!response.ok) throw await readError(response);
  const body = (await response.json()) as { issues: LanguageToolIssue[] };
  return body.issues;
}
