"use client";

import type { Intensity, Language, LengthOption, Mode } from "@/lib/types";
import { Segmented } from "./Segmented";

export interface Settings {
  language: Language;
  mode: Mode;
  intensity: Intensity;
  length: LengthOption;
  customInstructions: string;
}

interface Props {
  settings: Settings;
  onChange: (settings: Settings) => void;
  disabled?: boolean;
}

const LANGUAGE_OPTIONS = [
  { value: "pt-PT", label: "PT · Portugal" },
  { value: "pt-BR", label: "PT · Brasil" },
  { value: "en-GB", label: "EN · British" },
  { value: "en-US", label: "EN · American" },
] as const;

const MODE_OPTIONS = [
  { value: "profissional", label: "Profissional" },
  { value: "academico", label: "Académico" },
  { value: "informal", label: "Informal" },
  { value: "literario", label: "Literário" },
  { value: "personalizado", label: "Personalizado" },
] as const;

const INTENSITY_OPTIONS = [
  { value: "ligeira", label: "Ligeira", title: "Corrige e melhora a fluidez, preservando a estrutura." },
  { value: "moderada", label: "Moderada", title: "Reformula frases e reduz padrões repetitivos." },
  { value: "profunda", label: "Profunda", title: "Reorganiza a expressão e o ritmo, preservando as ideias." },
] as const;

const LENGTH_OPTIONS_UI = [
  { value: "manter", label: "Manter", title: "Aproximadamente o mesmo número de palavras." },
  { value: "encurtar", label: "Encurtar", title: "Cerca de 60% a 75% do original." },
  { value: "desenvolver", label: "Desenvolver", title: "Cerca de 125% a 150%, sem inventar informação." },
] as const;

export function Controls({ settings, onChange, disabled }: Props) {
  const update = <K extends keyof Settings>(key: K, value: Settings[K]) => onChange({ ...settings, [key]: value });

  return (
    <fieldset disabled={disabled} className="flex flex-col gap-4 disabled:opacity-60">
      <div className="flex flex-wrap gap-x-6 gap-y-4">
        <div>
          <label className="label" htmlFor="language">
            Idioma
          </label>
          <select
            id="language"
            className="field h-[2.125rem] w-auto py-0 pr-8"
            value={settings.language}
            onChange={(e) => update("language", e.target.value as Language)}
          >
            {LANGUAGE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <span className="label">Modo</span>
          <Segmented ariaLabel="Modo de escrita" value={settings.mode} options={MODE_OPTIONS} onChange={(v) => update("mode", v)} />
        </div>
        <div>
          <span className="label">Intensidade</span>
          <Segmented ariaLabel="Intensidade de revisão" value={settings.intensity} options={INTENSITY_OPTIONS} onChange={(v) => update("intensity", v)} />
        </div>
        <div>
          <span className="label">Extensão</span>
          <Segmented ariaLabel="Extensão do resultado" value={settings.length} options={LENGTH_OPTIONS_UI} onChange={(v) => update("length", v)} />
        </div>
      </div>
      {settings.mode === "personalizado" && (
        <div className="fade-up">
          <label className="label" htmlFor="custom">
            Instruções de estilo
          </label>
          <textarea
            id="custom"
            className="field min-h-[3.5rem] resize-y"
            placeholder="Ex.: frases curtas, sem primeira pessoa do plural, evitar adjetivos em cadeia, tratar o leitor por «tu»."
            value={settings.customInstructions}
            maxLength={4000}
            onChange={(e) => update("customInstructions", e.target.value)}
          />
          <p className="mt-1 text-[11px] text-subtle">As instruções aplicam-se ao estilo. Não alteram as regras de preservação de factos.</p>
        </div>
      )}
    </fieldset>
  );
}
