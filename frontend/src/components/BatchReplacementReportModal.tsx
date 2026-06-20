import { useMemo, useState } from "react";
import { format, useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import type {
  ReplacementReport,
  ReplacementReportRule,
} from "@/bridge";
import styles from "./BatchReplacementReportModal.module.css";

interface BatchReplacementReportModalProps {
  report: ReplacementReport;
  onClose: () => void;
}

const NUM = new Intl.NumberFormat("en");

export function BatchReplacementReportModal({
  report,
  onClose,
}: BatchReplacementReportModalProps) {
  const messages = useMessages();
  const labels = messages.batchReplacementReport;
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(() => {
    // Auto-expand the first few rules with matches so the modal feels
    // populated even before the user clicks anything. Beyond that we
    // collapse — long rule lists would otherwise dump hundreds of
    // snippets into the viewport on open.
    const initial = new Set<number>();
    let opened = 0;
    for (const rule of report.rules) {
      if (rule.total_count === 0) continue;
      initial.add(rule.rule_index);
      opened += 1;
      if (opened >= 3) break;
    }
    return initial;
  });
  useEscapeKey(onClose);

  const filteredRules = useMemo(
    () => filterRules(report.rules, query.trim().toLowerCase()),
    [report.rules, query],
  );

  const allExpanded =
    filteredRules.length > 0 &&
    filteredRules.every((r) => expanded.has(r.rule_index));

  const toggleAll = () => {
    if (allExpanded) {
      setExpanded(new Set());
    } else {
      setExpanded(new Set(filteredRules.map((r) => r.rule_index)));
    }
  };

  const toggleOne = (ruleIndex: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(ruleIndex)) next.delete(ruleIndex);
      else next.add(ruleIndex);
      return next;
    });
  };

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>{labels.title}</h2>
          <button
            type="button"
            className={styles.close}
            aria-label={labels.close}
            onClick={onClose}
          >
            ×
          </button>
        </div>

        <div className={styles.summary}>
          <Stat
            label={labels.summary.totalReplacements}
            value={NUM.format(report.totals.total_replacements)}
          />
          <Stat
            label={labels.summary.rulesWithMatches}
            value={`${report.totals.rules_with_matches} / ${report.totals.rules_active}`}
          />
          <Stat
            label={labels.summary.filesProcessed}
            value={NUM.format(report.totals.files_processed)}
          />
          <Stat
            label={labels.summary.generatedAt}
            value={formatGeneratedAt(report.generated_at)}
          />
        </div>

        <div className={styles.toolbar}>
          <input
            type="search"
            className={styles.search}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={labels.searchPlaceholder}
            aria-label={labels.searchPlaceholder}
          />
          <button
            type="button"
            className={styles.expandToggle}
            onClick={toggleAll}
            disabled={filteredRules.length === 0}
          >
            {allExpanded ? labels.collapseAll : labels.expandAll}
          </button>
        </div>

        <div className={styles.body}>
          {filteredRules.length === 0 ? (
            <div className={styles.empty}>
              {query
                ? format(labels.noResults, { query })
                : labels.noRules}
            </div>
          ) : (
            filteredRules.map((rule) => (
              <RuleGroup
                key={rule.rule_index}
                rule={rule}
                expanded={expanded.has(rule.rule_index)}
                onToggle={() => toggleOne(rule.rule_index)}
                query={query.trim()}
                labels={labels}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

interface StatProps {
  label: string;
  value: string;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className={styles.summaryStat}>
      <span className={styles.summaryValue}>{value}</span>
      <span className={styles.summaryLabel}>{label}</span>
    </div>
  );
}

interface RuleGroupProps {
  rule: ReplacementReportRule;
  expanded: boolean;
  onToggle: () => void;
  query: string;
  labels: ReturnType<typeof useMessages>["batchReplacementReport"];
}

function RuleGroup({
  rule,
  expanded,
  onToggle,
  query,
  labels,
}: RuleGroupProps) {
  const matchingOccurrences = useMemo(() => {
    if (!query) return rule.occurrences;
    const needle = query.toLowerCase();
    return rule.occurrences.filter((occ) =>
      [
        occ.match_text,
        occ.replacement_text,
        occ.before_context,
        occ.after_context,
        occ.file_path,
      ]
        .some((s) => s.toLowerCase().includes(needle)),
    );
  }, [rule.occurrences, query]);

  return (
    <div className={styles.ruleGroup}>
      <button
        type="button"
        className={styles.ruleHeader}
        onClick={onToggle}
        aria-expanded={expanded}
      >
        <span
          className={`${styles.chevron} ${expanded ? styles.chevronOpen : ""}`.trim()}
          aria-hidden
        >
          ›
        </span>
        <span className={styles.rulePhrase}>{rule.src}</span>
        <span className={styles.ruleArrow} aria-hidden>
          →
        </span>
        <span className={styles.rulePhrase}>{rule.dst}</span>
        {!rule.enabled ? (
          <span className={styles.ruleBadgeOff}>{labels.disabledBadge}</span>
        ) : null}
        <span className={styles.ruleCount}>
          {format(labels.matchCount, { n: rule.total_count })}
        </span>
      </button>

      {expanded ? (
        <div className={styles.ruleBody}>
          {rule.total_count === 0 ? (
            <div className={styles.snippetEmpty}>{labels.noMatchesForRule}</div>
          ) : matchingOccurrences.length === 0 ? (
            <div className={styles.snippetEmpty}>
              {labels.noOccurrenceMatchesQuery}
            </div>
          ) : (
            <>
              {matchingOccurrences.map((occ, i) => (
                <Snippet key={i} occurrence={occ} labels={labels} />
              ))}
              {rule.occurrences_truncated ? (
                <div className={styles.snippetTruncated}>
                  {format(labels.truncated, {
                    shown: rule.occurrences.length,
                    total: rule.total_count,
                  })}
                </div>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

interface SnippetProps {
  occurrence: import("@/bridge").ReplacementReportOccurrence;
  labels: ReturnType<typeof useMessages>["batchReplacementReport"];
}

function Snippet({ occurrence, labels }: SnippetProps) {
  return (
    <div className={styles.snippet}>
      <div className={styles.snippetMeta}>
        {labels.fileLabel}: {basename(occurrence.file_path)}
      </div>
      <div className={styles.snippetLine}>
        <span>{occurrence.before_context}</span>
        <span className={styles.matchHighlight}>{occurrence.match_text}</span>
        <span>{occurrence.after_context}</span>
      </div>
      <div className={styles.snippetLine}>
        <span>{occurrence.before_context}</span>
        <span className={styles.replacementHighlight}>
          {occurrence.replacement_text}
        </span>
        <span>{occurrence.after_context}</span>
      </div>
    </div>
  );
}

function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function filterRules(
  rules: ReplacementReportRule[],
  query: string,
): ReplacementReportRule[] {
  if (!query) return rules;
  return rules.filter((rule) => {
    const haystacks = [
      rule.src,
      rule.dst,
      ...rule.occurrences.flatMap((occ) => [
        occ.match_text,
        occ.replacement_text,
        occ.before_context,
        occ.after_context,
        occ.file_path,
      ]),
    ];
    return haystacks.some((s) => s.toLowerCase().includes(query));
  });
}

function formatGeneratedAt(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  // Local time, no timezone label — "2026-05-02 11:30".
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}
