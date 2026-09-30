export type AiErrorCode =
  | "not_configured"
  | "auth"
  | "rate_limit"
  | "refusal"
  | "bad_request"
  | "overloaded"
  | "api"
  | "parse"
  | "aborted"
  | "too_long";

export class AiError extends Error {
  code: AiErrorCode;
  status: number;

  constructor(code: AiErrorCode, message: string, status = 500) {
    super(message);
    this.name = "AiError";
    this.code = code;
    this.status = status;
  }
}

/** Mensagens úteis para o utilizador, em português de Portugal. */
export function describeAiError(error: unknown): { code: AiErrorCode; message: string; status: number } {
  if (error instanceof AiError) {
    return { code: error.code, message: error.message, status: error.status };
  }
  if (error instanceof Error && error.name === "AbortError") {
    return { code: "aborted", message: "A operação foi cancelada.", status: 499 };
  }
  return {
    code: "api",
    message: "Ocorreu um erro inesperado ao contactar o fornecedor de IA. Tenta novamente dentro de instantes.",
    status: 500,
  };
}
