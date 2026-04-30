import { useCallback, useEffect, useRef, useState } from "react";
import { useMessages } from "@/locales";
import {
  BridgeError,
  glossaryBridge,
  translationBridge,
  type ProbeContinuable,
} from "@/bridge";
import {
  useRunSnapshot,
  useRuntimeStore,
  type RunKind,
} from "@/store/useRuntimeStore";
import styles from "./RunControls.module.css";

interface RunControlsProps {
  kind: RunKind;
}

const ICON_PLAY = "▶";
const ICON_STOP = "■";
const ICON_CONTINUE = "↻";

const NEEDS_CONFIRM: ReadonlySet<string> = new Set([
  "running",
  "paused",
  "pausing",
  "stopping",
  "stopped",
]);

const EMPTY_PROBE: ProbeContinuable = {
  continuable: false,
  task_id: null,
  status: null,
  pending: 0,
  failed: 0,
};

export function RunControls({ kind }: RunControlsProps) {
  const messages = useMessages();
  const labels = messages.runControls;
  const snapshot = useRunSnapshot(kind);
  const setLastError = useRuntimeStore((state) => state.setLastError);
  const refreshActiveTask = useRuntimeStore((state) => state.refreshActiveTask);

  const status = snapshot.status;
  const idle = snapshot.isIdle;

  const bridge = kind === "translation" ? translationBridge : glossaryBridge;

  const [probe, setProbe] = useState<ProbeContinuable>(EMPTY_PROBE);
  const [confirmOpen, setConfirmOpen] = useState(false);
  // Triple-click escape hatch: while a task is active the start button
  // looks inactive but stays clickable so a misconfigured run can be
  // force-restarted without first hitting Stop. Counter resets after 5s
  // of inactivity, or when status leaves the active set.
  const [restartClicks, setRestartClicks] = useState(0);
  const restartResetTimer = useRef<number | null>(null);

  // Refresh `probe_continuable` on mount and whenever the live status
  // crosses a boundary that could change continuability (RUNNING ↔
  // PAUSED ↔ STOPPED ↔ COMPLETED). Cheap: one bridge call per
  // transition.
  useEffect(() => {
    let cancelled = false;
    void bridge
      .probeContinuable()
      .then((next) => {
        if (!cancelled) setProbe(next);
      })
      .catch((error) => {
        if (cancelled) return;
        if (BridgeError.isBridgeError(error)) {
          // Probe failures don't surface to the user; the Continue
          // button just stays disabled. Reset to empty so stale data
          // doesn't lie.
          setProbe(EMPTY_PROBE);
        } else {
          throw error;
        }
      });
    return () => {
      cancelled = true;
    };
  }, [bridge, status, idle]);

  const dispatch = useCallback(
    async (action: () => Promise<unknown>): Promise<void> => {
      setLastError(kind, null);
      try {
        await action();
        await refreshActiveTask(kind);
        const next = await bridge.probeContinuable();
        setProbe(next);
      } catch (error) {
        if (BridgeError.isBridgeError(error)) {
          setLastError(kind, error);
          return;
        }
        throw error;
      }
    },
    [bridge, kind, refreshActiveTask, setLastError],
  );

  const performStart = useCallback(
    () => dispatch(() => bridge.startTask(`start-${Date.now().toString(36)}`)),
    [bridge, dispatch],
  );

  const clearRestartTimer = useCallback(() => {
    if (restartResetTimer.current !== null) {
      window.clearTimeout(restartResetTimer.current);
      restartResetTimer.current = null;
    }
  }, []);

  const handleStartClick = useCallback(() => {
    // While a task is in flight ("running" / "stopping") the click acts
    // as a vote toward force-restart; show the dialog only after 3 hits.
    if (status === "running" || status === "stopping") {
      clearRestartTimer();
      const next = restartClicks + 1;
      if (next >= 3) {
        setRestartClicks(0);
        setConfirmOpen(true);
        return;
      }
      setRestartClicks(next);
      restartResetTimer.current = window.setTimeout(() => {
        setRestartClicks(0);
        restartResetTimer.current = null;
      }, 5000);
      return;
    }
    const hasPriorState = probe.task_id !== null || NEEDS_CONFIRM.has(status);
    if (hasPriorState) {
      setConfirmOpen(true);
      return;
    }
    void performStart();
  }, [clearRestartTimer, probe.task_id, restartClicks, status, performStart]);

  const handleConfirmStart = useCallback(() => {
    setConfirmOpen(false);
    void performStart();
  }, [performStart]);

  // Reset the click counter when the task leaves the active set so the
  // hint disappears as soon as Stop / Continue / a fresh start lands.
  useEffect(() => {
    if (status !== "running" && status !== "stopping") {
      clearRestartTimer();
      setRestartClicks(0);
    }
  }, [clearRestartTimer, status]);

  useEffect(() => clearRestartTimer, [clearRestartTimer]);

  const handleStop = useCallback(() => {
    const taskId = useRuntimeStore.getState()[kind].activeTaskId;
    if (!taskId) return;
    void dispatch(() => bridge.stopTask(taskId));
  }, [bridge, kind, dispatch]);

  const handleContinue = useCallback(() => {
    const taskId = probe.task_id;
    if (!taskId) return;
    void dispatch(() => bridge.continueTask(taskId));
  }, [bridge, dispatch, probe.task_id]);

  // Pause is intentionally fused into Stop — the pause path was racy
  // and crash-prone. Stop is the only way to pause-then-resume now;
  // the user picks up via Continue (cache survives stop).
  const canStop = status === "running";
  const canContinue = probe.continuable && status !== "running";
  // Start looks inactive while a task is in flight, but stays clickable
  // so a triple-click can force-restart (see handleStartClick).
  const startActive = status === "running" || status === "stopping";
  const restartRemaining = startActive ? Math.max(0, 3 - restartClicks) : 0;

  const stopLabel = status === "stopping" ? labels.stopping : labels.stop;
  const startLabel = startActive
    ? status === "running"
      ? labels.running
      : labels.starting
    : labels.start;

  return (
    <>
      <div className={styles.bar} role="group" aria-label="task controls">
        <Button
          kind="primary"
          icon={ICON_PLAY}
          label={startLabel}
          disabled={false}
          inactive={startActive}
          onClick={handleStartClick}
        />
        <Button
          kind="ghost"
          icon={ICON_CONTINUE}
          label={labels.continue}
          disabled={!canContinue}
          onClick={handleContinue}
        />
        <Button
          kind="warn"
          icon={ICON_STOP}
          label={stopLabel}
          disabled={!canStop}
          onClick={handleStop}
        />
      </div>
      {restartClicks > 0 && restartRemaining > 0 ? (
        <p className={styles.hint} role="status" aria-live="polite">
          {labels.restartHint.replace("{count}", String(restartRemaining))}
        </p>
      ) : null}
      {confirmOpen ? (
        <ConfirmStartDialog
          title={labels.confirmStartTitle}
          body={labels.confirmStartBody}
          confirm={labels.confirmStartConfirm}
          cancel={labels.confirmStartCancel}
          onConfirm={handleConfirmStart}
          onCancel={() => setConfirmOpen(false)}
        />
      ) : null}
    </>
  );
}

