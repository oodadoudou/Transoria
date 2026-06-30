import { describe, expect, it } from "vitest";

import type { TxtToEpubPreset, TxtToEpubStyle } from "@/bridge";
import { en } from "@/locales/en";
import { zh } from "@/locales/zh";
import {
  displayPreset,
  displayStyle,
  mergeChinesePresets,
} from "./TxtToEpubPage.logic";

const markdownPreset: TxtToEpubPreset = {
  id: "markdown",
  label: "后端 Markdown",
  description: "后端描述",
  rules: [{ level: 1, pattern: "^# .+$" }],
};

describe("TXT to EPUB display helpers", () => {
  it("uses locale text for known TOC presets", () => {
    expect(displayPreset(markdownPreset, en.txtToEpubTool)).toEqual({
      label: "Markdown headings",
      description: "#, ##, ###, #### headings",
    });
    expect(displayPreset(markdownPreset, zh.txtToEpubTool)).toEqual({
      label: "Markdown 标题",
      description: "#、##、###、#### 标题",
    });
  });

  it("falls back to backend preset labels for unknown presets", () => {
    const custom: TxtToEpubPreset = {
      id: "custom_backend",
      label: "Backend custom",
      description: "Backend description",
      rules: [],
    };

    expect(displayPreset(custom, en.txtToEpubTool)).toEqual({
      label: "Backend custom",
      description: "Backend description",
    });
  });

  it("merges Chinese presets without duplicating rules and localizes the merged label", () => {
    const presets: TxtToEpubPreset[] = [
      {
        id: "zh_webnovel",
        label: "网文",
        description: "old",
        rules: [
          { level: 1, pattern: "^第.+章" },
          { level: 2, pattern: "^番外" },
        ],
      },
      {
        id: "zh_published",
        label: "出版",
        description: "old",
        rules: [
          { level: 1, pattern: "^第.+章" },
          { level: 3, pattern: "^外传" },
        ],
      },
      markdownPreset,
    ];

    const merged = mergeChinesePresets(presets, en.txtToEpubTool);

    expect(merged).toHaveLength(2);
    expect(merged[0]).toMatchObject({
      id: "zh_novel",
      label: "Chinese fiction headings",
    });
    expect(merged[0].rules).toEqual([
      { level: 1, pattern: "^第.+章" },
      { level: 2, pattern: "^番外" },
      { level: 3, pattern: "^外传" },
    ]);
  });

  it("uses locale style labels and group labels", () => {
    const style: TxtToEpubStyle = {
      id: "enhanced:soft_structure",
      group: "enhanced",
      groupLabel: "Backend group",
      label: "Backend style",
      description: "Backend description",
      css: "",
      compatibility: "enhanced",
    };

    expect(displayStyle(style, en.txtToEpubTool)).toEqual({
      label: "Soft structure",
      groupLabel: "Enhanced style",
    });
    expect(displayStyle(style, zh.txtToEpubTool)).toEqual({
      label: "浅底结构",
      groupLabel: "增强样式",
    });
  });

  it("falls back to backend style labels for unknown style keys", () => {
    const style: TxtToEpubStyle = {
      id: "basic:new_backend_style",
      group: "basic",
      groupLabel: "Backend group",
      label: "Backend New",
      description: "Backend description",
      css: "",
      compatibility: "broad",
    };

    expect(displayStyle(style, en.txtToEpubTool)).toEqual({
      label: "Backend New",
      groupLabel: "Compatible style",
    });
  });
});
