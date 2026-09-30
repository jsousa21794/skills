"use client";

import { useState } from "react";
import { IconLock, IconX } from "./Icons";

interface Props {
  terms: string[];
  onChange: (terms: string[]) => void;
}

export function LockedTerms({ terms, onChange }: Props) {
  const [draft, setDraft] = useState("");

  const add = () => {
    const value = draft.trim();
    if (!value) return;
    if (!terms.includes(value)) onChange([...terms, value]);
    setDraft("");
  };

  return (
    <div>
      <span className="label flex items-center gap-1.5">
        <IconLock size={12} /> Termos bloqueados
      </span>
      <div className="flex flex-wrap items-center gap-1.5">
        {terms.map((term) => (
          <span key={term} className="chip" title={term}>
            <span className="max-w-[14rem] truncate">{term}</span>
            <button
              type="button"
              className="rounded-full p-0.5 text-muted hover:text-fg"
              aria-label={`Desbloquear ${term}`}
              onClick={() => onChange(terms.filter((t) => t !== term))}
            >
              <IconX size={12} />
            </button>
          </span>
        ))}
        <input
          className="field h-7 max-w-[16rem] flex-1 py-0 text-xs"
          placeholder={terms.length ? "Outro termo…" : "Nome, sigla ou passagem a manter intacta"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          onBlur={add}
          aria-label="Adicionar termo bloqueado"
        />
      </div>
      <p className="mt-1.5 text-[11px] text-subtle">Também podes selecionar texto no painel original e usar «Bloquear seleção».</p>
    </div>
  );
}
