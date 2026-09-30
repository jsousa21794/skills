"use client";

import { useState } from "react";
import { formatTime, type Version } from "@/lib/history";
import { IconTrash, IconX } from "./Icons";

interface Props {
  open: boolean;
  onClose: () => void;
  versions: Version[];
  currentId: string | null;
  onRestore: (version: Version) => void;
  onCompare: (version: Version) => void;
  onUpdate: (id: string, patch: Partial<Pick<Version, "label" | "pinned">>) => void;
  onDelete: (id: string) => void;
  persistEnabled: boolean;
  onTogglePersist: (enabled: boolean) => void;
  onClearAll: () => void;
}

export function HistoryDrawer({ open, onClose, versions, currentId, onRestore, onCompare, onUpdate, onDelete, persistEnabled, onTogglePersist, onClearAll }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  if (!open) return null;

  const commitLabel = (id: string) => {
    const label = draft.trim();
    if (label) onUpdate(id, { label });
    setEditing(null);
  };

  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-labelledby="history-title">
      <button type="button" className="absolute inset-0 bg-black/20 dark:bg-black/50" aria-label="Fechar histórico" onClick={onClose} />
      <aside className="absolute inset-y-0 right-0 flex w-full max-w-sm flex-col border-l bg-elevated shadow-xl" style={{ animation: "fade-up 200ms var(--ease-out) both" }}>
        <header className="flex items-center justify-between border-b px-4 py-3">
          <h2 id="history-title" className="text-sm font-semibold">
            Histórico de versões
          </h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Fechar">
            <IconX />
          </button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto p-3">
          {versions.length === 0 ? (
            <p className="p-3 text-sm text-muted">Ainda não há versões. Cada reescrita cria uma entrada aqui.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {versions.map((version) => (
                <li key={version.id} className={`rounded-lg border p-3 ${version.id === currentId ? "border-accent bg-accent-soft/40" : ""}`}>
                  <div className="mb-1 flex items-baseline justify-between gap-2">
                    {editing === version.id ? (
                      <input
                        className="field h-7 py-0 text-sm"
                        value={draft}
                        autoFocus
                        onChange={(e) => setDraft(e.target.value)}
                        onBlur={() => commitLabel(version.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitLabel(version.id);
                          if (e.key === "Escape") setEditing(null);
                        }}
                        aria-label="Nome da versão"
                      />
                    ) : (
                      <button
                        type="button"
                        className="truncate text-left text-sm font-medium hover:underline"
                        title="Renomear"
                        onClick={() => {
                          setEditing(version.id);
                          setDraft(version.label);
                        }}
                      >
                        {version.pinned && <span className="mr-1 text-accent" aria-label="Marcada">★</span>}
                        {version.label}
                      </button>
                    )}
                    <time className="shrink-0 text-[11px] text-subtle" dateTime={new Date(version.createdAt).toISOString()}>
                      {formatTime(version.createdAt)}
                    </time>
                  </div>
                  <p className="mb-2 line-clamp-2 text-xs text-muted">{version.text}</p>
                  <div className="flex items-center gap-1.5 text-[11px] text-subtle">
                    <span>{version.options.language}</span>·<span>{version.options.mode}</span>·<span>{version.options.intensity}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    <button type="button" className="btn btn-sm" onClick={() => onRestore(version)}>
                      Restaurar
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCompare(version)}>
                      Comparar
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onUpdate(version.id, { pinned: !version.pinned })} title="Versões marcadas não são descartadas quando o histórico enche">
                      {version.pinned ? "Desmarcar" : "Marcar"}
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost text-danger" onClick={() => onDelete(version.id)} aria-label="Eliminar versão">
                      <IconTrash size={12} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <footer className="border-t p-3 text-xs">
          <label className="flex cursor-pointer items-start gap-2">
            <input type="checkbox" className="mt-0.5 accent-[var(--accent)]" checked={persistEnabled} onChange={(e) => onTogglePersist(e.target.checked)} />
            <span>
              <span className="font-medium">Guardar histórico neste browser</span>
              <span className="block text-subtle">Desligado por defeito. Os textos ficam apenas em memória e desaparecem ao fechar a página.</span>
            </span>
          </label>
          <button type="button" className="btn btn-sm mt-3 w-full text-danger" onClick={onClearAll}>
            <IconTrash size={14} /> Eliminar histórico e dados guardados
          </button>
        </footer>
      </aside>
    </div>
  );
}
