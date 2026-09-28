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
