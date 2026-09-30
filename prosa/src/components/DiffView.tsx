"use client";

import { useMemo } from "react";
import { computeWordDiff, diffStats } from "@/lib/diff";

interface Props {
  original: string;
  revised: string;
}

export function DiffView({ original, revised }: Props) {
  const segments = useMemo(() => computeWordDiff(original, revised), [original, revised]);
  const stats = useMemo(() => diffStats(segments), [segments]);

  return (
    <div className="fade-up">
      <div className="mb-3 flex flex-wrap gap-3 text-xs text-muted">
        <span>
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-added align-middle" /> {stats.added} palavras acrescentadas
        </span>
        <span>
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-removed align-middle" /> {stats.removed} removidas
        </span>
        <span>{stats.unchanged} mantidas</span>
      </div>
      <div className="prose-text whitespace-pre-wrap">
        {segments.map((segment, i) => {
          if (segment.kind === "added") {
            return (
              <ins key={i} className="rounded-sm bg-added px-0.5 text-added-fg no-underline">
                {segment.value}
              </ins>
            );
          }
          if (segment.kind === "removed") {
            return (
              <del key={i} className="rounded-sm bg-removed px-0.5 text-removed-fg">
                {segment.value}
              </del>
            );
          }
          return <span key={i}>{segment.value}</span>;
        })}
      </div>
    </div>
  );
}
