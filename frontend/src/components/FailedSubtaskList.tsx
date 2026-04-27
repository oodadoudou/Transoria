import type { TaskFailure } from '@/bridge';
import styles from './FailedSubtaskList.module.css';

interface FailedSubtaskListProps {
  failures: TaskFailure[];
}

export function FailedSubtaskList({ failures }: FailedSubtaskListProps) {
  if (!failures.length) return null;
  return (
    <div className={styles.list}>
      {failures.slice(0, 10).map((failure) => (
        <div key={failure.subtask_id} className={styles.row}>
          <div className={styles.head}>
            <code className={styles.code}>{failure.last_error_code}</code>
            <span className={styles.attempts}>
              {failure.attempts} attempt{failure.attempts === 1 ? '' : 's'}
            </span>
          </div>
          <div className={styles.path}>{failure.source_file}</div>
          <div className={styles.message}>{failure.message}</div>
        </div>
      ))}
      {failures.length > 10 ? (
        <div className={styles.more}>
          + {failures.length - 10} more
        </div>
      ) : null}
    </div>
  );
}
