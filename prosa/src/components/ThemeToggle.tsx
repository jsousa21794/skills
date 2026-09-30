"use client";

import { useState } from "react";
import { STORAGE_KEYS } from "@/lib/client/storage";
import { IconMoon, IconSun } from "./Icons";

type Theme = "light" | "dark";

function currentTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

/** Renderizado apenas no cliente (o Workspace é carregado sem SSR), por isso pode ler o DOM ao iniciar. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(currentTheme);

  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEYS.theme, next);
    } catch {
      // ignorar
    }
    setTheme(next);
  };

  return (
    <button type="button" className="btn btn-ghost" onClick={toggle} aria-label={theme === "dark" ? "Mudar para tema claro" : "Mudar para tema escuro"} title="Alternar tema">
      {theme === "dark" ? <IconSun /> : <IconMoon />}
    </button>
  );
}
