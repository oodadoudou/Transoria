export async function applyRunSelection<T, E>(
  select: () => Promise<T | null>,
  readError: () => E | null,
  reportError: (error: E | null) => void,
): Promise<boolean> {
  const result = await select();
  reportError(result === null ? readError() : null);
  return result !== null;
}
