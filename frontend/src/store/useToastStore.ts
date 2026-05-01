import { create } from "zustand";

export type ToastVariant = "success" | "warning" | "error";

export interface Toast {
  id: string;
  variant: ToastVariant;
  /** Short headline shown in bold; usually a fixed locale string. */
  title: string;
  /** Optional detail line — e.g. saved field name, error message,
   * rejected-fields summary. */
  detail?: string;
  /** Auto-dismiss in ms. Set to 0 to keep the toast until the user
   * clicks the close button. Default 3000. */
  durationMs: number;
  /** Wall-clock ts when the toast was pushed; used for debouncing
   * identical toasts and ordering visible toasts oldest-on-top. */
  createdAt: number;
}

interface ToastStore {
  toasts: Toast[];
  push: (input: {
    variant: ToastVariant;
    title: string;
    detail?: string;
    durationMs?: number;
  }) => string;
  dismiss: (id: string) => void;
  clear: () => void;
}

const MAX_VISIBLE = 4;
const DEFAULT_DURATION_MS = 3000;

let counter = 0;
const nextId = (): string => {
  counter += 1;
  return `t-${Date.now().toString(36)}-${counter}`;
};

export const useToastStore = create<ToastStore>((set, get) => ({
  toasts: [],
  push: ({ variant, title, detail, durationMs }) => {
    const id = nextId();
    const toast: Toast = {
      id,
      variant,
      title,
      detail,
      durationMs: durationMs ?? DEFAULT_DURATION_MS,
      createdAt: Date.now(),
    };
    set((state) => {
      const next = [...state.toasts, toast];
      // Drop the oldest when we exceed the visible cap; toasts piling
      // up indefinitely is worse than losing one for a panic-saving user.
      if (next.length > MAX_VISIBLE) {
        next.splice(0, next.length - MAX_VISIBLE);
      }
      return { toasts: next };
    });
    if (toast.durationMs > 0) {
      window.setTimeout(() => get().dismiss(id), toast.durationMs);
    }
    return id;
  },
  dismiss: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
  clear: () => set({ toasts: [] }),
}));
