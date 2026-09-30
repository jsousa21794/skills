"use client";

import { useRef } from "react";
import { countWords } from "@/lib/text";
import { IconLock } from "./Icons";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onLockSelection: (selection: string) => void;
  disabled?: boolean;
  maxChars: number;
}

export function OriginalPanel({ value, onChange, onLockSelection, disabled, maxChars }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const words = countWords(value);
  const tooLong = value.length > maxChars;

  const lockSelection = () => {
    const el = ref.current;
    if (!el) return;
    const selection = el.value.slice(el.selectionStart, el.selectionEnd).trim();
    if (selection) onLockSelection(selection);
    el.focus();
  };

  return (
    <section className="surface flex min-h-[20rem] flex-col" aria-labelledby="original-title">
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2.5">
        <h2 id="original-title" className="text-sm font-semibold">
          Original
        </h2>
        <div className="flex items-center gap-2">
          <button type="button" className="btn btn-ghost btn-sm" onClick={lockSelection} disabled={disabled} title="Bloquear o texto selecionado para que não seja alterado">
            <IconLock size={14} /> Bloquear seleção
          </button>
          <span className={`text-xs tabular-nums ${tooLong ? "text-danger" : "text-subtle"}`}>
            {words} {words === 1 ? "palavra" : "palavras"}
          </span>
        </div>
      </header>
      <textarea
        ref={ref}
        className="prose-text scroll-thin min-h-[18rem] flex-1 resize-none bg-transparent px-4 py-3 outline-none placeholder:text-subtle"
        placeholder="Cola ou escreve aqui o texto a rever…"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        spellCheck
        aria-label="Texto original"
      />
      {tooLong && (
        <p className="border-t px-4 py-2 text-xs text-danger">
          O texto excede o limite de {maxChars.toLocaleString("pt-PT")} caracteres. Divide-o ou aumenta PROSA_MAX_INPUT_CHARS.
        </p>
      )}
    </section>
  );
}
