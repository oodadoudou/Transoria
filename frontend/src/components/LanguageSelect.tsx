import { useMessages } from "@/locales";
import type { Language } from "@/bridge";
import styles from "./LanguageSelect.module.css";

const LANGUAGE_IDS: Language[] = [
  "kr",
  "zh",
  "zh-Hant",
  "en",
  "ja",
  "ru",
  "ar",
  "de",
  "fr",
  "pl",
  "es",
  "it",
  "pt",
  "hu",
  "tr",
  "th",
  "id",
  "vi",
];

interface LanguageSelectProps {
  value: Language;
  onChange: (next: Language) => void;
  ariaLabel: string;
}

export function LanguageSelect({
  value,
  onChange,
  ariaLabel,
}: LanguageSelectProps) {
  const messages = useMessages();
  return (
    <select
      aria-label={ariaLabel}
      className={styles.select}
      value={value}
      onChange={(e) => onChange(e.target.value as Language)}
    >
      {LANGUAGE_IDS.map((id) => (
        <option key={id} value={id}>
          {messages.language.options[id]}
        </option>
      ))}
    </select>
  );
}
