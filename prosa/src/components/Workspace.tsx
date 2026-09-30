"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, fetchConfig, requestCheck, streamRewrite } from "@/lib/client/api";
import { STORAGE_KEYS, clearAll, readJson, remove, writeJson } from "@/lib/client/storage";
import { createVersionId, pushVersion, type Version } from "@/lib/history";
import { countWords, joinParagraphs, localeFor, splitParagraphs } from "@/lib/text";
import type { Ambiguity, ConfigStatus, LanguageToolIssue, MeaningCheckStatus, ParagraphCheck, RewriteRequest, VoiceProfile } from "@/lib/types";
import { Controls, type Settings } from "./Controls";
import { HistoryDrawer } from "./HistoryDrawer";
import { IconHistory, IconSparkle, IconStop, IconUser, IconWarning } from "./Icons";
import { Indicators } from "./Indicators";
import { LanguageToolPanel } from "./LanguageToolPanel";
import { LockedTerms } from "./LockedTerms";
import { NotesPanel } from "./NotesPanel";
import { OriginalPanel } from "./OriginalPanel";
import { ProgressBar } from "./ProgressBar";
import { RevisedPanel, type RevisedView } from "./RevisedPanel";
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
const DEFAULT_SIMILARITY_MIN = 0.72;

interface Progress {
  completed: number;
  total: number;
}

