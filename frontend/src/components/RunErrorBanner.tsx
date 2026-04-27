import { useMessages } from '@/locales';
import { useRuntimeStore, type RunKind } from '@/store/useRuntimeStore';
import styles from './RunErrorBanner.module.css';

interface RunErrorBannerProps {
  kind: RunKind;
}

export function RunErrorBanner({ kind }: RunErrorBannerProps) {
  const messages = useMessages();
  const error = useRuntimeStore((state) => state[kind].lastError);
  const clearError = useRuntimeStore((state) => state.clearError);

  if (!error) return null;

  return (
    <div className={styles.banner} role="alert">
      <div className={styles.body}>
        <span className={styles.title}>{messages.errors.runFailureTitle}</span>
        <span className={styles.message}>{error.message}</span>
        <span className={styles.code}>{error.code}</span>
      </div>
      <button
        type="button"
        className={styles.dismiss}
        onClick={() => clearError(kind)}
      >
        {messages.errors.dismiss}
      </button>
    </div>
  );
}
