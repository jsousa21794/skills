"use client";

import { formatTime, type Version } from "@/lib/history";
import { IconTrash, IconX } from "./Icons";

interface Props {
  open: boolean;
  onClose: () => void;
  versions: Version[];
  currentId: string | null;
  onRestore: (version: Version) => void;
  onCompare: (version: Version) => void;
  persistEnabled: boolean;
  onTogglePersist: (enabled: boolean) => void;
  onClearAll: () => void;
}

const LABELS: Record<string, string> = {
  ligeira: "ligeira",
  moderada: "moderada",
  profunda: "profunda",
};

export function HistoryDrawer({ open, onClose, versions, currentId, onRestore, onCompare, persistEnabled, onTogglePersist, onClearAll }: Props) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-labelledby="history-title">
      <button type="button" className="absolute inset-0 bg-black/20 dark:bg-black/50" aria-label="Fechar histórico" onClick={onClose} />
      <aside
        className="absolute inset-y-0 right-0 flex w-full max-w-sm flex-col border-l bg-elevated shadow-xl"
        style={{ animation: "fade-up 200ms var(--ease-out) both" }}
      >
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
                    <span className="text-sm font-medium">{version.label}</span>
                    <time className="text-[11px] text-subtle" dateTime={new Date(version.createdAt).toISOString()}>
                      {formatTime(version.createdAt)}
                    </time>
                  </div>
                  <p className="mb-2 line-clamp-2 text-xs text-muted">{version.text}</p>
                  <div className="flex items-center gap-1.5 text-[11px] text-subtle">
                    <span>{version.options.language}</span>·<span>{version.options.mode}</span>·<span>{LABELS[version.options.intensity] ?? version.options.intensity}</span>
                  </div>
                  <div className="mt-2 flex gap-1.5">
                    <button type="button" className="btn btn-sm" onClick={() => onRestore(version)}>
                      Restaurar
                    </button>
                    <button type="button" className="btn btn-sm btn-ghost" onClick={() => onCompare(version)}>
                      Comparar com atual
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
