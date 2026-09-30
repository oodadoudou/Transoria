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

export function nextMatchIndex(matches: Array<{ path: string; start: number }>, paths: string[], path: string, position: number): number {
  const order = new Map(paths.map((item, index) => [item, index]));
  const current = order.get(path);
  if (current === undefined) return 0;
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

export function reorderedSpine(spine: string[], path: string, target: string, after: boolean): string[] {
  if (path === target || !spine.includes(path) || !spine.includes(target)) return spine;
  const order = spine.filter((item) => item !== path);
  order.splice(order.indexOf(target) + Number(after), 0, path);
  return order.every((item, index) => item === spine[index]) ? spine : order;
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
