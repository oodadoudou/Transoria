import { useMessages } from "@/locales";
import { useRunSnapshot, usePollRunSnapshot } from "@/store/useRuntimeStore";
import { Panel } from "@/components/Panel";
import { ProgressRing } from "@/components/ProgressRing";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { FailedSubtaskList } from "@/components/FailedSubtaskList";
import { RunControls } from "@/components/RunControls";
import styles from "./RunPage.module.css";

const NUM = new Intl.NumberFormat("en");

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.glossary;
  const snapshot = useRunSnapshot("glossary");
  usePollRunSnapshot("glossary");

  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const etaSeconds =
    snapshot.progress.eta_seconds > 0 ? snapshot.progress.eta_seconds : null;

  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="glossary" />

      {snapshot.failures.length > 0 ? (
        <Panel label="Failed subtasks">
          <FailedSubtaskList failures={snapshot.failures} />
        </Panel>
      ) : null}

      <Panel label={run.progress}>
        <div className={styles.progressCard}>
          <ProgressRing percent={percent} completed={completed} total={total} />
          <div className={styles.statGrid}>
            <Stat label={run.stats.completed} value={NUM.format(completed)} />
            <Stat label={run.stats.failed} value={NUM.format(failed)} />
            <Stat label={run.stats.remaining} value={NUM.format(remaining)} />
            <Stat
              label={run.stats.elapsed}
              value={snapshot.isIdle ? "—" : formatDuration(0)}
            />
            <Stat
              label={run.stats.eta}
              value={etaSeconds === null ? "—" : formatDuration(etaSeconds)}
            />
            <Stat
              label={run.stats.avgSpeed}
              value={NUM.format(ratePerMinute)}
              delta="/min"
            />
          </div>
        </div>
      </Panel>

      <RunControls kind="glossary" />
    </>
  );
}

interface StatProps {
  label: string;
  value: string;
  delta?: string;
}

function Stat({ label, value, delta }: StatProps) {
  return (
    <div className={styles.stat}>
      <div className={styles.statLabel}>{label}</div>
      <b className="tnum">
        {value}
        {delta ? <span className={styles.delta}>{delta}</span> : null}
      </b>
    </div>
  );
}
