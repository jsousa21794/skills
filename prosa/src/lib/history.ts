import type { Ambiguity, RewriteOptions } from "./types";

export interface Version {
  id: string;
  createdAt: number;
  /** Texto a partir do qual esta versão foi produzida. */
  source: string;
  /** Texto produzido. */
  text: string;
  label: string;
  options: Pick<RewriteOptions, "language" | "mode" | "intensity" | "length">;
  ambiguities: Ambiguity[];
  warnings: string[];
}

export const MAX_VERSIONS = 30;

export function createVersionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `v-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function pushVersion(history: Version[], version: Version): Version[] {
  return [version, ...history].slice(0, MAX_VERSIONS);
}

export function formatTime(timestamp: number, locale = "pt-PT"): string {
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" }).format(
    new Date(timestamp),
  );
}
