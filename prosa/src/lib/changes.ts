import { diffArrays } from "diff";
import { joinTokens, tokenizeSentences, type TextToken } from "./text";

/**
 * Alterações frase a frase entre dois textos, para aceitar ou rejeitar
 * individualmente. Um bloco removido seguido de um bloco acrescentado é
 * tratado como substituição.
 */

export interface ChangeHunk {
  id: number;
  kind: "replace" | "insert" | "delete";
  /** Frases do texto de base (original). */
  from: string[];
  /** Frases do texto revisto. */
  to: string[];
}

export interface ChangesModel {
  /** Sequência de itens: texto igual ou uma alteração. */
  items: ({ kind: "same"; tokens: TextToken[] } | { kind: "change"; hunk: ChangeHunk })[];
  hunks: ChangeHunk[];
}

function key(token: TextToken): string {
  return token.kind === "break" ? "\u0000" : token.text.replace(/\s+/g, " ").trim();
}

export function computeChanges(base: string, revised: string, locale: string): ChangesModel {
  const a = tokenizeSentences(base, locale);
  const b = tokenizeSentences(revised, locale);
  const parts = diffArrays(a, b, { comparator: (x, y) => key(x) === key(y) });
  const items: ChangesModel["items"] = [];
  const hunks: ChangeHunk[] = [];
  let pendingRemoved: TextToken[] | null = null;
  let nextId = 0;

  const flushRemoved = () => {
    if (pendingRemoved) {
      const hunk: ChangeHunk = { id: nextId++, kind: "delete", from: sentencesOf(pendingRemoved), to: [] };
      hunks.push(hunk);
      items.push({ kind: "change", hunk });
      pendingRemoved = null;
    }
  };

  for (const part of parts) {
    if (part.removed) {
      flushRemoved();
      pendingRemoved = part.value;
    } else if (part.added) {
      const from = pendingRemoved ? sentencesOf(pendingRemoved) : [];
      pendingRemoved = null;
      const hunk: ChangeHunk = { id: nextId++, kind: from.length > 0 ? "replace" : "insert", from, to: sentencesOf(part.value) };
      hunks.push(hunk);
      items.push({ kind: "change", hunk });
    } else {
      flushRemoved();
      items.push({ kind: "same", tokens: part.value });
    }
  }
  flushRemoved();
  return { items, hunks };
}

function sentencesOf(tokens: TextToken[]): string[] {
  return tokens.map((t) => (t.kind === "break" ? "\n\n" : t.text));
}

function tokensOf(sentences: string[]): TextToken[] {
  return sentences.map((s) => (s === "\n\n" ? { kind: "break" } : { kind: "sentence", text: s }));
}

/**
 * Reconstrói o texto revisto substituindo, nas alterações rejeitadas,
 * as frases revistas pelas originais.
 */
export function applyDecisions(model: ChangesModel, rejected: Set<number>): string {
  const tokens: TextToken[] = [];
  for (const item of model.items) {
    if (item.kind === "same") tokens.push(...item.tokens);
    else tokens.push(...tokensOf(rejected.has(item.hunk.id) ? item.hunk.from : item.hunk.to));
  }
  return joinTokens(tokens);
}
