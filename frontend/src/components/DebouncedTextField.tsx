import { useEffect, useRef, useState } from 'react';
import { TextField } from '@/components/TextField';
import type { ComponentProps } from 'react';

type TextFieldProps = ComponentProps<typeof TextField>;

interface DebouncedTextFieldProps
  extends Omit<TextFieldProps, 'onChange' | 'value'> {
  value: string;
  onCommit: (next: string) => void;
  delayMs?: number;
}

/**
 * Wraps `TextField` with a local draft + debounce so per-keystroke updates
 * don't fire upstream callbacks (or bridge calls) on every character.
 *
 * - Local state mirrors the incoming `value` when it changes externally
 *   (e.g. after a refresh from the bridge).
 * - `onCommit` fires after `delayMs` of inactivity, on blur, or on unmount
 *   if there's still pending text.
 */
export function DebouncedTextField({
  value,
  onCommit,
  delayMs = 350,
  ...rest
}: DebouncedTextFieldProps) {
  const [draft, setDraft] = useState(value);
  const timeoutRef = useRef<number | null>(null);
  const pendingRef = useRef<string | null>(null);
  const onCommitRef = useRef(onCommit);

  useEffect(() => {
    onCommitRef.current = onCommit;
  }, [onCommit]);

  useEffect(() => {
    setDraft(value);
    pendingRef.current = null;
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, [value]);

  useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
        if (pendingRef.current !== null) {
          onCommitRef.current(pendingRef.current);
        }
      }
    };
  }, []);

  const handleChange = (next: string) => {
    setDraft(next);
    pendingRef.current = next;
    if (timeoutRef.current !== null) {
      window.clearTimeout(timeoutRef.current);
    }
    timeoutRef.current = window.setTimeout(() => {
      timeoutRef.current = null;
      const pending = pendingRef.current;
      pendingRef.current = null;
      if (pending !== null) {
        onCommitRef.current(pending);
      }
    }, delayMs);
  };

  return <TextField {...rest} value={draft} onChange={handleChange} />;
}
