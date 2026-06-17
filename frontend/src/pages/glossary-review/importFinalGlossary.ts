import {
  glossaryBridge,
  importedGlossaryToPersisted,
  type PersistedGlossaryEntry,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useTaskStore, type GlossaryEntry } from "@/store/useTaskStore";
import { appendUniqueRows, tableRowKey, uniqueRows } from "@/utils/tableDedupe";

interface ImportFinalGlossaryLabels {
  empty: string;
}

export type ImportFinalGlossaryMode = "replace" | "append";

export type ImportFinalGlossaryResult =
  | { status: "imported"; count: number }
  | { status: "needs_decision"; existingCount: number };

function persistedToEntry(
  entry: PersistedGlossaryEntry,
  id: string,
): GlossaryEntry {
  return {
    id,
    source: entry.src,
    translation: entry.dst,
    description: entry.info,
    regex: entry.regex,
    caseSensitive: entry.case_sensitive,
    enabled: entry.enabled,
    frequency: entry.frequency ?? 0,
  };
}

function persistedGlossaryKey(entry: PersistedGlossaryEntry): string {
  return tableRowKey([
    entry.src,
    entry.dst,
    entry.info,
    entry.regex,
    entry.case_sensitive,
    entry.enabled,
    entry.frequency ?? 0,
  ]);
}

export async function importFinalGlossaryToTranslation(
  outputPath: string,
  labels: ImportFinalGlossaryLabels,
  mode?: ImportFinalGlossaryMode,
): Promise<ImportFinalGlossaryResult> {
  const imported = await glossaryBridge.importRules(outputPath);
  const incomingPersisted = importedGlossaryToPersisted(imported.entries);
  if (incomingPersisted.length === 0) {
    throw new Error(labels.empty);
  }

  const settings = useSettingsStore.getState();
  await settings.hydrate();
  const currentSettings = useSettingsStore.getState();
  const translationSettings = currentSettings.translation.draft;
  if (!translationSettings) {
    throw new Error("Translation settings are not loaded.");
  }
  const existingPersisted = translationSettings.translation_glossary;
  if (existingPersisted.length > 0 && !mode) {
    return {
      status: "needs_decision",
      existingCount: existingPersisted.length,
    };
  }
  const shouldReplace = existingPersisted.length === 0 || mode === "replace";
  const incomingUnique = uniqueRows(incomingPersisted, persistedGlossaryKey);
  const merge = shouldReplace
    ? { rows: incomingUnique, added: incomingUnique }
    : appendUniqueRows(
        existingPersisted,
        incomingPersisted,
        persistedGlossaryKey,
      );
  const nextPersisted = merge.rows;

  const stamp = Date.now().toString(36);
  const nextEntries = nextPersisted.map((entry, idx) =>
    persistedToEntry(entry, `g-review-${stamp}-${idx}`),
  );

  useTaskStore.getState().importTranslationGlossaryEntries(nextEntries);
  useTaskStore.getState().setTranslationGlossaryEnabled(true);
  currentSettings.updateField(
    "translation",
    "translation_glossary",
    nextPersisted,
  );
  await currentSettings.saveNow("translation");
  return { status: "imported", count: merge.added.length };
}
