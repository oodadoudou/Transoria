import { useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import type { GlossaryEntry } from "@/store/useTaskStore";
import styles from "./GlossaryStatsModal.module.css";

interface GlossaryStatsModalProps {
  entries: GlossaryEntry[];
  onClose: () => void;
}

export function GlossaryStatsModal({
  entries,
  onClose,
}: GlossaryStatsModalProps) {
  const messages = useMessages();
  const labels = messages.glossaryStats;
  useEscapeKey(onClose);

  const total = entries.length;
  const enabled = entries.filter((e) => e.enabled).length;
  const uniqueSrc = new Set(entries.map((e) => e.source)).size;
  const caseSensitive = entries.filter((e) => e.caseSensitive).length;
  const avgSrcLen =
    total === 0
      ? 0
      : Math.round(
          entries.reduce((sum, e) => sum + e.source.length, 0) / total,
        );
  const avgDstLen =
    total === 0
      ? 0
      : Math.round(
          entries.reduce((sum, e) => sum + e.translation.length, 0) / total,
        );

  const infoCounts = new Map<string, number>();
  for (const entry of entries) {
    const key = entry.description.trim() || labels.uncategorized;
    infoCounts.set(key, (infoCounts.get(key) ?? 0) + 1);
  }
  const topInfo = [...infoCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>{labels.title}</h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={onClose}
            aria-label={labels.close}
          >
            ×
          </button>
        </div>
        <div className={styles.body}>
          <div className={styles.statGrid}>
            <Stat label={labels.total} value={total} />
            <Stat label={labels.enabled} value={enabled} />
            <Stat label={labels.disabled} value={total - enabled} />
            <Stat label={labels.uniqueSrc} value={uniqueSrc} />
            <Stat label={labels.caseSensitive} value={caseSensitive} />
            <Stat
              label={labels.avgLen}
              value={`${avgSrcLen} / ${avgDstLen}`}
            />
          </div>

          <div className={styles.section}>
            <div className={styles.sectionLabel}>{labels.topInfo}</div>
            {topInfo.length === 0 ? (
              <div className={styles.empty}>{labels.empty}</div>
            ) : (
              <ul className={styles.bars}>
                {topInfo.map(([info, count]) => (
                  <li key={info} className={styles.bar}>
                    <span className={styles.barLabel}>{info}</span>
                    <span className={styles.barTrack}>
                      <span
                        className={styles.barFill}
                        style={{
                          width: `${Math.max(4, (count / topInfo[0][1]) * 100)}%`,
                        }}
                      />
                    </span>
                    <span className={styles.barCount}>{count}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className={styles.stat}>
      <div className={styles.statValue}>{value}</div>
      <div className={styles.statLabel}>{label}</div>
    </div>
  );
}
