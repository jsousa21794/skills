"use client";

import type { LanguageToolIssue } from "@/lib/types";
import { IconRefresh } from "./Icons";

interface Props {
  available: boolean;
  busy: boolean;
  error: string | null;
  original: LanguageToolIssue[] | null;
  revised: LanguageToolIssue[] | null;
  onRun: () => void;
  hasRevised: boolean;
}

function Group({ title, issues }: { title: string; issues: LanguageToolIssue[] | null }) {
  if (issues === null) return null;
  const grouped = new Map<string, LanguageToolIssue[]>();
  for (const issue of issues) grouped.set(issue.category, [...(grouped.get(issue.category) ?? []), issue]);
  return (
    <div>
      <p className="label">
        {title} · {issues.length} {issues.length === 1 ? "ocorrência" : "ocorrências"}
      </p>
      {issues.length === 0 ? (
        <p className="text-xs text-subtle">Sem ocorrências.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {Array.from(grouped.entries()).map(([category, list]) => (
            <li key={category} className="text-xs">
              <span className="font-medium">{category}</span> <span className="text-muted">×{list.length}</span>
              <ul className="mt-1 flex flex-col gap-1 pl-3">
                {list.slice(0, 6).map((issue, i) => (
                  <li key={i} className="text-muted">
                    <span className="text-fg">«{issue.context.trim().slice(0, 90)}»</span> — {issue.shortMessage || issue.message}
                    {issue.replacements.length > 0 && <span className="text-subtle"> · sugestões: {issue.replacements.slice(0, 3).join(", ")}</span>}
                  </li>
                ))}
                {list.length > 6 && <li className="text-subtle">… e mais {list.length - 6}.</li>}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Resultados do LanguageTool para o original e para o revisto, quando há servidor configurado. */
export function LanguageToolPanel({ available, busy, error, original, revised, onRun, hasRevised }: Props) {
  return (
    <section className="surface p-4" aria-labelledby="lt-title">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="lt-title" className="text-sm font-semibold">
          Verificação linguística (LanguageTool)
        </h2>
        <button type="button" className="btn btn-sm" onClick={onRun} disabled={!available || busy}>
          <IconRefresh size={12} className={busy ? "animate-spin" : ""} /> {busy ? "A verificar…" : "Verificar"}
        </button>
      </div>
      {!available && (
        <p className="text-xs text-muted">
          Sem servidor LanguageTool configurado. Corre um localmente (por exemplo <code className="rounded bg-bg px-1">docker run -p 8010:8010 erikvl87/languagetool</code>) e define <code className="rounded bg-bg px-1">LANGUAGETOOL_URL=http://localhost:8010</code>.
        </p>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
      {available && original === null && !error && <p className="text-xs text-subtle">Gramática, ortografia e regras da variante escolhida, para o original e para a revisão.</p>}
      <div className={`mt-3 grid gap-4 ${hasRevised ? "sm:grid-cols-2" : ""}`}>
        <Group title="Original" issues={original} />
        {hasRevised && <Group title="Revisto" issues={revised} />}
      </div>
    </section>
  );
}