interface ButtonProps {
  kind: "primary" | "ghost" | "warn";
  icon: string;
  label: string;
  disabled: boolean;
  inactive?: boolean;
  onClick: () => void;
}

function Button({
  kind,
  icon,
  label,
  disabled,
  inactive,
  onClick,
}: ButtonProps) {
  const className = [
    styles.button,
    styles[kind],
    inactive ? styles.inactive : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <button
      type="button"
      className={className}
      disabled={disabled}
      onClick={onClick}
    >
      <span className={styles.icon} aria-hidden>
        {icon}
      </span>
      <span className={styles.label}>{label}</span>
    </button>
  );
}

interface ConfirmStartDialogProps {
  title: string;
  body: string;
  confirm: string;
  cancel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

function ConfirmStartDialog({
  title,
  body,
  confirm,
  cancel,
  onConfirm,
  onCancel,
}: ConfirmStartDialogProps) {
  return (
    <div
      className={styles.dialogOverlay}
      role="dialog"
      aria-modal="true"
      aria-labelledby="run-controls-confirm-title"
      onClick={onCancel}
    >
      <div className={styles.dialog} onClick={(e) => e.stopPropagation()}>
        <h2 id="run-controls-confirm-title" className={styles.dialogTitle}>
          {title}
        </h2>
        <p className={styles.dialogBody}>{body}</p>
        <div className={styles.dialogActions}>
          <button
            type="button"
            className={`${styles.button} ${styles.ghost}`}
            onClick={onCancel}
          >
            <span className={styles.label}>{cancel}</span>
          </button>
          <button
            type="button"
            className={`${styles.button} ${styles.warn}`}
            onClick={onConfirm}
          >
            <span className={styles.label}>{confirm}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
