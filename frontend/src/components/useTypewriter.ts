import { useEffect, useState } from "react";

interface TypewriterOptions {
  /** How many full type→hold→erase cycles before settling on the
   * complete word. ``loops = 1`` means: type once, hold, erase, type
   * once more (the final settle render). Default 1 — restrained. */
  loops?: number;
  /** Per-character append delay during the typing phase. */
  typeMs?: number;
  /** Per-character pop delay during the erasing phase. */
  eraseMs?: number;
  /** Hold duration when the full word is on screen. */
  holdFullMs?: number;
  /** Hold duration when the word has been fully erased. */
  holdEmptyMs?: number;
}

interface TypewriterState {
  /** The current substring to render. */
  text: string;
  /** ``true`` once the loop sequence is over and the full word is
   * permanently shown — let callers hide the cursor at that point. */
  done: boolean;
}

/**
 * Tiny cycle-aware typewriter. Designed for the brand wordmark — runs
 * a short sequence on mount then settles, so the user sees the
 * animation once or twice and isn't visually pestered for the rest of
 * the session.
 *
 * Honors ``prefers-reduced-motion``: reduced-motion users see the full
 * word immediately with ``done=true`` and no cursor.
 */
export function useTypewriter(
  target: string,
  {
    loops = 1,
    typeMs = 150,
    eraseMs = 90,
    holdFullMs = 1500,
    holdEmptyMs = 350,
  }: TypewriterOptions = {},
): TypewriterState {
  const reduced =
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const [state, setState] = useState<TypewriterState>(() =>
    reduced
      ? { text: target, done: true }
      : { text: "", done: false },
  );

  useEffect(() => {
    if (reduced) {
      setState({ text: target, done: true });
      return;
    }

    let cancelled = false;
    let timer: number | undefined;
    let cyclesLeft = Math.max(0, loops);

    const schedule = (delay: number, fn: () => void) => {
      timer = window.setTimeout(() => {
        if (!cancelled) fn();
      }, delay);
    };

    const typeUp = (current: string) => {
      if (current.length >= target.length) {
        // Reached the full word.
        if (cyclesLeft <= 0) {
          setState({ text: target, done: true });
          return;
        }
        schedule(holdFullMs, () => eraseDown(target));
        return;
      }
      const next = target.slice(0, current.length + 1);
      setState({ text: next, done: false });
      schedule(typeMs, () => typeUp(next));
    };

    const eraseDown = (current: string) => {
      if (current.length === 0) {
        cyclesLeft -= 1;
        schedule(holdEmptyMs, () => typeUp(""));
        return;
      }
      const next = current.slice(0, -1);
      setState({ text: next, done: false });
      schedule(eraseMs, () => eraseDown(next));
    };

    // Kick off after a short beat so the page paint finishes first.
    schedule(holdEmptyMs, () => typeUp(""));

    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [target, reduced, loops, typeMs, eraseMs, holdFullMs, holdEmptyMs]);

  return state;
}
