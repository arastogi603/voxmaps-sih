import type { ChangeEvent, ReactNode } from "react";
import { Info, LockKeyhole } from "lucide-react";

export function Card({ children, className = "", id }: { children: ReactNode; className?: string; id?: string }) {
  return <section id={id} className={`card ${className}`.trim()}>{children}</section>;
}

export function SectionHeading({ eyebrow, title, description, action }: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </div>
      {action && <div className="section-action">{action}</div>}
    </div>
  );
}

export function FieldLabel({ children, tooltip, unit, required = false }: {
  children: ReactNode;
  tooltip?: string;
  unit?: string;
  required?: boolean;
}) {
  return (
    <span className="field-label">
      <span>{children}{required && <span className="required"> *</span>}</span>
      {unit && <span className="unit-pill">{unit}</span>}
      {tooltip && <span className="tooltip" title={tooltip} aria-label={tooltip}><Info size={13} /></span>}
    </span>
  );
}

export function NumberField({
  label,
  value,
  onChange,
  unit,
  tooltip,
  min,
  max,
  step = 1,
  disabled = false,
  error,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  unit?: string;
  tooltip?: string;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  error?: string;
}) {
  const update = (event: ChangeEvent<HTMLInputElement>) => {
    const next = event.target.valueAsNumber;
    if (Number.isFinite(next)) onChange(next);
  };
  return (
    <label className={`field ${error ? "field-error" : ""}`}>
      <FieldLabel tooltip={tooltip} unit={unit}>{label}</FieldLabel>
      <div className="number-box">
        <input type="number" value={value} min={min} max={max} step={step} disabled={disabled} onChange={update} />
        {unit && <span>{unit}</span>}
      </div>
      {error && <small>{error}</small>}
    </label>
  );
}

export function RangeNumberField({
  label,
  value,
  onChange,
  unit,
  tooltip,
  min,
  max,
  step = 1,
  disabled = false,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  unit?: string;
  tooltip?: string;
  min: number;
  max: number;
  step?: number;
  disabled?: boolean;
}) {
  const update = (event: ChangeEvent<HTMLInputElement>) => {
    const next = event.target.valueAsNumber;
    if (Number.isFinite(next)) onChange(next);
  };
  return (
    <label className="field range-number-field">
      <FieldLabel tooltip={tooltip} unit={unit}>{label}</FieldLabel>
      <div className="range-number-row">
        <input
          className="range-input"
          type="range"
          aria-label={`${label} slider`}
          value={Math.min(max, Math.max(min, value))}
          min={min}
          max={max}
          step={step}
          disabled={disabled}
          onChange={update}
        />
        <div className="number-box compact">
          <input type="number" aria-label={`${label} exact value`} value={value} min={min} max={max} step={step} disabled={disabled} onChange={update} />
          {unit && <span>{unit}</span>}
        </div>
      </div>
      <div className="range-bounds"><span>{min}</span><span>{max}</span></div>
    </label>
  );
}

export function TextField({ label, value, onChange, placeholder, tooltip, disabled = false, mono = false }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  tooltip?: string;
  disabled?: boolean;
  mono?: boolean;
}) {
  return (
    <label className="field">
      <FieldLabel tooltip={tooltip}>{label}</FieldLabel>
      <input className={`text-input ${mono ? "mono" : ""}`} value={value} placeholder={placeholder} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function SelectField<T extends string>({ label, value, onChange, options, tooltip, disabled = false }: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string; description?: string }>;
  tooltip?: string;
  disabled?: boolean;
}) {
  return (
    <label className="field">
      <FieldLabel tooltip={tooltip}>{label}</FieldLabel>
      <select className="select-input" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value as T)}>
        {options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}
      </select>
      {options.find((option) => option.value === value)?.description && (
        <small className="field-help">{options.find((option) => option.value === value)?.description}</small>
      )}
    </label>
  );
}

export function Toggle({ label, checked, onChange, description, disabled = false }: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: string;
  disabled?: boolean;
}) {
  return (
    <label className={`toggle-row ${disabled ? "disabled" : ""}`}>
      <span>
        <strong>{label}</strong>
        {description && <small>{description}</small>}
      </span>
      <button
        type="button"
        className={`toggle ${checked ? "on" : ""}`}
        aria-pressed={checked}
        aria-label={label}
        onClick={() => !disabled && onChange(!checked)}
        disabled={disabled}
      ><span /></button>
    </label>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label }: {
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: string; title?: string }>;
  label: string;
}) {
  return (
    <div className="segmented-wrap">
      <FieldLabel>{label}</FieldLabel>
      <div className="segmented" role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            title={option.title}
            className={value === option.value ? "active" : ""}
            onClick={() => onChange(option.value)}
            aria-pressed={value === option.value}
          >{option.label}</button>
        ))}
      </div>
    </div>
  );
}

export function LockedValue({ label, value, explanation }: { label: string; value: string; explanation?: string }) {
  return (
    <div className="locked-value">
      <div><LockKeyhole size={14} /><span>{label}</span></div>
      <strong>{value}</strong>
      {explanation && <small>{explanation}</small>}
    </div>
  );
}

export function EmptyState({ icon, title, description, action }: { icon?: ReactNode; title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
