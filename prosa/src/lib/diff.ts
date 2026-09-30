import { diffWords } from "diff";

export interface DiffSegment {
  value: string;
  kind: "same" | "added" | "removed";
}

export function computeWordDiff(original: string, revised: string): DiffSegment[] {
  return diffWords(original, revised).map((part) => ({
    value: part.value,
    kind: part.added ? "added" : part.removed ? "removed" : "same",
  }));
}

export function diffStats(segments: DiffSegment[]): { added: number; removed: number; unchanged: number } {
  const count = (kind: DiffSegment["kind"]) =>
    segments
      .filter((s) => s.kind === kind)
      .reduce((acc, s) => acc + (s.value.match(/\S+/g)?.length ?? 0), 0);
  return { added: count("added"), removed: count("removed"), unchanged: count("same") };
}
