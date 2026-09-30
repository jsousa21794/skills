"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, fetchConfig, streamRewrite } from "@/lib/client/api";
import { STORAGE_KEYS, clearAll, readJson, remove, writeJson } from "@/lib/client/storage";
import { createVersionId, pushVersion, type Version } from "@/lib/history";
import { joinParagraphs, splitParagraphs } from "@/lib/text";
import type { Ambiguity, ConfigStatus, RewriteRequest, VoiceProfile } from "@/lib/types";
import { Controls, type Settings } from "./Controls";
import { HistoryDrawer } from "./HistoryDrawer";
import { IconHistory, IconSparkle, IconStop, IconUser, IconWarning } from "./Icons";
import { Indicators } from "./Indicators";
import { LockedTerms } from "./LockedTerms";
import { NotesPanel } from "./NotesPanel";
import { OriginalPanel } from "./OriginalPanel";
import { ProgressBar } from "./ProgressBar";
import { RevisedPanel } from "./RevisedPanel";
import { ThemeToggle } from "./ThemeToggle";
import { VoiceProfileDialog } from "./VoiceProfileDialog";

const DEFAULT_SETTINGS: Settings = {
  language: "pt-PT",
  mode: "profissional",
  intensity: "moderada",
  length: "manter",
  customInstructions: "",
};

const INTENSITY_LABEL = { ligeira: "Ligeira", moderada: "Moderada", profunda: "Profunda" } as const;

interface Progress {
  completed: number;
  total: number;
}

