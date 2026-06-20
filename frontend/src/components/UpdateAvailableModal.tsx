import { useMessages } from "@/locales";
import type { UpdateCheckResult } from "@/bridge";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { Pill } from "./Pill";
import type { AutoUpdateState } from "./useUpdatePrompt";
import styles from "./UpdateAvailableModal.module.css";

const MAX_NOTE_LINES = 12;

interface UpdateAvailableModalProps {
  result: UpdateCheckResult;
  canAutoUpdate: boolean;
  autoUpdateState: AutoUpdateState;
  autoUpdateError: string | null;
  shutdownInSeconds: number | null;
  onDismiss: () => void;
  onUpdateNow: () => void;
  onAutoUpdate: () => void;
}

export function UpdateAvailableModal({
  result,
  canAutoUpdate,
  autoUpdateState,
  autoUpdateError,
  shutdownInSeconds,
  onDismiss,
  onUpdateNow,
  onAutoUpdate,
}: UpdateAvailableModalProps) {
  const messages = useMessages().updatePrompt;
  const trimmedNotes = clampNotes(result.release_notes_markdown);
  const publishedAt = formatPublishedAt(result.published_at);
  const inFlight =
    autoUpdateState === "preparing" || autoUpdateState === "ready";
  useEscapeKey(onDismiss, !inFlight);

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
          {autoUpdateState === "preparing" ? (
            <p className={styles.autoStatus}>{messages.autoPreparing}</p>
          ) : null}
          {autoUpdateState === "ready" ? (
            <p className={styles.autoStatusReady}>
              {messages.autoReadyPrefix}
              <strong>{shutdownInSeconds ?? 0}</strong>
              {messages.autoReadySuffix}
            </p>
          ) : null}
          {autoUpdateState === "error" && autoUpdateError ? (
            <p className={styles.autoStatusError}>
              {messages.autoFailed}: {autoUpdateError}
            </p>
          ) : null}
        </div>
        <div className={styles.footer}>
          <Pill variant="ghost" onClick={onDismiss} disabled={inFlight}>
            {messages.laterAction}
          </Pill>
          {canAutoUpdate && autoUpdateState !== "error" ? (
            <Pill onClick={onAutoUpdate} disabled={inFlight}>
              {autoUpdateState === "preparing"
                ? messages.autoPreparingAction
                : autoUpdateState === "ready"
                  ? messages.autoReadyAction
                  : messages.autoUpdateAction}
            </Pill>
          ) : (
            <Pill onClick={onUpdateNow}>{messages.updateAction}</Pill>
          )}
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
