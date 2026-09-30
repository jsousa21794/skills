/**
 * Verificações pós-reescrita, independentes do modelo. Não corrigem o texto:
 * produzem avisos que a interface mostra ao utilizador.
 */

const NUMBER_RE = /\d+(?:[.,]\d+)*%?/g;
const YEAR_RE = /\b(1[5-9]\d{2}|20\d{2}|21\d{2})\b/g;
const QUOTE_RE = /[«“"]([^«»“”"]{12,})[»”"]/g;

function collect(text: string, re: RegExp): Map<string, number> {
  const map = new Map<string, number>();
  for (const match of text.matchAll(re)) {
    const key = match[1] ?? match[0];
    map.set(key, (map.get(key) ?? 0) + 1);
  }
  return map;
}

function missing(original: Map<string, number>, rewritten: Map<string, number>): string[] {
  const out: string[] = [];
  for (const [key, count] of original) {
    if ((rewritten.get(key) ?? 0) < count) out.push(key);
  }
  return out;
}

export function checkPreservation(original: string, rewritten: string): string[] {
  const warnings: string[] = [];

  const lostNumbers = missing(collect(original, NUMBER_RE), collect(rewritten, NUMBER_RE));
  if (lostNumbers.length > 0) {
    warnings.push(
      `Números presentes no original não aparecem iguais na revisão: ${lostNumbers.slice(0, 6).join(", ")}${lostNumbers.length > 6 ? "…" : ""}. Confirma se foram reescritos por extenso ou perdidos.`,
    );
  }

  const lostYears = missing(collect(original, YEAR_RE), collect(rewritten, YEAR_RE));
  const yearsNotInNumbers = lostYears.filter((y) => !lostNumbers.includes(y));
  if (yearsNotInNumbers.length > 0) {
    warnings.push(`Datas possivelmente alteradas: ${yearsNotInNumbers.join(", ")}.`);
  }

  const lostQuotes = missing(collect(original, QUOTE_RE), collect(rewritten, QUOTE_RE));
  if (lostQuotes.length > 0) {
    warnings.push(
      `Uma ou mais citações entre aspas foram alteradas ou removidas. Confirma: “${lostQuotes[0].slice(0, 60)}${lostQuotes[0].length > 60 ? "…" : ""}”.`,
    );
  }

  const ratio = rewritten.trim().length / Math.max(1, original.trim().length);
  if (ratio < 0.35) {
    warnings.push("A revisão ficou muito mais curta do que o original. Verifica se falta conteúdo.");
  }

  return warnings;
}
