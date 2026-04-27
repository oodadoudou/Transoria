import { create } from "zustand";
import type { Messages } from "./types";
import { en } from "./en";
import { zh } from "./zh";

export type Locale = "en" | "zh";

const catalogues: Record<Locale, Messages> = { en, zh };

interface I18nState {
  locale: Locale;
  messages: Messages;
  setLocale: (locale: Locale) => void;
}

export const useI18n = create<I18nState>((set) => ({
  locale: "zh",
  messages: zh,
  setLocale: (locale) => set({ locale, messages: catalogues[locale] }),
}));

/** Substitute `{name}` placeholders in a template string. */
export function format(
  template: string,
  vars: Record<string, string | number>,
): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => {
    const value = vars[key];
    return value === undefined ? `{${key}}` : String(value);
  });
}

/** Convenience hook returning just the catalogue. */
export function useMessages(): Messages {
  return useI18n((state) => state.messages);
}
