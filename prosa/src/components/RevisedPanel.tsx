"use client";

import { useState } from "react";
import { splitParagraphs } from "@/lib/text";
import type { ParagraphCheck } from "@/lib/types";
import { ChangesView } from "./ChangesView";
import { DiffView } from "./DiffView";
import { IconCheck, IconCopy, IconDownload, IconRefresh, IconUndo } from "./Icons";
import { Segmented } from "./Segmented";

export type RevisedView = "text" | "changes" | "compare" | "edit";

interface Props {
  revised: string;
  compareWith: string;
  compareLabel: string;
  locale: string;
  busy: boolean;
  busyParagraph: number | null;
  checks: ParagraphCheck[];
  similarityMin: number;
  view: RevisedView;
  onViewChange: (view: RevisedView) => void;
  onReviseParagraph: (index: number) => void;
  onEdit: (text: string) => void;
  onRestoreOriginal: () => void;
  onDownloadTxt: () => void;
  onDownloadDocx: () => void;
  rejectedChanges: Set<number>;
  onToggleChange: (id: number, rejected: boolean) => void;
  onApplyChanges: (text: string) => void;
}

const VIEWS = [
  { value: "text", label: "Texto" },
  { value: "changes", label: "Alterações" },
  { value: "compare", label: "Comparar" },
  { value: "edit", label: "Editar" },
] as const;

export function RevisedPanel(props: Props) {
  const { revised, compareWith, compareLabel, locale, busy, busyParagraph, checks, similarityMin, view, onViewChange } = props;
  const [copied, setCopied] = useState(false);
  const paragraphs = splitParagraphs(revised);
  const hasRevision = revised.trim().length > 0;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(revised);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Sem permissão de clipboard: o utilizador pode selecionar o texto manualmente.
    }
  };

  return (
    <section className="surface flex min-h-[20rem] flex-col" aria-labelledby="revised-title">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-2.5">
        <div className="flex items-center gap-3">
          <h2 id="revised-title" className="text-sm font-semibold">
            Revisto
          </h2>
          {hasRevision && <Segmented ariaLabel="Vista do texto revisto" value={view} options={VIEWS} onChange={onViewChange} />}
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <button type="button" className="btn btn-ghost btn-sm" onClick={copy} disabled={!hasRevision} title="Copiar texto revisto">
            {copied ? <IconCheck size={14} /> : <IconCopy size={14} />} {copied ? "Copiado" : "Copiar"}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={props.onDownloadTxt} disabled={!hasRevision} title="Descarregar como ficheiro de texto">
            <IconDownload size={14} /> .txt
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={props.onDownloadDocx} disabled={!hasRevision} title="Descarregar .docx com alterações registadas face ao original">
            <IconDownload size={14} /> .docx com alterações
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={props.onRestoreOriginal} disabled={!hasRevision || busy} title="Substituir a revisão pelo texto original">
            <IconUndo size={14} /> Restaurar original
          </button>
        </div>
      </header>

      <div className="scroll-thin flex-1 overflow-y-auto px-4 py-3">
        {!hasRevision && !busy && <p className="prose-text text-subtle">A versão revista aparece aqui. Passa o rato sobre um parágrafo para o rever de novo isoladamente.</p>}
        {!hasRevision && busy && <p className="prose-text text-subtle">A rever…</p>}

        {hasRevision && view === "compare" && <DiffView original={compareWith} revised={revised} locale={locale} />}

        {hasRevision && view === "changes" && (
          <ChangesView base={compareWith} revised={revised} locale={locale} rejected={props.rejectedChanges} onToggle={props.onToggleChange} onApply={props.onApplyChanges} />
        )}

        {hasRevision && view === "edit" && (
          <textarea className="prose-text field min-h-[16rem] w-full resize-y" value={revised} onChange={(e) => props.onEdit(e.target.value)} aria-label="Editar texto revisto" />
        )}

        {hasRevision && view === "text" && (
          <div className="flex flex-col gap-3">
            {paragraphs.map((paragraph, index) => {
              const isBusy = busyParagraph === index;
              const check = checks.find((c) => c.index === index);
              const lowSimilarity = check?.similarity !== null && check?.similarity !== undefined && check.similarity < similarityMin;
              return (
                <div key={`${index}-${paragraph.slice(0, 24)}`} className={`group relative -mx-2 rounded-lg px-2 py-1 transition-colors ${isBusy ? "bg-accent-soft/50" : "hover:bg-accent-soft/30"}`}>
                  <p className="prose-text whitespace-pre-wrap">{paragraph}</p>
                  {check && (check.patterns > 0 || check.foreignVariant.length > 0 || lowSimilarity || check.polished) && (
                    <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-subtle">
                      {check.polished && <span className="rounded-full bg-accent-soft px-1.5 py-0.5 text-fg">re-revisto</span>}
                      {check.patterns > 0 && <span className="rounded-full bg-warning px-1.5 py-0.5 text-warning-fg">{check.patterns} {check.patterns === 1 ? "padrão de IA" : "padrões de IA"}</span>}
                      {check.foreignVariant.length > 0 && <span className="rounded-full bg-warning px-1.5 py-0.5 text-warning-fg" title={check.foreignVariant.join(", ")}>outra variante</span>}
                      {lowSimilarity && <span className="rounded-full bg-removed px-1.5 py-0.5 text-removed-fg">sentido afastado ({check.similarity})</span>}
                    </div>
                  )}
                  <button
                    type="button"
                    className="btn btn-sm absolute -top-2 right-1 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                    disabled={busy}
                    onClick={() => props.onReviseParagraph(index)}
                    title="Rever apenas este parágrafo"
                  >
                    <IconRefresh size={12} className={isBusy ? "animate-spin" : ""} /> {isBusy ? "A rever…" : "Rever parágrafo"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {hasRevision && (view === "compare" || view === "changes") && <p className="border-t px-4 py-2 text-[11px] text-subtle">A comparar com {compareLabel}.</p>}
    </section>
  );
}
