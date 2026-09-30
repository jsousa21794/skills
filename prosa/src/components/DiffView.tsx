"use client";

import { useMemo, useState } from "react";
import { computeChanges } from "@/lib/changes";
import { computeWordDiff, diffStats } from "@/lib/diff";
import { Segmented } from "./Segmented";

interface Props {
  original: string;
  revised: string;
  locale: string;
}

const MODES = [
  { value: "words", label: "Palavras" },
  { value: "sentences", label: "Frases" },
] as const;

export function DiffView({ original, revised, locale }: Props) {
  const [mode, setMode] = useState<"words" | "sentences">("words");
  const segments = useMemo(() => computeWordDiff(original, revised), [original, revised]);
  const stats = useMemo(() => diffStats(segments), [segments]);
  const changes = useMemo(() => (mode === "sentences" ? computeChanges(original, revised, locale) : null), [mode, original, revised, locale]);

  return (
    <div className="fade-up">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted">
        <Segmented ariaLabel="Granularidade da comparação" value={mode} options={MODES} onChange={setMode} />
        <span>
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-added align-middle" /> {stats.added} palavras acrescentadas
        </span>
        <span>
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-removed align-middle" /> {stats.removed} removidas
        </span>
        <span>{stats.unchanged} mantidas</span>
      </div>
      {mode === "words" ? (
        <div className="prose-text whitespace-pre-wrap">
          {segments.map((segment, i) =>
            segment.kind === "added" ? (
              <ins key={i} className="rounded-sm bg-added px-0.5 text-added-fg no-underline">
                {segment.value}
              </ins>
            ) : segment.kind === "removed" ? (
              <del key={i} className="rounded-sm bg-removed px-0.5 text-removed-fg">
                {segment.value}
              </del>
            ) : (
              <span key={i}>{segment.value}</span>
            ),
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {changes!.items.map((item, i) =>
            item.kind === "same" ? (
              <p key={i} className="prose-text text-muted">
                {item.tokens.map((t) => (t.kind === "break" ? "\n\n" : t.text + " ")).join("").trim()}
              </p>
            ) : (
              <div key={i} className="grid gap-2 rounded-lg border p-2 sm:grid-cols-2">
                <p className="prose-text text-removed-fg">{item.hunk.from.join(" ").trim() || <span className="text-subtle">(nada)</span>}</p>
                <p className="prose-text text-added-fg">{item.hunk.to.join(" ").trim() || <span className="text-subtle">(removido)</span>}</p>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}
