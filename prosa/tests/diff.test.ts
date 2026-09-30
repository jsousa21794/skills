import { describe, expect, it } from "vitest";
import { computeWordDiff, diffStats } from "@/lib/diff";

describe("diff", () => {
  it("marca palavras acrescentadas e removidas", () => {
    const segments = computeWordDiff("O gato dorme.", "O gato preto dorme muito.");
    const added = segments.filter((s) => s.kind === "added").map((s) => s.value.trim());
    expect(added).toContain("preto");
    expect(added).toContain("muito");
    expect(segments.some((s) => s.kind === "removed")).toBe(false);
    expect(diffStats(segments)).toEqual({ added: 2, removed: 0, unchanged: 4 });
  });

  it("texto igual não tem alterações", () => {
    const segments = computeWordDiff("igual", "igual");
    expect(segments.every((s) => s.kind === "same")).toBe(true);
  });
});
