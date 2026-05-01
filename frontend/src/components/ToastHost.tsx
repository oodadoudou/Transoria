import { useToastStore, type Toast } from "@/store/useToastStore";
import styles from "./ToastHost.module.css";

/**
 * Mounts a fixed-position stack of toasts that listen to ``useToastStore``.
 * Mount once at app root; ``useToastStore.getState().push(...)`` triggers
 * a render anywhere in the tree.
 */
export function ToastHost(): JSX.Element | null {
  const toasts = useToastStore((state) => state.toasts);
  const dismiss = useToastStore((state) => state.dismiss);

  if (toasts.length === 0) return null;

  return (
    <div className={styles.host} role="region" aria-label="notifications">
      {toasts.map((toast) => (
        <ToastCard key={toast.id} toast={toast} onClose={() => dismiss(toast.id)} />
      ))}
    </div>
  );
}

function ToastCard({
  toast,
  onClose,
}: {
  toast: Toast;
  onClose: () => void;
}): JSX.Element {
  return (
    <div
      className={`${styles.card} ${styles[toast.variant]}`}
      role={toast.variant === "error" ? "alert" : "status"}
      aria-live={toast.variant === "error" ? "assertive" : "polite"}
    >
      <div className={styles.body}>
        <span className={styles.title}>{toast.title}</span>
        {toast.detail ? (
          <span className={styles.detail}>{toast.detail}</span>
        ) : null}
      </div>
      <button
        type="button"
        className={styles.close}
        aria-label="dismiss"
        onClick={onClose}
      >
        ×
      </button>
    </div>
  );
}
