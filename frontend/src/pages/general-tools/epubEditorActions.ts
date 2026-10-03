import type { EditorState } from "@codemirror/state";
import { historyField } from "@codemirror/commands";

export function savedEditorHistory(state: EditorState | undefined, content: string) {
  return state?.doc.toString() === content ? { json: state.toJSON({ history: historyField }), fields: { history: historyField } } : undefined;
}

export function previewDestination(target: string): { path: string; fragment: string } {
  const separator = target.indexOf("#");
  const path = separator < 0 ? target : target.slice(0, separator);
  const fragment = separator < 0 ? "" : target.slice(separator + 1);
  try { return { path, fragment: decodeURIComponent(fragment) }; }
  catch { return { path, fragment }; }
}

export function relativeResourceHref(from: string, target: string): string {
  const source = from.split("/").slice(0, -1);
  const destination = target.split("/");
  while (source.length && destination.length && source[0] === destination[0]) {
    source.shift();
    destination.shift();
  }
  return [...source.map(() => ".."), ...destination].map((part) => encodeURIComponent(part).replace(/'/g, "%27")).join("/");
}

export function xmlAttribute(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

export function nextMatchIndex(matches: Array<{ path: string; start: number }>, paths: string[], path: string, position: number, direction: -1 | 1 = 1): number {
  if (!matches.length) return -1;
  const order = new Map(paths.map((item, index) => [item, index]));
  const current = order.get(path);
  if (current === undefined) return direction === 1 ? 0 : matches.length - 1;
  if (direction === -1) {
    for (let offset = matches.length - 1; offset >= 0; offset -= 1) {
      const match = matches[offset], index = order.get(match.path);
      if (index !== undefined && (index < current || (index === current && match.start < position))) return offset;
    }
    return matches.length - 1;
  }
  const next = matches.findIndex((match) => {
    const index = order.get(match.path);
    return index !== undefined && (index > current || (index === current && match.start >= position));
  });
  return next < 0 ? 0 : next;
}

export function searchScopePaths(scope: string, selected: string, files: Array<{ path: string; editable: boolean; media_type: string }>, spine: string[]): string[] {
  const media = new Map(files.filter((file) => file.editable).map((file) => [file.path, file.media_type]));
  if (scope === "current" || scope === "selection") return media.has(selected) ? [selected] : [];
  const ordered = Array.from(new Set([...spine, ...media.keys()]));
  return ordered.filter((path) => scope === "styles" ? media.get(path) === "text/css" : scope === "text" ? ["application/xhtml+xml", "text/html", "text/plain"].includes(media.get(path) ?? "") : scope === "all" && media.has(path));
}

export function resourceAfterHistory(
  selected: string,
  previousSpine: string[],
  files: Array<{ path: string; editable: boolean }>,
): string {
  const available = new Set(files.filter((file) => file.editable).map((file) => file.path));
  if (available.has(selected)) return selected;
  const index = previousSpine.indexOf(selected);
  if (index >= 0) {
    for (let offset = index - 1; offset >= 0; offset -= 1) {
      if (available.has(previousSpine[offset])) return previousSpine[offset];
    }
  }
  return files.find((file) => file.editable)?.path ?? "";
}

export function resourcePreviewPath(
  path: string,
  files: Array<{ path: string; media_type: string }>,
  spine: string[],
  navPath: string,
  preferred = "",
): string {
  const media = new Map(files.map((file) => [file.path, file.media_type]));
  const previewable = (target: string) => target !== navPath && ["application/xhtml+xml", "text/html", "image/svg+xml"].includes(media.get(target) ?? "");
  if (media.get(path) === "text/css") return previewable(preferred) ? preferred : spine.find(previewable) ?? "";
  return previewable(path) || media.get(path)?.startsWith("image/") ? path : "";
}

export function resourceAfterMutation(
  action: string, selected: string, resource: string, target: string,
  spine: string[], files: Array<{ path: string; editable: boolean }>,
): string {
  if (action === "add" && files.some((file) => file.path === target && file.editable)) return target;
  return resourceAfterHistory(action === "rename" && selected === resource ? target : selected, spine, files);
}
