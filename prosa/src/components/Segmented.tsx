"use client";

interface Option<T extends string> {
  value: T;
  label: string;
  title?: string;
}

interface Props<T extends string> {
  value: T;
  options: readonly Option<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
}

export function Segmented<T extends string>({ value, options, onChange, ariaLabel, className }: Props<T>) {
  return (
    <div className={`segmented ${className ?? ""}`} role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          title={option.title}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
