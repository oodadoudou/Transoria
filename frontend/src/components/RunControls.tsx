import { useCallback, useEffect, useRef, useState } from "react";
import { useMessages } from "@/locales";
import {
  BridgeError,
  glossaryBridge,
  glossaryReviewBridge,
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

type ControlAction = "start" | "continue" | "stop";

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

const IN_FLIGHT_STATUSES: ReadonlySet<string> = new Set([
  "pending",
  "running",
  "pausing",
  "stopping",
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

  const bridge =
    kind === "translation"
      ? translationBridge
      : kind === "glossary"
        ? glossaryBridge
        : glossaryReviewBridge;

  const [probe, setProbe] = useState<ProbeContinuable>(EMPTY_PROBE);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingAction, setPendingAction] = useState<ControlAction | null>(null);
  const controlPendingRef = useRef(false);
  const isInFlight =
    pendingAction !== null || (!idle && IN_FLIGHT_STATUSES.has(status));
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
    async (
      action: () => Promise<unknown>,
      actionKind: ControlAction,
    ): Promise<void> => {
      if (controlPendingRef.current) return;
      controlPendingRef.current = true;
      setPendingAction(actionKind);
      setLastError(kind, null);
      try {
        await action();
        await refreshActiveTask(kind);
        const next = await bridge.probeContinuable();
        setProbe(next);
      } catch (error) {
        if (BridgeError.isBridgeError(error)) {
          setLastError(kind, error);
          if (actionKind === "continue") {
            try {
              const next = await bridge.probeContinuable();
              setProbe(next);
            } catch {
              setProbe(EMPTY_PROBE);
            }
          }
          return;
        }
        throw error;
      } finally {
        controlPendingRef.current = false;
        setPendingAction(null);
      }
    },
    [bridge, kind, refreshActiveTask, setLastError],
  );

  const performStart = useCallback(
    () =>
      dispatch(
        () => bridge.startTask(`start-${Date.now().toString(36)}`),
        "start",
      ),
    [bridge, dispatch],
  );

  const clearRestartTimer = useCallback(() => {
    if (restartResetTimer.current !== null) {
      window.clearTimeout(restartResetTimer.current);
      restartResetTimer.current = null;
    }
  }, []);

  const handleStartClick = useCallback(() => {
    if (controlPendingRef.current) return;
    // While a task is in flight, the click acts
    // as a vote toward force-restart; show the dialog only after 3 hits.
    if (isInFlight) {
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
  }, [
    clearRestartTimer,
    isInFlight,
    performStart,
    probe.task_id,
    restartClicks,
    status,
  ]);

  const handleConfirmStart = useCallback(() => {
    setConfirmOpen(false);
    void performStart();
  }, [performStart]);

  // Reset the click counter when the task leaves the active set so the
  // hint disappears as soon as Stop / Continue / a fresh start lands.
  useEffect(() => {
    if (!isInFlight) {
      clearRestartTimer();
      setRestartClicks(0);
    }
  }, [clearRestartTimer, isInFlight]);

  useEffect(() => clearRestartTimer, [clearRestartTimer]);

  const handleStop = useCallback(async () => {
    const taskId = useRuntimeStore.getState()[kind].activeTaskId;
    if (!taskId) return;
    if (controlPendingRef.current) return;
    controlPendingRef.current = true;
    setPendingAction("stop");
    setLastError(kind, null);
    try {
      await bridge.stopTask(taskId);
      // Stop is asynchronous on the backend — the executor finishes
      // cancelling in-flight LLM calls and only then transitions to
      // STOPPED. Without this loop the next probe fires while status
      // is still STOPPING, ``probe.continuable`` returns false, and
      // Continue stays disabled until the next 2s poll tick.
      const pollSnapshot = useRuntimeStore.getState().pollSnapshot;
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        await pollSnapshot(kind);
        const cur = useRuntimeStore.getState()[kind].snapshot?.header.status;
        if (cur === "stopped" || cur === "completed" || cur === "failed") {
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 400));
      }
      await refreshActiveTask(kind);
      const next = await bridge.probeContinuable();
      setProbe(next);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setLastError(kind, error);
        return;
      }
      throw error;
    } finally {
      controlPendingRef.current = false;
      setPendingAction(null);
    }
  }, [bridge, kind, refreshActiveTask, setLastError]);

  const handleContinue = useCallback(() => {
    if (controlPendingRef.current) return;
    const taskId = probe.task_id;
    if (!taskId) return;
    setProbe((current) =>
      current.task_id === taskId ? { ...current, continuable: false } : current,
    );
    void dispatch(() => bridge.continueTask(taskId), "continue");
  }, [bridge, dispatch, probe.task_id]);

  // Pause is intentionally fused into Stop — the pause path was racy
  // and crash-prone. Stop is the only way to pause-then-resume now;
  // the user picks up via Continue (cache survives stop).
  const canStop = !idle && status === "running";
  const canContinue = probe.continuable && !isInFlight;
  // Start looks inactive while a task is in flight, but stays clickable
  // so a triple-click can force-restart (see handleStartClick).
  const startActive = isInFlight;
  const restartRemaining = startActive ? Math.max(0, 3 - restartClicks) : 0;

  const stopLabel =
    pendingAction === "stop" || status === "stopping" || status === "pausing"
      ? labels.stopping
      : labels.stop;
  const startLabel = startActive
    ? pendingAction === "continue" || status === "running"
      ? labels.running
      : labels.starting
    : labels.start;

  return (
    <>
      <div className={styles.bar} role="group" aria-label={labels.taskControls}>
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
      {canContinue ? (
        <p className={styles.hint} role="status">
          {labels.continueHint
            .replace("{failed}", String(probe.failed))
            .replace("{pending}", String(probe.pending))}
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
