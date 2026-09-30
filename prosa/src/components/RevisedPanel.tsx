"use client";

import { useState } from "react";
import { splitParagraphs } from "@/lib/text";
import { DiffView } from "./DiffView";
import { IconCheck, IconCompare, IconCopy, IconDownload, IconRefresh, IconUndo } from "./Icons";

interface Props {
  original: string;
  revised: string;
  compareWith: string;
  compareLabel: string;
  busy: boolean;
  busyParagraph: number | null;
  onReviseParagraph: (index: number) => void;
  onEdit: (text: string) => void;
  onRestoreOriginal: () => void;
  onDownload: () => void;
  showDiff: boolean;
  onToggleDiff: () => void;
}

export function RevisedPanel({
  original,
  revised,
  compareWith,
  compareLabel,
  busy,
  busyParagraph,
  onReviseParagraph,
  onEdit,
  onRestoreOriginal,
  onDownload,
  showDiff,
  onToggleDiff,
}: Props) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
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
        <h2 id="revised-title" className="text-sm font-semibold">
          Revisto
        </h2>
        <div className="flex flex-wrap items-center gap-1">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onToggleDiff} disabled={!hasRevision} aria-pressed={showDiff} title={`Comparar com ${compareLabel}`}>
            <IconCompare size={14} /> {showDiff ? "Ver texto" : "Comparar"}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing((v) => !v)} disabled={!hasRevision || busy} aria-pressed={editing}>
            {editing ? "Terminar edição" : "Editar"}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={copy} disabled={!hasRevision} title="Copiar texto revisto">
            {copied ? <IconCheck size={14} /> : <IconCopy size={14} />} {copied ? "Copiado" : "Copiar"}
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onDownload} disabled={!hasRevision} title="Descarregar como ficheiro de texto">
            <IconDownload size={14} /> Descarregar
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onRestoreOriginal} disabled={!hasRevision || busy} title="Substituir a revisão pelo texto original">
            <IconUndo size={14} /> Restaurar original
          </button>
        </div>
      </header>

      <div className="scroll-thin flex-1 overflow-y-auto px-4 py-3">
        {!hasRevision && !busy && (
          <p className="prose-text text-subtle">A versão revista aparece aqui. Passa o rato sobre um parágrafo para o rever de novo isoladamente.</p>
        )}
        {!hasRevision && busy && <p className="prose-text text-subtle">A rever…</p>}

        {hasRevision && showDiff && <DiffView original={compareWith} revised={revised} />}

        {hasRevision && !showDiff && editing && (
          <textarea
            className="prose-text field min-h-[16rem] w-full resize-y"
            value={revised}
            onChange={(e) => onEdit(e.target.value)}
            aria-label="Editar texto revisto"
          />
        )}

        {hasRevision && !showDiff && !editing && (
          <div className="flex flex-col gap-3">
            {paragraphs.map((paragraph, index) => {
              const isBusy = busyParagraph === index;
              return (
                <div key={`${index}-${paragraph.slice(0, 24)}`} className={`group relative -mx-2 rounded-lg px-2 py-1 transition-colors ${isBusy ? "bg-accent-soft/50" : "hover:bg-accent-soft/30"}`}>
                  <p className="prose-text whitespace-pre-wrap">{paragraph}</p>
                  <button
                    type="button"
                    className="btn btn-sm absolute -top-2 right-1 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                    disabled={busy}
                    onClick={() => onReviseParagraph(index)}
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
      {hasRevision && original !== compareWith && showDiff && (
        <p className="border-t px-4 py-2 text-[11px] text-subtle">A comparar com: {compareLabel}.</p>
      )}
    </section>
  );
}
