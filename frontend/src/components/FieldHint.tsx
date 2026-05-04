import { useId, useState } from "react";
import { useMessages } from "@/locales";
import type { ProviderTemplateFieldHint } from "@/bridge";
import styles from "./FieldHint.module.css";

interface FieldHintProps {
  mode: "provider" | "custom";
  providerName?: string;
  hint: ProviderTemplateFieldHint;
}

export function FieldHint({ mode, providerName, hint }: FieldHintProps) {
  const messages = useMessages();
  const fieldHint = messages.fieldHint;
  const modelHints = messages.modelHints;
  const [open, setOpen] = useState(false);
  const popoverId = useId();

  // The description_key is dot-separated (e.g. "modelHints.timeout").
  // Resolve it through the existing locales structure; fall back to
  // a literal key when missing so locale gaps surface visibly.
  const description = resolveModelHint(modelHints, hint.description_key);
  const showRecommendation =
    mode === "provider" && hint.recommended_value.length > 0;
  const showSource = mode === "provider" && hint.source_url !== null;

  return (
    <span className={styles.wrap}>
      <button
        type="button"
        className={styles.trigger}
        aria-label={fieldHint.toggleLabel}
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={() => setOpen(!open)}
        onBlur={() => {
          // Close after a tick so click-on-link inside the popover
          // still navigates.
          setTimeout(() => setOpen(false), 120);
        }}
      >
        ?
      </button>
      {open ? (
        <div
          id={popoverId}
          className={styles.popover}
          role="tooltip"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <p className={styles.description}>{description}</p>
          {showRecommendation ? (
            <p className={styles.recommendation}>
              <span className={styles.recommendationLabel}>
                {fieldHint.recommendedFor.replace(
                  "{provider}",
                  providerName ?? fieldHint.fallbackProvider,
                )}
              </span>
              <code className={styles.recommendationValue}>
                {hint.recommended_value}
              </code>
            </p>
          ) : null}
          {showSource && hint.source_url ? (
            <a
              className={styles.sourceLink}
              href={hint.source_url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {fieldHint.source} →
            </a>
          ) : null}
        </div>
      ) : null}
    </span>
  );
}

function resolveModelHint(
  modelHints: Record<string, string>,
  key: string,
): string {
  const segments = key.split(".");
  if (segments.length === 2 && segments[0] === "modelHints") {
    return modelHints[segments[1]] ?? key;
  }
  return key;
}
