"use client";

import { useMemo } from "react";
import { computeMetrics, type TextMetrics } from "@/lib/metrics";

interface Props {
  original: string;
  revised: string;
}

function Stat({ label, a, b }: { label: string; a: number | string; b: number | string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className="tabular-nums">
        {a}
        {b !== null && (
          <>
            <span className="mx-1.5 text-subtle">›</span>
            <span className="font-medium">{b}</span>
          </>
        )}
      </span>
    </div>
  );
}

function LengthBars({ lengths, max }: { lengths: number[]; max: number }) {
  if (lengths.length === 0) return <p className="text-xs text-subtle">Sem frases detetadas.</p>;
  return (
    <div className="flex h-10 items-end gap-px" aria-label="Extensão de cada frase, por ordem">
      {lengths.slice(0, 120).map((n, i) => (
        <div
          key={i}
          className="flex-1 rounded-t-sm bg-accent/70"
          style={{ height: `${Math.max(6, (n / Math.max(1, max)) * 100)}%` }}
          title={`Frase ${i + 1}: ${n} palavras`}
        />
      ))}
    </div>
  );
}

function Repetitions({ metrics }: { metrics: TextMetrics }) {
  const seen = new Set<string>();
  const items = [
    ...metrics.repeatedOpeners.map((f) => ({ ...f, kind: "início de frase" })),
    ...metrics.repeatedBigrams.map((f) => ({ ...f, kind: "expressão" })),
    ...metrics.repeatedWords.map((f) => ({ ...f, kind: "palavra" })),
  ]
    .filter((item) => (seen.has(item.term) ? false : (seen.add(item.term), true)))
    .slice(0, 8);
  if (items.length === 0) return <p className="text-xs text-subtle">Sem repetições salientes.</p>;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <li key={`${item.kind}-${item.term}`} className="chip" title={item.kind}>
          <span className="max-w-[10rem] truncate">{item.term}</span>
          <span className="rounded-full bg-elevated px-1.5 text-[10px] tabular-nums text-muted">×{item.count}</span>
        </li>
      ))}
    </ul>
  );
}

export function Indicators({ original, revised }: Props) {
  const a = useMemo(() => computeMetrics(original), [original]);
  const b = useMemo(() => (revised ? computeMetrics(revised) : null), [revised]);
  const max = Math.max(a.longestSentence, b?.longestSentence ?? 0, 1);

  return (
    <section className="surface p-4" aria-labelledby="indicators-title">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 id="indicators-title" className="text-sm font-semibold">
          Indicadores
        </h2>
        <span className="text-[11px] text-subtle">original {b ? "› revisto" : ""}</span>
      </div>
      <div className="divide-y divide-line">
        <Stat label="Palavras" a={a.words} b={b?.words ?? null} />
        <Stat label="Frases" a={a.sentences} b={b?.sentences ?? null} />
        <Stat label="Parágrafos" a={a.paragraphs} b={b?.paragraphs ?? null} />
        <Stat label="Média de palavras por frase" a={a.averageSentenceLength} b={b?.averageSentenceLength ?? null} />
        <Stat label="Frase mais longa" a={a.longestSentence} b={b?.longestSentence ?? null} />
      </div>
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <p className="label">Ritmo · original</p>
          <LengthBars lengths={a.sentenceLengths} max={max} />
          <div className="mt-3">
            <p className="label">Repetições · original</p>
            <Repetitions metrics={a} />
          </div>
        </div>
        {b && (
          <div>
            <p className="label">Ritmo · revisto</p>
            <LengthBars lengths={b.sentenceLengths} max={max} />
            <div className="mt-3">
              <p className="label">Repetições · revisto</p>
              <Repetitions metrics={b} />
            </div>
          </div>
        )}
      </div>
      <p className="mt-4 text-[11px] leading-relaxed text-subtle">
        Estes indicadores medem apenas o texto: extensão, ritmo e repetições. A Prosa não calcula percentagens de «escrita humana» nem promete resultados em detetores de IA, porque essas medidas não são fiáveis.
      </p>
    </section>
  );
}
