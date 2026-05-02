import { useMessages } from "@/locales";
import type { UpdateCheckResult } from "@/bridge";
import { Pill } from "./Pill";
import styles from "./UpdateAvailableModal.module.css";

const MAX_NOTE_LINES = 12;

interface UpdateAvailableModalProps {
  result: UpdateCheckResult;
  onDismiss: () => void;
  onUpdateNow: () => void;
}

export function UpdateAvailableModal({
  result,
  onDismiss,
  onUpdateNow,
}: UpdateAvailableModalProps) {
  const messages = useMessages().updatePrompt;
  const trimmedNotes = clampNotes(result.release_notes_markdown);
  const publishedAt = formatPublishedAt(result.published_at);

  return (
    <div
      className={styles.overlay}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="update-prompt-title"
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title} id="update-prompt-title">
            {messages.title}
          </h2>
          <span className={styles.versionBadge}>{result.latest_version}</span>
        </div>
        <div className={styles.body}>
          <p>
            {messages.bodyPrefix}
            <strong>{result.latest_version}</strong>
            {messages.bodySuffix}
          </p>
          {publishedAt ? (
            <div className={styles.metaRow}>
              <span className={styles.metaLabel}>
                {messages.publishedAtLabel}:
              </span>
              <span>{publishedAt}</span>
            </div>
          ) : null}
          <div className={styles.notesLabel}>{messages.notesLabel}</div>
          {trimmedNotes ? (
            <pre className={styles.notes}>{trimmedNotes}</pre>
          ) : (
            <p className={styles.notesEmpty}>{messages.notesEmpty}</p>
          )}
        </div>
        <div className={styles.footer}>
          <Pill variant="ghost" onClick={onDismiss}>
            {messages.laterAction}
          </Pill>
          <Pill onClick={onUpdateNow}>{messages.updateAction}</Pill>
        </div>
      </div>
    </div>
  );
}

function clampNotes(raw: string): string {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  if (lines.length <= MAX_NOTE_LINES) return raw.trim();
  return lines.slice(0, MAX_NOTE_LINES).join("\n").trimEnd() + "\n…";
}

function formatPublishedAt(raw: string): string {
  if (!raw) return "";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleDateString();
}
