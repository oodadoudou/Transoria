import { useMessages } from '@/locales';
import {
  BridgeError,
  glossaryBridge,
  translationBridge,
} from '@/bridge';
import {
  useRunSnapshot,
  useRuntimeStore,
  type RunKind,
} from '@/store/useRuntimeStore';
import styles from './RunControls.module.css';

interface RunControlsProps {
  kind: RunKind;
}

const ICON_PLAY = '▶';
const ICON_PAUSE = '❚❚';
const ICON_STOP = '■';
const ICON_RESUME = '↻';

export function RunControls({ kind }: RunControlsProps) {
  const messages = useMessages();
  const labels = messages.runControls;
  const snapshot = useRunSnapshot(kind);
  const setLastError = useRuntimeStore((state) => state.setLastError);
  const refreshActiveTask = useRuntimeStore((state) => state.refreshActiveTask);

  const status = snapshot.status;
  const idle = snapshot.isIdle;
  const isRunning = status === 'running';
  const isStopping = status === 'stopping';
  const isStopped = status === 'stopped' && !idle;

  const bridge = kind === 'translation' ? translationBridge : glossaryBridge;

  const dispatch = async (
    action: () => Promise<unknown>,
  ): Promise<void> => {
    setLastError(kind, null);
    try {
      await action();
      await refreshActiveTask(kind);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setLastError(kind, error);
        return;
      }
      throw error;
    }
  };

  const handleStart = () =>
    dispatch(() => bridge.startTask(`start-${Date.now().toString(36)}`));

  const handlePause = () => {
    const taskId = useRuntimeStore.getState()[kind].activeTaskId;
    if (!taskId) return;
    return dispatch(() => bridge.pauseTask(taskId));
  };

  const handleStop = () => {
    const taskId = useRuntimeStore.getState()[kind].activeTaskId;
    if (!taskId) return;
    return dispatch(() => bridge.stopTask(taskId));
  };

  const handleResume = () => {
    const taskId = useRuntimeStore.getState()[kind].activeTaskId;
    if (!taskId) return;
    return dispatch(() => bridge.resumeTask(taskId));
  };

  return (
    <div className={styles.bar} role="group" aria-label="task controls">
      <Button
        kind="primary"
        icon={ICON_PLAY}
        label={labels.start}
        disabled={isRunning || isStopping}
        onClick={handleStart}
      />
      <Button
        kind="ghost"
        icon={ICON_PAUSE}
        label={labels.pause}
        disabled={!isRunning}
        onClick={handlePause}
      />
      <Button
        kind="ghost"
        icon={ICON_RESUME}
        label={labels.resume}
        disabled={!isStopped}
        onClick={handleResume}
      />
      <Button
        kind="warn"
        icon={ICON_STOP}
        label={labels.stop}
        disabled={!isRunning && !isStopping}
        onClick={handleStop}
      />
    </div>
  );
}

interface ButtonProps {
  kind: 'primary' | 'ghost' | 'warn';
  icon: string;
  label: string;
  disabled: boolean;
  onClick: () => void;
}

function Button({ kind, icon, label, disabled, onClick }: ButtonProps) {
  const className = `${styles.button} ${styles[kind]}`.trim();
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
