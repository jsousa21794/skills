import { describe, expect, it } from "vitest";
import { checkPreservation } from "@/lib/guard";

describe("checkPreservation", () => {
  it("não avisa quando números, datas e citações se mantêm", () => {
    const original = "Em 2021 vendemos 1.250 unidades, «um recorde histórico para a equipa», a 3,5% de margem.";
    const rewritten = "A 3,5% de margem, vendemos 1.250 unidades em 2021: «um recorde histórico para a equipa».";
    expect(checkPreservation(original, rewritten)).toEqual([]);
  });

  it("avisa quando um número desaparece", () => {
    const warnings = checkPreservation("Crescemos 12% em 2020.", "Crescemos bastante em 2020.");
    expect(warnings.some((w) => w.includes("12%"))).toBe(true);
  });

  it("avisa quando uma data muda", () => {
    const warnings = checkPreservation("Fundada em 1998.", "Fundada em 1989.");
    expect(warnings.join(" ")).toMatch(/1998/);
  });

  it("avisa quando uma citação é alterada", () => {
    const warnings = checkPreservation("Ele disse: «nunca desistimos do plano».", "Ele disse: «nunca abandonámos o plano».");
    expect(warnings.some((w) => w.toLowerCase().includes("citaç"))).toBe(true);
  });

  it("avisa quando o texto encolhe demasiado", () => {
    const original = Array.from({ length: 60 }, (_, i) => `palavra${i}`).join(" ");
    const warnings = checkPreservation(original, "curto.");
    expect(warnings.some((w) => w.includes("mais curta"))).toBe(true);
  });
});
