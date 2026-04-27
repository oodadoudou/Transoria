import { useId, useState } from 'react';
import nfStyles from './NumberField.module.css';
import tfStyles from './TextField.module.css';

interface TextFieldProps {
  label: string;
  value: string;
  onChange?: (v: string) => void;
  help?: string;
  placeholder?: string;
  /** Render as a multi-line textarea (3+ rows). */
  multiline?: boolean;
  rows?: number;
  /** Use the monospace font (e.g., model IDs, URLs, JSON snippets). */
  mono?: boolean;
}

/**
 * Text input with the same label · ? · field grammar as NumberField. Inputs
 * grow to fill the row's right column. Set `multiline` for textareas (used
 * for API key lists and custom-headers JSON).
 */
export function TextField({
  label,
  value,
  onChange,
  help,
  placeholder,
  multiline,
  rows = 3,
  mono,
}: TextFieldProps) {
  const [helpOpen, setHelpOpen] = useState(false);
  const helpId = useId();

  const inputClass = [
    nfStyles.input,
    tfStyles.text,
    mono ? tfStyles.mono : '',
    multiline ? tfStyles.multi : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={nfStyles.field}>
      <div className={`${nfStyles.row} ${tfStyles.row}`}>
        <div className={nfStyles.labelWrap}>
          <span className={nfStyles.label}>{label}</span>
          {help ? (
            <button
              type="button"
              className={nfStyles.help}
              aria-expanded={helpOpen}
              aria-controls={helpId}
              onClick={() => setHelpOpen(!helpOpen)}
              title={help}
            >
              ?
            </button>
          ) : null}
        </div>
        <div className={tfStyles.inputWrap}>
          {multiline ? (
            <textarea
              className={inputClass}
              value={value}
              rows={rows}
              placeholder={placeholder}
              onChange={(e) => onChange?.(e.target.value)}
              spellCheck={false}
              readOnly={!onChange}
            />
          ) : (
            <input
              type="text"
              className={inputClass}
              value={value}
              placeholder={placeholder}
              onChange={(e) => onChange?.(e.target.value)}
              spellCheck={false}
              readOnly={!onChange}
            />
          )}
        </div>
      </div>
      {help && helpOpen ? (
        <div id={helpId} className={nfStyles.hint}>
          {help}
        </div>
      ) : null}
    </div>
  );
}
