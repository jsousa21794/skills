interface Props {
  completed: number;
  total: number;
  active: boolean;
  label?: string;
}

export function ProgressBar({ completed, total, active, label }: Props) {
  if (!active) return null;
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  const indeterminate = total === 0;
  return (
    <div className="fade-up flex items-center gap-3 text-xs text-muted" role="status" aria-live="polite">
      <div className="h-1.5 w-40 overflow-hidden rounded-full bg-line" aria-hidden>
        {indeterminate ? (
          <div className="progress-indeterminate h-full w-full" />
        ) : (
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-300"
            style={{ width: `${Math.max(4, percent)}%`, transitionTimingFunction: "var(--ease-out)" }}
          />
        )}
      </div>
      <span>
        {label ?? (total > 1 ? `Secção ${Math.min(completed + 1, total)} de ${total}` : "A rever…")}
      </span>
    </div>
  );
}
