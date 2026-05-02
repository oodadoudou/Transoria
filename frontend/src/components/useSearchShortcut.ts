import { useEffect } from "react";

/** Global ⌘F / Ctrl+F handler that opens the table search box and
 * suppresses the browser's native find-in-page shortcut. Pages use
 * this on top of their existing ``searchOpen`` toggle so users have a
 * keyboard alternative to the toolbar button.
 *
 * Re-checked on every render via the ``open`` callback so a page's
 * latest setter closure is always active. The listener attaches to
 * ``window`` so it fires regardless of which element has focus,
 * matching the platform expectation. */
export function useSearchShortcut(open: () => void): void {
  useEffect(() => {
    function handler(event: KeyboardEvent): void {
      if (!(event.metaKey || event.ctrlKey)) return;
      if (event.key !== "f" && event.key !== "F") return;
      event.preventDefault();
      open();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open]);
}
