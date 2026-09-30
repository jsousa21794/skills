import type { Ambiguity } from "@/lib/types";
import { IconInfo, IconWarning } from "./Icons";

interface Props {
  ambiguities: Ambiguity[];
  warnings: string[];
}

export function NotesPanel({ ambiguities, warnings }: Props) {
  if (ambiguities.length === 0 && warnings.length === 0) return null;
  return (
    <section className="surface fade-up p-4" aria-labelledby="notes-title">
      <h2 id="notes-title" className="mb-3 text-sm font-semibold">
        Notas da revisão
      </h2>
      {warnings.length > 0 && (
        <ul className="mb-3 flex flex-col gap-2">
          {warnings.map((warning, i) => (
            <li key={i} className="flex gap-2 rounded-lg bg-warning p-2.5 text-xs leading-relaxed text-warning-fg">
              <IconWarning size={14} className="mt-0.5 shrink-0" />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
      {ambiguities.length > 0 && (
        <ul className="flex flex-col gap-2">
          {ambiguities.map((item, i) => (
            <li key={i} className="flex gap-2 rounded-lg border p-2.5 text-xs leading-relaxed">
              <IconInfo size={14} className="mt-0.5 shrink-0 text-accent" />
              <div>
                <p className="mb-0.5 font-medium">«{item.excerpt}»</p>
                <p className="text-muted">{item.note}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
