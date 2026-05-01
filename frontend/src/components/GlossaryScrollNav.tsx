import { useMessages } from "@/locales";
import styles from "./GlossaryScrollNav.module.css";

interface GlossaryScrollNavProps {
  onTop: () => void;
  onBottom: () => void;
}

export function GlossaryScrollNav({ onTop, onBottom }: GlossaryScrollNavProps) {
  const messages = useMessages();
  const labels = messages.glossaryScrollNav;

  return (
    <div className={styles.host} aria-hidden={false}>
      <button
        type="button"
        className={styles.btn}
        onClick={onTop}
        aria-label={labels.top}
        title={labels.top}
      >
        <svg
          className={styles.icon}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
        >
          <path
            d="M3.5 9.5L8 5l4.5 4.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      <button
        type="button"
        className={styles.btn}
        onClick={onBottom}
        aria-label={labels.bottom}
        title={labels.bottom}
      >
        <svg
          className={styles.icon}
          viewBox="0 0 16 16"
          fill="none"
          aria-hidden
        >
          <path
            d="M3.5 6.5L8 11l4.5-4.5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
    </div>
  );
}
