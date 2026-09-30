export type SearchScope = "current" | "text" | "styles" | "all" | "selection";
export type SearchOptions = { query: string; replacement: string; caseSensitive: boolean; regularExpression: boolean; scope: SearchScope };
export type SavedSearch = SearchOptions & { id: string; name: string };

export function parseSavedSearches(value: string): SavedSearch[] {
  try {
    const entries: unknown = JSON.parse(value);
    if (!Array.isArray(entries)) return [];
    const ids = new Set<string>();
    return entries.filter((entry): entry is SavedSearch => {
      if (!entry || typeof entry !== "object") return false;
      const item = entry as Partial<SavedSearch>;
      if (typeof item.id !== "string" || !item.id || ids.has(item.id) || typeof item.name !== "string" || !item.name.trim()
        || typeof item.query !== "string" || !item.query || item.query.length > 2000 || typeof item.replacement !== "string"
        || typeof item.caseSensitive !== "boolean" || typeof item.regularExpression !== "boolean"
        || !["current", "text", "styles", "all", "selection"].includes(item.scope ?? "")) return false;
      ids.add(item.id);
      return true;
    }).slice(0, 100);
  } catch { return []; }
}
