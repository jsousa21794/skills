/**
 * Persistência local opcional. Nada de texto é guardado por defeito:
 * só as preferências de interface e, se o utilizador o ativar, o histórico.
 */

const PREFIX = "prosa:";

export const STORAGE_KEYS = {
  theme: `${PREFIX}theme`,
  settings: `${PREFIX}settings`,
  profile: `${PREFIX}profile`,
  historyEnabled: `${PREFIX}history-enabled`,
  history: `${PREFIX}history`,
} as const;

export function readJson<T>(key: string): T | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function writeJson(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Sem espaço ou armazenamento bloqueado: a aplicação continua a funcionar em memória.
  }
}

export function remove(key: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignorar
  }
}

/** Apaga tudo o que a aplicação guardou neste browser. */
export function clearAll(): void {
  if (typeof window === "undefined") return;
  try {
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key && key.startsWith(PREFIX)) keys.push(key);
    }
    keys.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    // ignorar
  }
}