/** Estimativa grosseira de tokens para avisar sobre a janela de contexto. */
function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.6);
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
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ambiguities, setAmbiguities] = useState<Ambiguity[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [checks, setChecks] = useState<ParagraphCheck[]>([]);
  const [meaningCheck, setMeaningCheck] = useState<MeaningCheckStatus | null>(null);

  const [persistHistory, setPersistHistory] = useState<boolean>(() => readJson<boolean>(STORAGE_KEYS.historyEnabled) === true);
  const [versions, setVersions] = useState<Version[]>(() => (readJson<boolean>(STORAGE_KEYS.historyEnabled) === true ? (readJson<Version[]>(STORAGE_KEYS.history) ?? []) : []));
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [view, setView] = useState<RevisedView>("text");
  const [compareVersion, setCompareVersion] = useState<Version | null>(null);
  const [rejectedChanges, setRejectedChanges] = useState<Set<number>>(new Set());
  const [mobileTab, setMobileTab] = useState<"original" | "revised">("original");

  const [ltBusy, setLtBusy] = useState(false);
  const [ltError, setLtError] = useState<string | null>(null);
  const [ltOriginal, setLtOriginal] = useState<LanguageToolIssue[] | null>(null);
  const [ltRevised, setLtRevised] = useState<LanguageToolIssue[] | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Configuração do servidor (só indica se o fornecedor está pronto e qual o modelo; nunca a chave).
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
  const locale = localeFor(settings.language);

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

  const resetRevisionState = () => {
    setView("text");
    setCompareVersion(null);
    setRejectedChanges(new Set());
    setLtRevised(null);
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
    setStatusMessage(null);
    resetRevisionState();
    setProgress({ completed: 0, total: 0 });
    setAmbiguities([]);
    setWarnings([]);
    setChecks([]);
    setMeaningCheck(null);
    setMobileTab("revised");

    const partial: string[] = [];
    try {
      for await (const event of streamRewrite(buildRequest(text), controller.signal)) {
        if (event.type === "start") {
          setProgress({ completed: 0, total: event.sections });
        } else if (event.type === "progress") {
          setProgress({ completed: event.completed, total: event.total });
          setStatusMessage(null);
        } else if (event.type === "status") {
          setStatusMessage(event.message);
        } else if (event.type === "section") {
          partial.push(event.section.rewritten);
          setRevised(partial.join("\n\n"));
          setChecks((prev) => [...prev, ...event.section.checks]);
        } else if (event.type === "done") {
          setRevised(event.result.rewritten);
          setAmbiguities(event.result.ambiguities);
          setWarnings(event.result.warnings);
          setChecks(event.result.checks);
          setMeaningCheck(event.result.meaningCheck);
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
      setStatusMessage(null);
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
          if (event.type === "status") setStatusMessage(event.message);
          if (event.type === "done") {
            const next = [...paragraphs];
            next[index] = event.result.rewritten;
            const joined = joinParagraphs(next);
            setRevised(joined);
            setAmbiguities((prev) => [...prev, ...event.result.ambiguities]);
            setWarnings((prev) => [...prev, ...event.result.warnings]);
            setChecks((prev) => {
              const others = prev.filter((c) => c.index !== index);
              const fresh = event.result.checks.map((c) => ({ ...c, index }));
              return [...others, ...fresh].sort((a, b) => a.index - b.index);
            });
            setRejectedChanges(new Set());
            setLtRevised(null);
            recordVersion(revised, joined, `Parágrafo ${index + 1} revisto`, { ambiguities: event.result.ambiguities, warnings: event.result.warnings });
          } else if (event.type === "error") {
            setError(event.message);
          }
        }
      } catch (err) {
        setError(describeError(err));
      } finally {
        setBusy(false);
        setBusyParagraph(null);
        setStatusMessage(null);
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
    setChecks([]);
    setMeaningCheck(null);
    resetRevisionState();
    setCurrentId(null);
  };

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const stamp = () => new Date().toISOString().slice(0, 10);

  const downloadTxt = () => downloadBlob(new Blob([revised], { type: "text/plain;charset=utf-8" }), `prosa-revisto-${stamp()}.txt`);

  const downloadDocx = async () => {
    try {
      const { buildTrackedChangesDocx } = await import("@/lib/client/docx");
      const blob = await buildTrackedChangesDocx(compareVersion ? compareVersion.text : original, revised);
      downloadBlob(blob, `prosa-alteracoes-${stamp()}.docx`);
    } catch (err) {
      setError(`Não foi possível gerar o .docx: ${describeError(err)}`);
    }
  };

  const restoreVersion = (version: Version) => {
    setRevised(version.text);
    setAmbiguities(version.ambiguities);
    setWarnings(version.warnings);
    setChecks([]);
    setCurrentId(version.id);
    resetRevisionState();
    setHistoryOpen(false);
    setMobileTab("revised");
  };

  const compareWithVersion = (version: Version) => {
    setCompareVersion(version);
    setRejectedChanges(new Set());
    setView("compare");
    setHistoryOpen(false);
    setMobileTab("revised");
  };

  const updateVersion = (id: string, patch: Partial<Pick<Version, "label" | "pinned">>) => {
    setVersions((prev) => prev.map((v) => (v.id === id ? { ...v, ...patch } : v)));
  };

  const deleteVersion = (id: string) => {
    setVersions((prev) => prev.filter((v) => v.id !== id));
    if (currentId === id) setCurrentId(null);
    if (compareVersion?.id === id) setCompareVersion(null);
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

  const toggleChange = (id: number, rejected: boolean) => {
    setRejectedChanges((prev) => {
      const next = new Set(prev);
      if (rejected) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const applyChanges = (text: string) => {
    setRevised(text);
    setRejectedChanges(new Set());
    setChecks([]);
    setLtRevised(null);
    recordVersion(revised, text, "Alterações aplicadas manualmente", { ambiguities: [], warnings: [] });
    setView("text");
  };

  const runLanguageTool = async () => {
    if (!config?.languageTool || ltBusy) return;
    setLtBusy(true);
    setLtError(null);
    try {
      const [o, r] = await Promise.all([
        original.trim() ? requestCheck(original, settings.language) : Promise.resolve([]),
        revised.trim() ? requestCheck(revised, settings.language) : Promise.resolve(null),
      ]);
      setLtOriginal(o);
      setLtRevised(r);
    } catch (err) {
      setLtError(describeError(err));
    } finally {
      setLtBusy(false);
    }
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
  const compareLabel = compareVersion ? `a versão «${compareVersion.label}»` : "o original";
  const canRewrite = original.trim().length > 0 && !busy && configured && original.length <= maxChars;

  const profileSummary = useMemo(() => {
    if (!profile) return null;
    const filled = [profile.vocabulary, profile.rhythm, profile.formality, profile.sentenceStructure, profile.avoid].filter((f) => f.trim()).length;
    return filled > 0 ? `${profile.name} · ${filled}/5 campos` : `${profile.name} · vazio`;
  }, [profile]);

  const contextWarning = useMemo(() => {
    if (!config || config.provider !== "ollama" || !config.contextLength) return null;
    const sectionTokens = estimateTokens(original.slice(0, Math.min(original.length, config.sectionWords * 7)));
    const needed = 1800 + sectionTokens * 2;
    if (config.modelContextLength && config.contextLength > config.modelContextLength) {
      return `OLLAMA_NUM_CTX (${config.contextLength}) é maior do que o contexto máximo do modelo (${config.modelContextLength}). O Ollama vai limitar ao máximo do modelo.`;
    }
    if (countWords(original) > 0 && needed > config.contextLength) {
      return `Cada secção pode precisar de cerca de ${needed} tokens (instruções, texto e resposta), acima da janela de ${config.contextLength} tokens pedida ao Ollama. Reduz PROSA_SECTION_WORDS ou aumenta OLLAMA_NUM_CTX.`;
    }
    return null;
  }, [config, original]);

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
        {contextWarning && (
          <div className="fade-up flex gap-2.5 rounded-xl border border-warning-fg/20 bg-warning p-3 text-xs text-warning-fg" role="status">
            <IconWarning size={14} className="mt-0.5 shrink-0" />
            <span>{contextWarning}</span>
          </div>
        )}

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
          <ProgressBar completed={progress.completed} total={progress.total} active={busy} label={statusMessage ?? (busyParagraph !== null ? `A rever o parágrafo ${busyParagraph + 1}…` : undefined)} />
          {config && (
            <span className="ml-auto hidden text-[11px] text-subtle sm:inline" title={`Fornecedor: ${config.provider} · ${config.baseURL}${config.embedModel ? ` · embeddings: ${config.embedModel}` : ""}`}>
              {config.provider === "ollama" ? "Ollama" : "OpenAI"} · {config.model}
              {config.contextLength ? ` · ${config.contextLength} tokens de contexto${config.modelContextLength ? ` (máx. ${config.modelContextLength})` : ""}` : ""}
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
              revised={revised}
              compareWith={compareWith}
              compareLabel={compareLabel}
              locale={locale}
              busy={busy}
              busyParagraph={busyParagraph}
              checks={checks}
              similarityMin={DEFAULT_SIMILARITY_MIN}
              view={view}
              onViewChange={setView}
              onReviseParagraph={(i) => void reviseParagraph(i)}
              onEdit={setRevised}
              onRestoreOriginal={restoreOriginal}
              onDownloadTxt={downloadTxt}
              onDownloadDocx={() => void downloadDocx()}
              rejectedChanges={rejectedChanges}
              onToggleChange={toggleChange}
              onApplyChanges={applyChanges}
            />
          </div>
        </div>

        <div className={`grid gap-4 ${ambiguities.length + warnings.length > 0 ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" : ""}`}>
          <NotesPanel ambiguities={ambiguities} warnings={warnings} />
          {(original.trim() || revised.trim()) && <Indicators original={original} revised={revised} language={settings.language} checks={checks} meaningCheck={meaningCheck} />}
        </div>

        {(original.trim() || revised.trim()) && (
          <LanguageToolPanel available={config?.languageTool ?? false} busy={ltBusy} error={ltError} original={ltOriginal} revised={ltRevised} onRun={() => void runLanguageTool()} hasRevised={revised.trim().length > 0} />
        )}
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
        onUpdate={updateVersion}
        onDelete={deleteVersion}
        persistEnabled={persistHistory}
        onTogglePersist={togglePersist}
        onClearAll={clearEverything}
      />
      <VoiceProfileDialog open={profileOpen} onClose={() => setProfileOpen(false)} language={settings.language} profile={profile} onChange={setProfile} configured={configured} />
    </div>
  );
}
