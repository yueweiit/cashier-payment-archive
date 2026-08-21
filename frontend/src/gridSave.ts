export function buildDirtyGridPayload(
  row: Record<string, unknown>,
  dirtyCells: ReadonlySet<string>,
  excludedFields: ReadonlySet<string>,
): Record<string, unknown> {
  const localId = String(row.__localId || "");
  const prefix = `${localId}:`;
  const payload: Record<string, unknown> = {};

  dirtyCells.forEach((cell) => {
    if (!cell.startsWith(prefix)) return;
    const field = cell.slice(prefix.length);
    if (!field || excludedFields.has(field)) return;
    const value = row[field];
    // JSON.stringify drops undefined. Use null so clearing an optional number/date
    // still reaches the API; keep an empty string when a text field was cleared.
    payload[field] = value === undefined ? null : value;
  });

  return payload;
}

export function sameSheetOrder(left: readonly string[], right: readonly string[]): boolean {
  const normalize = (items: readonly string[]) => items.map((item) => String(item || "").trim()).filter(Boolean);
  const normalizedLeft = normalize(left);
  const normalizedRight = normalize(right);
  return normalizedLeft.length === normalizedRight.length
    && normalizedLeft.every((item, index) => item === normalizedRight[index]);
}
