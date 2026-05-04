export function uniqueRows<T>(rows: T[], keyOf: (row: T) => string): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const row of rows) {
    const key = keyOf(row);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

export function appendUniqueRows<T>(
  existing: T[],
  incoming: T[],
  keyOf: (row: T) => string,
): { rows: T[]; added: T[]; skipped: number } {
  const seen = new Set(existing.map(keyOf));
  const added: T[] = [];
  let skipped = 0;
  for (const row of incoming) {
    const key = keyOf(row);
    if (seen.has(key)) {
      skipped += 1;
      continue;
    }
    seen.add(key);
    added.push(row);
  }
  return { rows: [...existing, ...added], added, skipped };
}

export function tableRowKey(values: unknown[]): string {
  return JSON.stringify(values);
}
