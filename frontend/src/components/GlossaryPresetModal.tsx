import { useEffect, useState } from "react";
import { useMessages } from "@/locales";
import { glossaryBridge, BridgeError } from "@/bridge";
import { Pill } from "./Pill";
import styles from "./QuickSwitchModal.module.css";

interface PresetSummary {
  id: string;
  name: string;
  entry_count: number;
  entries: Array<{
    src: string;
    dst: string;
    info: string;
    regex: boolean;
    case_sensitive: boolean;
    enabled: boolean;
  }>;
}

interface GlossaryPresetModalProps {
  onPick: (entries: PresetSummary["entries"]) => void;
  onClose: () => void;
}

export function GlossaryPresetModal({
  onPick,
  onClose,
}: GlossaryPresetModalProps) {
  const messages = useMessages();
  const labels = messages.translation.glossaryPage.presets;
  const [presets, setPresets] = useState<PresetSummary[] | null>(null);
  const [directory, setDirectory] = useState<string>("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void glossaryBridge
      .listPresets()
      .then((r) => {
        if (cancelled) return;
        setPresets(r.presets);
        setDirectory(r.directory);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(
          BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err),
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
          {error ? (
            <div
              className={styles.empty}
              style={{ color: "#b04038", fontStyle: "normal" }}
            >
              {error}
            </div>
          ) : presets === null ? (
            <div className={styles.empty}>…</div>
          ) : presets.length === 0 ? (
            <div className={styles.empty}>
              {labels.empty}
              {directory ? (
                <div style={{ marginTop: 8, fontSize: 11, fontFamily: "var(--font-mono)" }}>
                  {labels.directoryHint}: <code>{directory}</code>
                </div>
              ) : null}
            </div>
          ) : (
            <div className={styles.list}>
              {presets.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  className={styles.row}
                  onClick={() => {
                    onPick(preset.entries);
                    onClose();
                  }}
                >
                  <span className={styles.rowText}>
                    <span className={styles.rowName}>{preset.name}</span>
                    <span className={styles.rowMeta}>
                      {preset.entry_count} entries
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        {directory && presets && presets.length > 0 ? (
          <div className={styles.footer}>
            <Pill variant="ghost" onClick={onClose}>
              {labels.close}
            </Pill>
          </div>
        ) : null}
      </div>
    </div>
  );
}
