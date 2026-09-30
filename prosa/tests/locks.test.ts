import { describe, expect, it } from "vitest";
import { countOccurrences, normalizeLockedTerms, verifyLocks } from "@/lib/locks";

describe("locks", () => {
  it("normaliza termos: remove vazios, espaços e duplicados", () => {
    expect(normalizeLockedTerms(["  ACME ", "", "ACME", "Q3 2025"])).toEqual(["ACME", "Q3 2025"]);
    expect(normalizeLockedTerms(undefined)).toEqual([]);
  });

  it("conta ocorrências literais", () => {
    expect(countOccurrences("a b a b a", "a")).toBe(3);
    expect(countOccurrences("aaa", "aa")).toBe(1);
    expect(countOccurrences("x", "")).toBe(0);
  });

  it("assinala termos que desapareceram ou perderam ocorrências", () => {
    const original = "A ACME lançou o produto. A ACME cresceu 12%.";
    const rewritten = "A empresa lançou o produto e cresceu 12%. A ACME é referência.";
    expect(verifyLocks(original, rewritten, ["ACME", "12%", "Inexistente"])).toEqual([{ term: "ACME", expected: 2, found: 1 }]);
  });

  it("não assinala nada quando tudo se mantém", () => {
    expect(verifyLocks("Dr. Silva, 2019.", "Em 2019, o Dr. Silva.", ["Dr. Silva", "2019"])).toEqual([]);
  });
});
