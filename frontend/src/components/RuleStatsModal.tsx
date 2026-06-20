import { useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import type {
  PersistedTextPreserveRule,
  PersistedTranslationReplacementRule,
} from "@/bridge";
import styles from "./GlossaryStatsModal.module.css";

type Props =
  | {
      kind: "text_preserve";
      rules: PersistedTextPreserveRule[];
      onClose: () => void;
    }
  | {
      kind: "pre_replacement" | "post_replacement";
      rules: PersistedTranslationReplacementRule[];
      onClose: () => void;
    };

export function RuleStatsModal(props: Props) {
  const labels = useMessages().ruleStats;
  const { rules } = props;
  useEscapeKey(props.onClose);

  const total = rules.length;
  const enabled = rules.filter((r) => r.enabled).length;

  const noteCounts = new Map<string, number>();
  for (const rule of rules) {
    const note =
      ("note" in rule ? rule.note : "")?.toString().trim() ||
      labels.uncategorized;
    noteCounts.set(note, (noteCounts.get(note) ?? 0) + 1);
  }
  const topNote = [...noteCounts.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={props.onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>{labels.title}</h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={props.onClose}
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
            {props.kind === "text_preserve" ? (
              <Stat
                label={labels.avgPatternLen}
                value={
                  total === 0
                    ? 0
                    : Math.round(
                        props.rules.reduce(
                          (sum, r) => sum + r.pattern.length,
                          0,
                        ) / total,
                      )
                }
              />
            ) : (
              <>
                <Stat
                  label={labels.regexCount}
                  value={props.rules.filter((r) => r.regex).length}
                />
                <Stat
                  label={labels.caseSensitive}
                  value={props.rules.filter((r) => r.case_sensitive).length}
                />
                <Stat
                  label={labels.avgSrcDstLen}
                  value={
                    total === 0
                      ? "0 / 0"
                      : `${Math.round(
                          props.rules.reduce(
                            (sum, r) => sum + r.src.length,
                            0,
                          ) / total,
                        )} / ${Math.round(
                          props.rules.reduce(
                            (sum, r) => sum + r.dst.length,
                            0,
                          ) / total,
                        )}`
                  }
                />
              </>
            )}
          </div>

          <div className={styles.section}>
            <div className={styles.sectionLabel}>{labels.topNote}</div>
            {topNote.length === 0 ? (
              <div className={styles.empty}>{labels.empty}</div>
            ) : (
              <ul className={styles.bars}>
                {topNote.map(([note, count]) => (
                  <li key={note} className={styles.bar}>
                    <span className={styles.barLabel}>{note}</span>
                    <span className={styles.barTrack}>
                      <span
                        className={styles.barFill}
                        style={{
                          width: `${Math.max(4, (count / topNote[0][1]) * 100)}%`,
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
