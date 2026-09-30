"use client";

import dynamic from "next/dynamic";

/**
 * O editor é totalmente interativo e lê preferências do browser ao iniciar,
 * por isso é carregado apenas no cliente. Evita diferenças entre servidor e cliente.
 */
const Workspace = dynamic(() => import("./Workspace").then((m) => m.Workspace), {
  ssr: false,
  loading: () => (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6" aria-busy="true">
      <div className="flex items-baseline gap-2.5">
        <span className="font-serif text-xl font-semibold tracking-tight">Prosa</span>
        <span className="text-xs text-muted">a carregar…</span>
      </div>
    </div>
  ),
});

export function WorkspaceLoader() {
  return <Workspace />;
}
