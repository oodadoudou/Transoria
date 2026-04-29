import { useMessages } from '@/locales';
import { dialogsBridge, BridgeError } from '@/bridge';
import styles from './FolderPickerRow.module.css';

interface FolderPickerRowProps {
  label: string;
  value: string;
  variant: 'input' | 'output';
  onChange: (path: string) => void;
  onError?: (error: BridgeError) => void;
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
}: FolderPickerRowProps) {
  const messages = useMessages();
  const buttonLabel = messages.folderPicker.choose;
  const placeholder = messages.folderPicker.placeholder;

  const handlePick = async () => {
    try {
      const result =
        variant === 'input'
          ? await dialogsBridge.chooseInputDirectory(value || undefined)
          : await dialogsBridge.chooseOutputDirectory(value || undefined);
      if (result.path) onChange(result.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error) && onError) {
        onError(error);
      }
    }
  };

  return (
    <div className={styles.row}>
      <div className={styles.field}>
        <span className={styles.label}>{label}</span>
        <input
          type="text"
          className={styles.input}
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
        />
      </div>
      <button type="button" className={styles.pickBtn} onClick={handlePick}>
        {buttonLabel}
      </button>
    </div>
  );
}
