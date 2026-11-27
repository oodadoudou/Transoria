import { useState } from "react";
import { ModalOverlay } from "@/components/ModalOverlay";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useMessages } from "@/locales";
import { Button } from "./Button";
import { TextField } from "./TextField";
import styles from "./CompletionWithFailuresDialog.module.css";

export interface GlossaryTermDraft {
  src: string;
  dst: string;
  info: string;
}

interface AddGlossaryTermModalProps {
  initial: GlossaryTermDraft;
  busy: boolean;
  onSave: (term: GlossaryTermDraft) => void;
  onCancel: () => void;
}

export function AddGlossaryTermModal({ initial, busy, onSave, onCancel }: AddGlossaryTermModalProps) {
  const messages = useMessages();
  const labels = messages.translation.glossaryPage.editor;
  const m = messages.translation.proofreadingPage;
  const [term, setTerm] = useState<GlossaryTermDraft>(initial);
  useEscapeKey(onCancel);
  const canSave = term.src.trim().length > 0 && term.dst.trim().length > 0 && !busy;

  return (
    <ModalOverlay
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-glossary-term-title"
      onClick={onCancel}
    >
      <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
        <h2 className={styles.title} id="add-glossary-term-title">
          {m.addGlossaryTerm}
        </h2>
        <TextField stacked label={labels.source} value={term.src} onChange={(src) => setTerm((t) => ({ ...t, src }))} />
        <TextField stacked label={labels.translation} value={term.dst} onChange={(dst) => setTerm((t) => ({ ...t, dst }))} />
        <TextField stacked label={labels.description} value={term.info} onChange={(info) => setTerm((t) => ({ ...t, info }))} />
        <div className={styles.footer}>
          <Button variant="ghost" onClick={onCancel}>
            {m.addGlossaryTermCancel}
          </Button>
          <Button disabled={!canSave} onClick={() => onSave({ src: term.src.trim(), dst: term.dst.trim(), info: term.info.trim() })}>
            {m.addGlossaryTermSave}
          </Button>
        </div>
      </div>
    </ModalOverlay>
  );
}
