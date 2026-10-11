import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { setTransport } from "../../src/bridge/transport";
import { useI18n } from "../../src/locales";
import { RequestLogPanel } from "../../src/components/RequestLogPanel";
import { QuickSwitchModal } from "../../src/components/QuickSwitchModal";
import { FailedSubtasksModal } from "../../src/components/FailedSubtasksModal";
import { CompletionWithFailuresDialog } from "../../src/components/CompletionWithFailuresDialog";
import { RunControls } from "../../src/components/RunControls";
import { GlossaryExportModal } from "../../src/components/GlossaryExportModal";
import { GlossaryStatsModal } from "../../src/components/GlossaryStatsModal";
import { PromptPresetModal } from "../../src/components/PromptPresetModal";
import { ModelProfileModal } from "../../src/components/ModelProfileModal";
import shell from "../../src/App.module.css";
import "../../src/theme/tokens.css";
import "../../src/theme/global.css";

const options = new URLSearchParams(location.search);
const kind = options.get("kind") ?? "translation";
const dialog = options.get("dialog") ?? "request";
useI18n.getState().setLocale(options.get("locale") === "en" ? "en" : "zh");
document.documentElement.dataset.theme = options.get("theme") ?? "dark";

const events = Array.from({ length: 220 }, (_, index) => ({
  request_id: `request-${index}`, timestamp: "2026-01-01T12:00:00Z",
  subtask_id: `chunk-${index}`, subtask_attempt: 1, label: `Synthetic request ${index}`,
  status: "completed", model_id: "synthetic-model-with-a-long-identifier",
  provider_format: "openai", duration_seconds: 20, input_tokens: 5000,
  output_tokens: 1200, response_chars: 1200,
  response_text: `RESPONSE ${index}\n` + "Synthetic response text. ".repeat(200),
}));

setTransport({
  isConnected: () => true,
  async call(method, payload) {
    if (method.endsWith("read_request_events")) {
      const { offset = 0, status = "all" } = payload as { offset?: number; status?: string };
      const matching = status === "all" || status === "completed" ? events : [];
      return { events: matching.slice(offset, offset + 200), total: matching.length, truncated: false } as never;
    }
    if (method.endsWith("probe_continuable")) {
      return { continuable: true, task_id: "synthetic", status: "stopped", pending: 1, failed: 0 } as never;
    }
    if (method === "model_templates.list") return { templates: [] } as never;
    throw new Error(`Unexpected smoke-test bridge call: ${method}`);
  },
});

function Fixture() {
  const [open, setOpen] = useState(true);
  useEffect(() => {
    if (dialog === "request") document.querySelector<HTMLButtonElement>('[role="switch"]')?.click();
    if (dialog === "restart") {
      const timer = setTimeout(() => document.querySelector<HTMLButtonElement>('[role="group"] > div > button')?.click(), 200);
      return () => clearTimeout(timer);
    }
  }, []);
  return <div className={shell.shell}><main className={`${shell.app} ${shell.withInspector}`}>
    <header style={{ gridArea: "topbar" }}>Native modal regression fixture</header>
    <aside style={{ gridArea: "rail" }}>Sidebar</aside>
    <section className={shell.main} style={options.has("paintLayer") ? { transform: "translateZ(0)" } : undefined}>
      <div style={{ height: 900, flexShrink: 0 }}>Scrollable content</div>
      <RunControls kind={kind as "translation" | "glossary" | "glossary_review"}>
        <RequestLogPanel kind={kind as "translation" | "glossary" | "glossary_review"} taskId="synthetic" taskStatus="completed" launcherVariant="bare" />
        {open && dialog === "switch" ? <QuickSwitchModal title="Quick switch" items={events.map((e) => ({ id: e.request_id, name: e.label }))} activeId="request-0" emptyMessage="Empty" onClose={() => setOpen(false)} onSelect={() => undefined} /> : null}
        {open && dialog === "failures" ? <FailedSubtasksModal failures={events.map((e) => ({ subtask_id: e.subtask_id, source_file: "synthetic.txt", message: e.label, attempts: 1, last_error_code: `synthetic.error-${e.request_id}`, last_error_at: e.timestamp }))} onClose={() => setOpen(false)} /> : null}
        {open && dialog === "completion" ? <CompletionWithFailuresDialog failedCount={200} onAccept={() => setOpen(false)} /> : null}
        {open && dialog === "export" ? <GlossaryExportModal onPick={() => undefined} onClose={() => setOpen(false)} /> : null}
        {open && dialog === "stats" ? <GlossaryStatsModal entries={[]} onClose={() => setOpen(false)} /> : null}
        {open && dialog === "prompt" ? <PromptPresetModal mode="create" kind="translation" onSaved={() => undefined} onCancel={() => setOpen(false)} /> : null}
        {open && dialog === "model" ? <ModelProfileModal mode="create" onSaved={() => undefined} onCancel={() => setOpen(false)} /> : null}
      </RunControls>
    </section><footer style={{ gridArea: "status" }}>Status bar</footer>
  </main></div>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
