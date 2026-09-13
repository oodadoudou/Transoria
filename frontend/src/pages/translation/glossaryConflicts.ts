import type { GlossaryEntry } from "@/store/useTaskStore";

export type GlossaryConflictKind =
  | "duplicateSource"
  | "overlap"
  | "invalidRegex";

export interface GlossaryConflictSummary {
  byEntryId: Map<string, GlossaryConflictKind[]>;
  conflicts: Array<{ kind: GlossaryConflictKind; entryIds: string[] }>;
  firstEntryId: string | null;
}

export function buildGlossaryConflictSummary(
  entries: GlossaryEntry[],
): GlossaryConflictSummary {
  const byEntryId = new Map<string, Set<GlossaryConflictKind>>();
  const conflicts: Array<{ kind: GlossaryConflictKind; entryIds: string[] }> =
    [];
  const active = entries.filter(
    (entry) =>
      entry.enabled &&
      entry.source.trim().length > 0 &&
      entry.translation.trim().length > 0,
  );
  const compiledRegexes = new WeakMap<GlossaryEntry, RegExp | null>();
  const regexFor = (entry: GlossaryEntry): RegExp | null => {
    if (compiledRegexes.has(entry)) {
      return compiledRegexes.get(entry) ?? null;
    }
    const compiled = compileRegex(entry);
    compiledRegexes.set(entry, compiled);
    return compiled;
  };

  const addConflict = (
    kind: GlossaryConflictKind,
    entryIds: string[],
  ): void => {
    const uniqueIds = Array.from(new Set(entryIds));
    if (uniqueIds.length === 0) return;
    conflicts.push({ kind, entryIds: uniqueIds });
    uniqueIds.forEach((id) => {
      const kinds = byEntryId.get(id) ?? new Set<GlossaryConflictKind>();
      kinds.add(kind);
      byEntryId.set(id, kinds);
    });
  };

  active.forEach((entry) => {
    if (!entry.regex) return;
    if (!regexFor(entry)) addConflict("invalidRegex", [entry.id]);
  });

  const bySource = new Map<string, GlossaryEntry[]>();
  active.forEach((entry) => {
    const source = entry.source.trim();
    const bucket = bySource.get(source) ?? [];
    bucket.push(entry);
    bySource.set(source, bucket);
  });
  bySource.forEach((bucket) => {
    const translations = new Set(
      bucket.map((entry) => entry.translation.trim()).filter(Boolean),
    );
    if (translations.size > 1) {
      addConflict(
        "duplicateSource",
        bucket.map((entry) => entry.id),
      );
    }
  });

  const regexIndices: number[] = [];
  const plainIndicesByFoldedSource = new Map<string, number[]>();
  active.forEach((entry, index) => {
    if (entry.regex) {
      regexIndices.push(index);
      return;
    }
    const key = entry.source.trim().toLowerCase();
    const bucket = plainIndicesByFoldedSource.get(key) ?? [];
    bucket.push(index);
    plainIndicesByFoldedSource.set(key, bucket);
  });

  const inspectPair = (i: number, j: number): void => {
    const left = active[i];
    const right = active[j];
    if (left.translation.trim() === right.translation.trim()) return;
    if (left.source.trim() === right.source.trim()) return;
    if (entriesOverlap(left, right, regexFor)) {
      addConflict("overlap", [left.id, right.id]);
    }
  };

  for (let i = 0; i < active.length; i += 1) {
    const left = active[i];
    if (left.regex) {
      for (let j = i + 1; j < active.length; j += 1) inspectPair(i, j);
      continue;
    }

    const plainCandidates =
      plainIndicesByFoldedSource.get(left.source.trim().toLowerCase()) ?? [];
    inspectMergedCandidates(i, plainCandidates, regexIndices, inspectPair);
  }

  return {
    byEntryId: new Map(
      Array.from(byEntryId.entries()).map(([id, kinds]) => [
        id,
        Array.from(kinds),
      ]),
    ),
    conflicts,
    firstEntryId: conflicts[0]?.entryIds[0] ?? null,
  };
}

function inspectMergedCandidates(
  currentIndex: number,
  plainCandidates: number[],
  regexCandidates: number[],
  inspect: (leftIndex: number, rightIndex: number) => void,
): void {
  let plainCursor = firstIndexAfter(plainCandidates, currentIndex);
  let regexCursor = firstIndexAfter(regexCandidates, currentIndex);

  while (
    plainCursor < plainCandidates.length ||
    regexCursor < regexCandidates.length
  ) {
    const plainIndex = plainCandidates[plainCursor] ?? Number.POSITIVE_INFINITY;
    const regexIndex = regexCandidates[regexCursor] ?? Number.POSITIVE_INFINITY;
    if (plainIndex < regexIndex) {
      inspect(currentIndex, plainIndex);
      plainCursor += 1;
    } else {
      inspect(currentIndex, regexIndex);
      regexCursor += 1;
    }
  }
}

function firstIndexAfter(indices: number[], value: number): number {
  let low = 0;
  let high = indices.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (indices[middle] <= value) low = middle + 1;
    else high = middle;
  }
  return low;
}

function entriesOverlap(
  left: GlossaryEntry,
  right: GlossaryEntry,
  regexFor: (entry: GlossaryEntry) => RegExp | null,
): boolean {
  if (!left.regex && !right.regex) {
    if (left.caseSensitive && right.caseSensitive) return false;
    return (
      left.source.trim().toLowerCase() === right.source.trim().toLowerCase()
    );
  }
  if (left.regex && right.regex) {
    if (!regexFor(left) || !regexFor(right)) return false;
    return (
      (left.source.trim() === right.source.trim() &&
        left.caseSensitive === right.caseSensitive) ||
      regexMatchesSource(regexFor(left), right.source.trim()) ||
      regexMatchesSource(regexFor(right), left.source.trim())
    );
  }
  const regexEntry = left.regex ? left : right;
  const plainEntry = left.regex ? right : left;
  return regexMatchesSource(regexFor(regexEntry), plainEntry.source.trim());
}

function compileRegex(entry: GlossaryEntry): RegExp | null {
  try {
    return new RegExp(entry.source, entry.caseSensitive ? "" : "i");
  } catch {
    return null;
  }
}

function regexMatchesSource(compiled: RegExp | null, source: string): boolean {
  return compiled ? compiled.test(source) : false;
}
