import { useMessages } from '@/locales';
import type { BridgeError } from '@/bridge';
import type { SaveState } from '@/store/useSettingsStore';
import styles from './SettingsToolbar.module.css';

interface SettingsToolbarProps {
  saveState: SaveState;
  lastError: BridgeError | null;
  onSave: () => void;
  onReset: () => void;
}

export function SettingsToolbar({
  saveState,
  lastError,
  onSave,
  onReset,
}: SettingsToolbarProps) {
  const messages = useMessages();
  const { settingsToolbar } = messages;

  const stateLabel = (() => {
    switch (saveState) {
      case 'saving':
        return settingsToolbar.saving;
      case 'saved':
        return settingsToolbar.saved;
      case 'error':
        return settingsToolbar.error;
      case 'idle':
      default:
        return settingsToolbar.idle;
    }
  })();

  const dotClass = `${styles.dot} ${
    saveState === 'saving'
      ? styles.saving
      : saveState === 'saved'
      ? styles.saved
      : saveState === 'error'
      ? styles.error
      : ''
  }`.trim();
  const errorText = lastError?.message || lastError?.code || "";

  return (
    <div className={styles.toolbar}>
      <div className={styles.state}>
        <span className={dotClass} aria-hidden />
        <span>{stateLabel}</span>
        {lastError ? (
          <span
            className={styles.errorMessage}
            title={`${lastError.code}: ${errorText}`}
          >
            {errorText}
          </span>
        ) : null}
      </div>
      <div className={styles.actions}>
        <button
          type="button"
          className={styles.button}
          onClick={onReset}
          disabled={saveState === 'saving'}
        >
          {settingsToolbar.reset}
        </button>
        <button
          type="button"
          className={`${styles.button} ${styles.primary}`}
          onClick={onSave}
          disabled={saveState === 'saving'}
        >
          {settingsToolbar.save}
        </button>
      </div>
    </div>
  );
}
