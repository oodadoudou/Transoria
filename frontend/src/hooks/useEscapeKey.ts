import { useEffect, useRef } from "react";

type EscapeKeyEntry = {
  id: symbol;
  handle: () => void;
};

const escapeKeyStack: EscapeKeyEntry[] = [];
let listening = false;

function handleDocumentKeyDown(event: KeyboardEvent): void {
  if (event.key !== "Escape" || event.defaultPrevented) return;
  const entry = escapeKeyStack[escapeKeyStack.length - 1];
  if (!entry) return;
  event.preventDefault();
  event.stopPropagation();
  entry.handle();
}

export function useEscapeKey(onEscape: () => void, enabled = true): void {
  const handlerRef = useRef(onEscape);

  useEffect(() => {
    handlerRef.current = onEscape;
  }, [onEscape]);

  useEffect(() => {
    if (!enabled) return undefined;

    const entry: EscapeKeyEntry = {
      id: Symbol("escape-key"),
      handle: () => handlerRef.current(),
    };
    escapeKeyStack.push(entry);

    if (!listening) {
      document.addEventListener("keydown", handleDocumentKeyDown, true);
      listening = true;
    }

    return () => {
      const index = escapeKeyStack.findIndex((item) => item.id === entry.id);
      if (index >= 0) escapeKeyStack.splice(index, 1);
      if (escapeKeyStack.length === 0 && listening) {
        document.removeEventListener("keydown", handleDocumentKeyDown, true);
        listening = false;
      }
    };
  }, [enabled]);
}
