import type { ExternalExpenseImportItem, ExternalExpensePreviewRow } from "./api";

type PreviewGroupRow = Pick<ExternalExpensePreviewRow, "source_type" | "source_id" | "importable" | "needs_workbench_sheet">;
export type ExternalExpenseGroupAssignments = Record<string, string>;

export function externalExpenseRowKey(row: Pick<PreviewGroupRow, "source_type" | "source_id">): string {
  return `${row.source_type}:${row.source_id}`;
}

export function toggleExternalExpenseKeys(selected: ReadonlySet<string>, keys: readonly string[]): Set<string> {
  const next = new Set(selected);
  const remove = keys.length > 0 && keys.every((key) => selected.has(key));
  keys.forEach((key) => remove ? next.delete(key) : next.add(key));
  return next;
}

export function externalExpenseImportPlan(
  rows: readonly PreviewGroupRow[],
  selected: ReadonlySet<string>,
  assignments: ExternalExpenseGroupAssignments,
  options: readonly string[],
) {
  const byKey = new Map(rows.map((row) => [externalExpenseRowKey(row), row]));
  const items: ExternalExpenseImportItem[] = [];
  const missingKeys: string[] = [];
  const unavailableKeys: string[] = [];
  for (const key of selected) {
    const row = byKey.get(key);
    if (!row?.importable) {
      unavailableKeys.push(key);
      continue;
    }
    const item: ExternalExpenseImportItem = { source_type: row.source_type, source_id: row.source_id };
    if (row.needs_workbench_sheet) {
      const group = assignments[key]?.trim();
      if (!group || !options.includes(group)) {
        missingKeys.push(key);
        continue;
      }
      item.workbench_sheet = group;
    }
    items.push(item);
  }
  return { items, missingKeys, unavailableKeys };
}

export function assignExternalExpenseGroup(
  rows: readonly PreviewGroupRow[],
  selected: ReadonlySet<string>,
  assignments: ExternalExpenseGroupAssignments,
  group: string,
  options: readonly string[],
): ExternalExpenseGroupAssignments {
  if (!options.includes(group)) throw new Error("请选择已有的导入分组");
  const next = { ...assignments };
  for (const row of rows) {
    const key = externalExpenseRowKey(row);
    if (row.importable && row.needs_workbench_sheet && selected.has(key)) next[key] = group;
  }
  return next;
}
