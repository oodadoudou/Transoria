import { type ReactNode, useEffect, useState } from "react";
import { useMessages } from "@/locales";
import { dialogsBridge, BridgeError } from "@/bridge";
import styles from "./FolderPickerRow.module.css";

const MAX_HISTORY_ITEMS = 5;
const HISTORY_PREFIX = "transoria.folderPicker.history.";

interface FolderPickerRowProps {
  label: string;
  value: string;
  variant: 'input' | 'output';
  onChange: (path: string) => void;
  onError?: (error: BridgeError) => void;
  compact?: boolean;
  historyKey?: string;
  help?: ReactNode;
}

/**
 * Folder selector with two paths in:
 * 1. Click "Choose folder" → native picker via `dialogsBridge`. When
 *    pywebview is present this opens an OS dialog; in browser dev mode
 *    the bridge throws and the user falls through to:
 * 2. Type/paste a path directly into the always-editable text input.
 *
 * The text input is the source of truth — both code paths feed
 * `onChange(path)` and re-render from the same `value` prop.
 */
export function FolderPickerRow({
  label,
  value,
  variant,
  onChange,
  onError,
  compact = false,
  historyKey,
  help,
}: FolderPickerRowProps) {
  const messages = useMessages();
  const buttonLabel = messages.folderPicker.choose;
  const placeholder = messages.folderPicker.placeholder;
  const recentLabel = messages.folderPicker.recent;
  const recentPlaceholder = messages.folderPicker.recentPlaceholder;
  const clearRecentLabel = messages.folderPicker.clearRecent;
  const [history, setHistory] = useState<string[]>(() =>
    historyKey ? loadHistory(historyKey) : [],
  );

  useEffect(() => {
    setHistory(historyKey ? loadHistory(historyKey) : []);
  }, [historyKey]);

  const recordPath = (path: string) => {
    if (!historyKey) return;
    const next = saveHistory(historyKey, path);
    setHistory(next);
  };

  const clearHistory = () => {
    if (!historyKey) return;
    removeHistory(historyKey);
    setHistory([]);
  };

  const handlePick = async () => {
    try {
      const result =
        variant === "input"
          ? await dialogsBridge.chooseInputDirectory(value || undefined)
          : await dialogsBridge.chooseOutputDirectory(value || undefined);
      if (result.path) {
        onChange(result.path);
        recordPath(result.path);
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error) && onError) {
        onError(error);
      }
    }
  };

  const handleRecentChange = (selection: string) => {
    if (!selection) return;
    if (selection === "__clear__") {
      clearHistory();
      return;
    }
    onChange(selection);
    recordPath(selection);
  };

  const commitManualPath = () => {
    recordPath(value);
  };

  return (
    <div className={compact ? `${styles.row} ${styles.compact}` : styles.row}>
      <div className={styles.field}>
        <span className={styles.label}>
          {label}
          {help}
        </span>
        <input
          type="text"
          className={styles.input}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onBlur={commitManualPath}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitManualPath();
          }}
          spellCheck={false}
        />
      </div>
      <div className={styles.actions}>
        {history.length > 0 ? (
          <label className={styles.recentWrap}>
            <span className={styles.srOnly}>{recentLabel}</span>
            <select
              className={styles.recentSelect}
              value=""
              onChange={(e) => handleRecentChange(e.target.value)}
              aria-label={recentLabel}
            >
              <option value="">{recentPlaceholder}</option>
              {history.map((path) => (
                <option key={path} value={path}>
                  {path}
                </option>
              ))}
              <option value="__clear__">{clearRecentLabel}</option>
            </select>
          </label>
        ) : null}
        <button type="button" className={styles.pickBtn} onClick={handlePick}>
          {buttonLabel}
        </button>
      </div>
    </div>
  );
}

function storageKey(historyKey: string): string {
  return `${HISTORY_PREFIX}${historyKey}`;
}

function loadHistory(historyKey: string): string[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(storageKey(historyKey)) ?? "[]",
    );
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === "string");
  } catch {
    return [];
  }
}

function saveHistory(historyKey: string, path: string): string[] {
  const normalized = path.trim();
  if (!normalized) return loadHistory(historyKey);
  const next = [
    normalized,
    ...loadHistory(historyKey).filter((item) => item !== normalized),
  ].slice(0, MAX_HISTORY_ITEMS);
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(storageKey(historyKey), JSON.stringify(next));
    } catch {
      // Recent paths are a convenience only; failing to persist is non-fatal.
    }
  }
  return next;
}

function removeHistory(historyKey: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(storageKey(historyKey));
  } catch {
    // Non-fatal convenience state.
  }
}
