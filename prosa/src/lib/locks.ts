/**
 * Termos e passagens bloqueados: o utilizador indica texto que não pode mudar.
 * A estratégia é dupla: instruir o modelo a preservar cada termo literalmente
 * e verificar depois se cada um continua presente o mesmo número de vezes.
 */

export function normalizeLockedTerms(terms: string[] | undefined): string[] {
  if (!terms) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of terms) {
    const term = raw.trim();
    if (!term || seen.has(term)) continue;
    seen.add(term);
    out.push(term);
  }
  return out;
}

export function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0;
  let count = 0;
  let index = haystack.indexOf(needle);
  while (index !== -1) {
    count += 1;
    index = haystack.indexOf(needle, index + needle.length);
  }
  return count;
}

export interface LockCheck {
  term: string;
  expected: number;
  found: number;
}

/**
 * Compara ocorrências de cada termo bloqueado no original e na reescrita.
 * Só os termos presentes no original são relevantes para a secção em causa.
 */
export function verifyLocks(original: string, rewritten: string, terms: string[]): LockCheck[] {
  const problems: LockCheck[] = [];
  for (const term of terms) {
    const expected = countOccurrences(original, term);
    if (expected === 0) continue;
    const found = countOccurrences(rewritten, term);
    if (found < expected) problems.push({ term, expected, found });
  }
  return problems;
}
