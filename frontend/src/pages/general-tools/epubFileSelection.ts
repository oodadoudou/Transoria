export function selectFiles(visible: string[], selected: string[], anchor: string, path: string, toggle: boolean, range: boolean): string[] {
  if (!visible.includes(path)) return selected;
  if (range && visible.includes(anchor)) {
    const start = visible.indexOf(anchor), end = visible.indexOf(path);
    const paths = visible.slice(Math.min(start, end), Math.max(start, end) + 1);
    return toggle ? [...new Set([...selected, ...paths])] : paths;
  }
  return toggle ? selected.includes(path) ? selected.filter((item) => item !== path) : [...selected, path] : [path];
}

export function numberedResourceNames(paths: string[], prefix: string, start: number): Record<string, string> {
  return Object.fromEntries(paths.map((path, index) => {
    const slash = path.lastIndexOf("/"), dot = path.lastIndexOf(".");
    const extension = dot > slash ? path.slice(dot) : "";
    return [path, `${path.slice(0, slash + 1)}${prefix}${start + index}${extension}`];
  }));
}