export function Workspace() {
  // Estado inicial lido do browser: este componente só é renderizado no cliente.
  const [settings, setSettings] = useState<Settings>(() => ({ ...DEFAULT_SETTINGS, ...(readJson<Settings>(STORAGE_KEYS.settings) ?? {}) }));
  const [original, setOriginal] = useState("");
  const [revised, setRevised] = useState("");
  const [lockedTerms, setLockedTerms] = useState<string[]>([]);
  const [profile, setProfile] = useState<VoiceProfile | null>(() => readJson<VoiceProfile>(STORAGE_KEYS.profile));
  const [config, setConfig] = useState<ConfigStatus | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [busyParagraph, setBusyParagraph] = useState<number | null>(null);
  const [progress, setProgress] = useState<Progress>({ completed: 0, total: 0 });
  const [error, setError] = useState<string | null>(null);
  const [ambiguities, setAmbiguities] = useState<Ambiguity[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [persistHistory, setPersistHistory] = useState<boolean>(() => readJson<boolean>(STORAGE_KEYS.historyEnabled) === true);
  const [versions, setVersions] = useState<Version[]>(() =>
    readJson<boolean>(STORAGE_KEYS.historyEnabled) === true ? (readJson<Version[]>(STORAGE_KEYS.history) ?? []) : [],
  );
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [showDiff, setShowDiff] = useState(false);
  const [compareVersion, setCompareVersion] = useState<Version | null>(null);
  const [mobileTab, setMobileTab] = useState<"original" | "revised">("original");

  const abortRef = useRef<AbortController | null>(null);

  // Configuração do servidor (só indica se a chave existe e qual o modelo; nunca a chave).
  useEffect(() => {
    fetchConfig()
      .then(setConfig)
      .catch(() => setConfigError("Não foi possível obter a configuração do servidor."));
  }, []);

  useEffect(() => {
    writeJson(STORAGE_KEYS.settings, settings);
  }, [settings]);

  useEffect(() => {
    if (profile) writeJson(STORAGE_KEYS.profile, profile);
    else remove(STORAGE_KEYS.profile);
  }, [profile]);

  useEffect(() => {
    if (persistHistory) writeJson(STORAGE_KEYS.history, versions);
  }, [versions, persistHistory]);

  const configured = config?.ready ?? false;
  const maxChars = config?.maxInputChars ?? 80000;

  const buildRequest = useCallback(
    (text: string, context?: RewriteRequest["context"]): RewriteRequest => ({
      text,
      language: settings.language,
      mode: settings.mode,
      intensity: settings.intensity,
      length: settings.length,
      customInstructions: settings.mode === "personalizado" ? settings.customInstructions : undefined,
      lockedTerms,
      profile,
      context,
    }),
    [settings, lockedTerms, profile],
  );

  const recordVersion = useCallback(
    (source: string, text: string, label: string, notes: { ambiguities: Ambiguity[]; warnings: string[] }) => {
      const version: Version = {
        id: createVersionId(),
        createdAt: Date.now(),
        source,
        text,
        label,
        options: { language: settings.language, mode: settings.mode, intensity: settings.intensity, length: settings.length },
        ambiguities: notes.ambiguities,
        warnings: notes.warnings,
      };
      setVersions((prev) => pushVersion(prev, version));
      setCurrentId(version.id);
    },
    [settings],
  );

  const describeError = (err: unknown): string => {
    if (err instanceof DOMException && err.name === "AbortError") return "Reescrita cancelada.";
    if (err instanceof ApiError) return err.message;
    if (err instanceof Error) return err.message;
    return "Ocorreu um erro inesperado.";
  };

  const rewrite = useCallback(async () => {
    const text = original.trim();
    if (!text || busy) return;
    if (text.length > maxChars) {
      setError(`O texto excede o limite de ${maxChars.toLocaleString("pt-PT")} caracteres.`);
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setBusy(true);
    setBusyParagraph(null);
    setError(null);
    setShowDiff(false);
    setCompareVersion(null);
    setProgress({ completed: 0, total: 0 });
    setAmbiguities([]);
    setWarnings([]);
    setMobileTab("revised");

    const partial: string[] = [];
    try {
      for await (const event of streamRewrite(buildRequest(text), controller.signal)) {
        if (event.type === "start") {
          setProgress({ completed: 0, total: event.sections });
        } else if (event.type === "progress") {
          setProgress({ completed: event.completed, total: event.total });
        } else if (event.type === "section") {
          partial.push(event.section.rewritten);
          setRevised(partial.join("\n\n"));
        } else if (event.type === "done") {
          setRevised(event.result.rewritten);
          setAmbiguities(event.result.ambiguities);
          setWarnings(event.result.warnings);
          recordVersion(text, event.result.rewritten, `${INTENSITY_LABEL[settings.intensity]} · ${settings.mode}`, {
            ambiguities: event.result.ambiguities,
            warnings: event.result.warnings,
          });
        } else if (event.type === "error") {
          setError(event.message);
        }
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }, [original, busy, maxChars, buildRequest, recordVersion, settings.intensity, settings.mode]);

  const reviseParagraph = useCallback(
    async (index: number) => {
      if (busy) return;
      const paragraphs = splitParagraphs(revised);
      const target = paragraphs[index];
      if (!target) return;
      const controller = new AbortController();
      abortRef.current = controller;
      setBusy(true);
      setBusyParagraph(index);
      setError(null);
      setProgress({ completed: 0, total: 1 });
      try {
        const request = buildRequest(target, {
          before: paragraphs.slice(Math.max(0, index - 2), index).join("\n\n") || undefined,
          after: paragraphs.slice(index + 1, index + 3).join("\n\n") || undefined,
        });
        for await (const event of streamRewrite(request, controller.signal)) {
          if (event.type === "done") {
            const next = [...paragraphs];
            next[index] = event.result.rewritten;
            const joined = joinParagraphs(next);
            setRevised(joined);
            setAmbiguities((prev) => [...prev, ...event.result.ambiguities]);
            setWarnings((prev) => [...prev, ...event.result.warnings]);
            recordVersion(revised, joined, `Parágrafo ${index + 1} revisto`, {
              ambiguities: event.result.ambiguities,
              warnings: event.result.warnings,
            });
          } else if (event.type === "error") {
            setError(event.message);
          }
        }
      } catch (err) {
        setError(describeError(err));
      } finally {
        setBusy(false);
        setBusyParagraph(null);
        abortRef.current = null;
      }
    },
    [busy, revised, buildRequest, recordVersion],
  );

  const cancel = () => abortRef.current?.abort();

  const restoreOriginal = () => {
    setRevised(original);
    setAmbiguities([]);
    setWarnings([]);
    setShowDiff(false);
    setCurrentId(null);
  };

  const download = () => {
    const blob = new Blob([revised], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `prosa-revisto-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const restoreVersion = (version: Version) => {
    setRevised(version.text);
    setAmbiguities(version.ambiguities);
    setWarnings(version.warnings);
    setCurrentId(version.id);
    setCompareVersion(null);
    setHistoryOpen(false);
    setMobileTab("revised");
  };

  const compareWithVersion = (version: Version) => {
    setCompareVersion(version);
    setShowDiff(true);
    setHistoryOpen(false);
    setMobileTab("revised");
  };

  const togglePersist = (enabled: boolean) => {
    setPersistHistory(enabled);
    writeJson(STORAGE_KEYS.historyEnabled, enabled);
    if (enabled) writeJson(STORAGE_KEYS.history, versions);
    else remove(STORAGE_KEYS.history);
  };

  const clearEverything = () => {
    if (!window.confirm("Eliminar o histórico, o perfil de voz e as preferências guardadas neste browser?")) return;
    clearAll();
    setVersions([]);
    setCurrentId(null);
    setPersistHistory(false);
    setProfile(null);
    setSettings(DEFAULT_SETTINGS);
    setHistoryOpen(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        void rewrite();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rewrite]);

  const compareWith = compareVersion ? compareVersion.text : original;
  const compareLabel = compareVersion ? `versão «${compareVersion.label}»` : "o original";
  const canRewrite = original.trim().length > 0 && !busy && configured && original.length <= maxChars;

  const profileSummary = useMemo(() => {
    if (!profile) return null;
    const filled = [profile.vocabulary, profile.rhythm, profile.formality, profile.sentenceStructure, profile.avoid].filter((f) => f.trim()).length;
    return filled > 0 ? `${profile.name} · ${filled}/5 campos` : `${profile.name} · vazio`;
  }, [profile]);

  return (
    <div className="mx-auto flex min-h-dvh max-w-7xl flex-col px-4 pb-10 sm:px-6">
      <header className="sticky top-0 z-30 -mx-4 flex items-center justify-between gap-3 border-b bg-bg/85 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
        <div className="flex items-baseline gap-2.5">
          <span className="font-serif text-xl font-semibold tracking-tight">Prosa</span>
          <span className="hidden text-xs text-muted sm:inline">revisão e reescrita com voz própria</span>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" className="btn btn-ghost" onClick={() => setProfileOpen(true)} title="Perfil de voz">
            <IconUser /> <span className="hidden sm:inline">{profileSummary ?? "Perfil de voz"}</span>
          </button>
          <button type="button" className="btn btn-ghost" onClick={() => setHistoryOpen(true)} title="Histórico de versões">
            <IconHistory /> <span className="hidden sm:inline">Histórico</span>
            {versions.length > 0 && <span className="rounded-full bg-accent-soft px-1.5 text-[10px] tabular-nums">{versions.length}</span>}
          </button>
          <ThemeToggle />
        </div>
      </header>

      <main className="flex flex-1 flex-col gap-4 pt-4">
        {config && !config.ready && config.problem && (
          <div className="fade-up flex gap-2.5 rounded-xl border border-warning-fg/20 bg-warning p-3 text-sm text-warning-fg" role="alert">
            <IconWarning className="mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">A reescrita real está desativada até o fornecedor de IA estar disponível.</p>
              <p className="mt-0.5 text-xs leading-relaxed">{config.problem} A Prosa nunca mostra resultados simulados como se fossem reais.</p>
              <button type="button" className="btn btn-sm mt-2" onClick={() => fetchConfig().then(setConfig).catch(() => undefined)}>
                Verificar de novo
              </button>
            </div>
          </div>
        )}
        {configError && <p className="text-xs text-danger">{configError}</p>}

        <section className="surface p-4">
          <Controls settings={settings} onChange={setSettings} disabled={busy} />
          <div className="mt-4 border-t pt-4">
            <LockedTerms terms={lockedTerms} onChange={setLockedTerms} />
          </div>
        </section>

        <div className="flex flex-wrap items-center gap-3">
          {busy ? (
            <button type="button" className="btn" onClick={cancel}>
              <IconStop size={14} /> Cancelar
            </button>
          ) : (
            <button type="button" className="btn btn-primary" onClick={() => void rewrite()} disabled={!canRewrite} title="Ctrl/Cmd + Enter">
              <IconSparkle size={14} /> Reescrever
            </button>
          )}
          <ProgressBar completed={progress.completed} total={progress.total} active={busy} label={busyParagraph !== null ? `A rever o parágrafo ${busyParagraph + 1}…` : undefined} />
          {config && (
            <span className="ml-auto hidden text-[11px] text-subtle sm:inline" title={`Fornecedor: ${config.provider} · ${config.baseURL}`}>
              {config.provider === "ollama" ? "Ollama" : "OpenAI"} · {config.model}
            </span>
          )}
        </div>

        {error && (
          <div className="fade-up flex items-start justify-between gap-3 rounded-xl border border-danger/30 bg-removed p-3 text-sm text-removed-fg" role="alert">
            <span>{error}</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setError(null)} aria-label="Fechar aviso">
              ×
            </button>
          </div>
        )}

        <div className="segmented self-start lg:hidden" role="tablist" aria-label="Painel visível">
          <button type="button" role="tab" aria-selected={mobileTab === "original"} onClick={() => setMobileTab("original")}>
            Original
          </button>
          <button type="button" role="tab" aria-selected={mobileTab === "revised"} onClick={() => setMobileTab("revised")}>
            Revisto
          </button>
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <div className={mobileTab === "original" ? "" : "hidden lg:block"}>
            <OriginalPanel
              value={original}
              onChange={setOriginal}
              onLockSelection={(selection) => setLockedTerms((prev) => (prev.includes(selection) ? prev : [...prev, selection]))}
              disabled={busy}
              maxChars={maxChars}
            />
          </div>
          <div className={mobileTab === "revised" ? "" : "hidden lg:block"}>
            <RevisedPanel
              original={original}
              revised={revised}
              compareWith={compareWith}
              compareLabel={compareLabel}
              busy={busy}
              busyParagraph={busyParagraph}
              onReviseParagraph={(i) => void reviseParagraph(i)}
              onEdit={setRevised}
              onRestoreOriginal={restoreOriginal}
              onDownload={download}
              showDiff={showDiff}
              onToggleDiff={() => setShowDiff((v) => !v)}
            />
          </div>
        </div>

        <div className={`grid gap-4 ${ambiguities.length + warnings.length > 0 ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" : ""}`}>
          <NotesPanel ambiguities={ambiguities} warnings={warnings} />
          {(original.trim() || revised.trim()) && <Indicators original={original} revised={revised} />}
        </div>
      </main>

      <footer className="mt-8 flex flex-wrap items-center justify-between gap-2 text-[11px] text-subtle">
        <span>Os textos não são guardados no servidor. O histórico local é opcional e pode ser eliminado a qualquer momento.</span>
        <span>Ctrl/Cmd + Enter para reescrever</span>
      </footer>

      <HistoryDrawer
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        versions={versions}
        currentId={currentId}
        onRestore={restoreVersion}
        onCompare={compareWithVersion}
        persistEnabled={persistHistory}
        onTogglePersist={togglePersist}
        onClearAll={clearEverything}
      />
      <VoiceProfileDialog open={profileOpen} onClose={() => setProfileOpen(false)} language={settings.language} profile={profile} onChange={setProfile} configured={configured} />
    </div>
  );
}
