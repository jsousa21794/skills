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
  /** Versões marcadas não são descartadas quando o histórico atinge o limite. */
  pinned?: boolean;
}

export const MAX_VERSIONS = 30;

export function createVersionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `v-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function pushVersion(history: Version[], version: Version): Version[] {
  const next = [version, ...history];
  if (next.length <= MAX_VERSIONS) return next;
  const pinned = next.filter((v) => v.pinned);
  const unpinned = next.filter((v) => !v.pinned).slice(0, Math.max(0, MAX_VERSIONS - pinned.length));
  return next.filter((v) => pinned.includes(v) || unpinned.includes(v));
}

export function formatTime(timestamp: number, locale = "pt-PT"): string {
  return new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" }).format(
    new Date(timestamp),
  );
}
