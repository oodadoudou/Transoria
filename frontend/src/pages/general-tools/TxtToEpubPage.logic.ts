import type {
  TxtToEpubPreset,
  TxtToEpubRule,
  TxtToEpubStyle,
} from "@/bridge";
import type { Messages } from "@/locales/types";

const CHINESE_PRESET_IDS = new Set(["zh_novel", "zh_webnovel", "zh_published", "extra"]);

type TxtToEpubMessages = Messages["txtToEpubTool"];

export function displayPreset(
  preset: TxtToEpubPreset,
  text: TxtToEpubMessages,
): { label: string; description: string } {
  const localized = lookupRecord(text.tocPresetText, preset.id);
  return localized ?? { label: preset.label, description: preset.description };
}

export function mergeChinesePresets(
  presets: TxtToEpubPreset[],
  text: TxtToEpubMessages,
): TxtToEpubPreset[] {
  const chinesePresets = presets.filter((preset) => CHINESE_PRESET_IDS.has(preset.id));
  if (chinesePresets.length === 0) return presets;
  if (chinesePresets.length === 1 && chinesePresets[0].id === "zh_novel") {
    const localized = text.tocPresetText.zh_novel;
    return presets.map((preset) =>
      preset.id === "zh_novel"
        ? { ...preset, label: localized.label, description: localized.description }
        : preset,
    );
  }

  const seenRules = new Set<string>();
  const mergedRules: TxtToEpubRule[] = [];
  for (const preset of chinesePresets) {
    for (const rule of preset.rules) {
      const key = `${rule.level}\n${rule.pattern}`;
      if (seenRules.has(key)) continue;
      seenRules.add(key);
      mergedRules.push(rule);
    }
  }

  const merged: TxtToEpubPreset = {
    id: "zh_novel",
    label: text.tocPresetText.zh_novel.label,
    description: text.tocPresetText.zh_novel.description,
    rules: mergedRules,
  };
  const next: TxtToEpubPreset[] = [];
  let inserted = false;
  for (const preset of presets) {
    if (CHINESE_PRESET_IDS.has(preset.id)) {
      if (!inserted) {
        next.push(merged);
        inserted = true;
      }
      continue;
    }
    next.push(preset);
  }
  return next;
}

export function displayStyle(
  style: TxtToEpubStyle,
  text: TxtToEpubMessages,
): { label: string; groupLabel: string } {
  const [, key = style.id] = style.id.split(":");
  return {
    label: text.styleLabels[key] ?? style.label,
    groupLabel: style.id.startsWith("enhanced:")
      ? text.styleGroups.enhanced
      : text.styleGroups.compatible,
  };
}

function lookupRecord<T>(record: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(record, key) ? record[key] : undefined;
}
