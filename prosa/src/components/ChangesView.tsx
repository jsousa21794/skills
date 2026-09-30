"use client";

import { useMemo } from "react";
import { applyDecisions, computeChanges } from "@/lib/changes";
import { computeWordDiff } from "@/lib/diff";
import { IconCheck, IconUndo } from "./Icons";

interface Props {
  base: string;
  revised: string;
  locale: string;
  rejected: Set<number>;
  onToggle: (id: number, rejected: boolean) => void;
  onApply: (text: string) => void;
}

function InlineDiff({ from, to }: { from: string; to: string }) {
  const segments = useMemo(() => computeWordDiff(from, to), [from, to]);
  return (
    <span>
      {segments.map((s, i) =>
        s.kind === "added" ? (
          <ins key={i} className="rounded-sm bg-added px-0.5 text-added-fg no-underline">
            {s.value}
          </ins>
        ) : s.kind === "removed" ? (
          <del key={i} className="rounded-sm bg-removed px-0.5 text-removed-fg">
            {s.value}
          </del>
        ) : (
          <span key={i}>{s.value}</span>
        ),
      )}
    </span>
  );
}

/** Alterações frase a frase, com a possibilidade de manter a frase original em cada uma. */
export function ChangesView({ base, revised, locale, rejected, onToggle, onApply }: Props) {
  const model = useMemo(() => computeChanges(base, revised, locale), [base, revised, locale]);
  const changed = model.hunks.length;
  const kept = rejected.size;

  if (changed === 0) {
    return <p className="prose-text text-subtle">Não há diferenças ao nível da frase entre o texto revisto e {"a base de comparação"}.</p>;
  }

  return (
    <div className="fade-up">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>
          {changed} {changed === 1 ? "alteração" : "alterações"}
          {kept > 0 && `, ${kept} ${kept === 1 ? "rejeitada" : "rejeitadas"}`}
        </span>
        {kept > 0 && (
          <button type="button" className="btn btn-sm btn-primary ml-auto" onClick={() => onApply(applyDecisions(model, rejected))}>
            <IconCheck size={12} /> Aplicar decisões ao texto revisto
          </button>
        )}
      </div>
      <div className="prose-text">
        {model.items.map((item, i) => {
          if (item.kind === "same") {
            return (
              <span key={i}>
                {item.tokens.map((t, j) => (t.kind === "break" ? <span key={j} className="block h-4" /> : <span key={j}>{t.text} </span>))}
              </span>
            );
          }
          const { hunk } = item;
          const isRejected = rejected.has(hunk.id);
          const from = hunk.from.join(" ").replace(/\s*\n\n\s*/g, " ¶ ");
          const to = hunk.to.join(" ").replace(/\s*\n\n\s*/g, " ¶ ");
          return (
            <span key={i} className={`group relative -mx-1 inline rounded px-1 ${isRejected ? "bg-removed/40" : "bg-accent-soft/40"}`}>
              {isRejected ? <span>{from} </span> : hunk.kind === "replace" ? <InlineDiff from={from} to={to} /> : hunk.kind === "insert" ? <ins className="bg-added text-added-fg no-underline">{to}</ins> : <del className="bg-removed text-removed-fg">{from}</del>}{" "}
              <button
                type="button"
                className="btn btn-sm relative -top-0.5 mx-0.5 inline-flex align-middle font-sans text-[11px]"
                title={isRejected ? "Aceitar a alteração" : "Manter a versão original desta frase"}
                onClick={() => onToggle(hunk.id, !isRejected)}
              >
                {isRejected ? <IconCheck size={11} /> : <IconUndo size={11} />} {isRejected ? "Aceitar" : "Manter original"}
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}
