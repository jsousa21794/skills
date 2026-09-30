"use client";

import { useState } from "react";
import { ApiError, requestProfile } from "@/lib/client/api";
import type { Language, VoiceProfile } from "@/lib/types";
import { IconSparkle, IconTrash, IconX } from "./Icons";

interface Props {
  open: boolean;
  onClose: () => void;
  language: Language;
  profile: VoiceProfile | null;
  onChange: (profile: VoiceProfile | null) => void;
  configured: boolean;
}

const FIELDS: { key: keyof Omit<VoiceProfile, "name">; label: string; hint: string }[] = [
  { key: "vocabulary", label: "Vocabulário", hint: "Palavras e expressões típicas, nível de tecnicidade, o que evita." },
  { key: "rhythm", label: "Ritmo", hint: "Extensão média das frases, alternância, pausas e parágrafos." },
  { key: "formality", label: "Formalidade", hint: "Registo, tratamento do leitor, tom." },
  { key: "sentenceStructure", label: "Construção frásica", hint: "Ordem, subordinação, listas, pontuação, aberturas de frase." },
  { key: "avoid", label: "Evitar", hint: "O que esta voz claramente não faz." },
];

const EMPTY: VoiceProfile = { name: "O meu perfil", vocabulary: "", rhythm: "", formality: "", sentenceStructure: "", avoid: "" };

export function VoiceProfileDialog({ open, onClose, language, profile, onChange, configured }: Props) {
  const [examples, setExamples] = useState<string[]>([""]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open) return null;
  const current = profile ?? EMPTY;

  const analyse = async () => {
    const cleaned = examples.map((e) => e.trim()).filter((e) => e.length >= 20);
    if (cleaned.length === 0) {
      setError("Cola pelo menos um exemplo com 20 caracteres ou mais.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const generated = await requestProfile(cleaned, language);
      onChange({ ...generated, name: current.name });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Não foi possível criar o perfil.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-end justify-center sm:items-center" role="dialog" aria-modal="true" aria-labelledby="profile-title">
      <button type="button" className="absolute inset-0 bg-black/20 dark:bg-black/50" aria-label="Fechar" onClick={onClose} />
      <div className="surface relative flex max-h-[92dvh] w-full max-w-2xl flex-col overflow-hidden rounded-b-none sm:rounded-b-[0.875rem]" style={{ animation: "fade-up 200ms var(--ease-out) both" }}>
        <header className="flex items-center justify-between border-b px-5 py-3">
          <h2 id="profile-title" className="text-sm font-semibold">
            Perfil de voz
          </h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Fechar">
            <IconX />
          </button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto px-5 py-4">
          <p className="mb-4 text-sm text-muted">
            Cola um ou mais textos escritos por ti. A Prosa extrai um perfil com as tuas preferências, que podes editar. O perfil é aplicado sem copiar passagens dos exemplos, e os exemplos não são guardados.
          </p>
          <div className="flex flex-col gap-2">
            {examples.map((example, i) => (
              <div key={i} className="relative">
                <textarea
                  className="field min-h-[5rem] resize-y pr-9"
                  placeholder={`Exemplo ${i + 1} da tua escrita…`}
                  value={example}
                  onChange={(e) => setExamples(examples.map((x, j) => (j === i ? e.target.value : x)))}
                />
                {examples.length > 1 && (
                  <button type="button" className="btn btn-ghost btn-sm absolute right-1 top-1" aria-label="Remover exemplo" onClick={() => setExamples(examples.filter((_, j) => j !== i))}>
                    <IconX size={14} />
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {examples.length < 10 && (
              <button type="button" className="btn btn-sm" onClick={() => setExamples([...examples, ""])}>
                Adicionar exemplo
              </button>
            )}
            <button type="button" className="btn btn-primary btn-sm" onClick={analyse} disabled={busy || !configured} title={configured ? undefined : "Configura ANTHROPIC_API_KEY para analisar exemplos."}>
              <IconSparkle size={14} /> {busy ? "A analisar…" : "Criar perfil a partir dos exemplos"}
            </button>
          </div>
          {error && <p className="mt-2 text-xs text-danger">{error}</p>}

          <hr className="my-5" />

          <div className="mb-3">
            <label className="label" htmlFor="profile-name">
              Nome do perfil
            </label>
            <input id="profile-name" className="field" value={current.name} onChange={(e) => onChange({ ...current, name: e.target.value })} maxLength={80} />
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {FIELDS.map((field) => (
              <div key={field.key} className={field.key === "avoid" ? "sm:col-span-2" : ""}>
                <label className="label" htmlFor={`profile-${field.key}`}>
                  {field.label}
                </label>
                <textarea
                  id={`profile-${field.key}`}
                  className="field min-h-[4.5rem] resize-y"
                  placeholder={field.hint}
                  value={current[field.key]}
                  maxLength={2000}
                  onChange={(e) => onChange({ ...current, [field.key]: e.target.value })}
                />
              </div>
            ))}
          </div>
        </div>
        <footer className="flex items-center justify-between border-t px-5 py-3">
          <button type="button" className="btn btn-sm text-danger" onClick={() => onChange(null)} disabled={!profile}>
            <IconTrash size={14} /> Apagar perfil
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={onClose}>
            Concluído
          </button>
        </footer>
      </div>
    </div>
  );
}
