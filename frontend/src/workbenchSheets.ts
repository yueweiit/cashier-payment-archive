import type { Batch, PaymentRequest } from "./api";

export function normalizeWorkbenchSheet(value?: string): string {
  const name = String(value || "").trim();
  if (!name) return "未分 Sheet";
  const legacy = name.match(/^(赣瑞模具|志威模具)\s*(?:[（(]\s*7\s*月\s*(?:前|后)\s*[）)]|7\s*月\s*(?:前|后))$/);
  return legacy?.[1] || name;
}

export function requestWorkbenchSheet(row: Partial<PaymentRequest>): string {
  return normalizeWorkbenchSheet(row.workbench_sheet?.trim() || row.source_sheet);
}

export function batchWorkbenchSheetOrder(batch?: Partial<Batch> | null): string[] {
  return Array.from(new Set((batch?.workbench_sheet_order ?? batch?.sheet_order ?? []).map(normalizeWorkbenchSheet)));
}

export function workbenchSheetTarget(group: string, batch?: Partial<Batch> | null, rows: Partial<PaymentRequest>[] = []): string {
  const displayGroup = normalizeWorkbenchSheet(group);
  return batch?.workbench_sheet_targets?.[displayGroup]
    || rows.find((row) => requestWorkbenchSheet(row) === displayGroup && row.source_sheet?.trim())?.source_sheet
    || displayGroup;
}

export function moveToWorkbenchSheet<T extends Partial<PaymentRequest>>(row: T, group: string, rawTarget: string): T {
  const displayGroup = normalizeWorkbenchSheet(group);
  const sameRawDifferentGroup = row.source_sheet === rawTarget && requestWorkbenchSheet(row) !== displayGroup;
  return { ...row, source_sheet: sameRawDifferentGroup ? displayGroup : rawTarget, workbench_sheet: displayGroup };
}

export function workbenchGridColumnOrder(order: readonly string[]): string[] {
  const expected = "expected_payment_account";
  const result = Array.from(new Set(order.filter((key) => key !== "source_sheet" && key !== expected)));
  const sourceIndex = order.indexOf("source_sheet");
  const existingIndex = order.indexOf(expected);
  const paymentIndex = result.indexOf("payment_account");
  const numberIndex = result.indexOf("dingding_id");
  const target = sourceIndex >= 0 && sourceIndex <= 3 ? sourceIndex
    : existingIndex >= 0 && existingIndex <= 3 ? existingIndex
      : paymentIndex >= 0 && paymentIndex <= 2 ? paymentIndex + 1
        : numberIndex >= 0 && numberIndex <= 2 ? numberIndex + 1 : 0;
  result.splice(Math.min(target, result.length), 0, expected);
  return result;
}

export function workbenchGridColumnVisible(key: string, hidden: ReadonlySet<string>): boolean {
  return key !== "source_sheet" && (key === "expected_payment_account" || !hidden.has(key));
}

export function canRenameWorkbenchSheet(group: string, batch?: Partial<Batch> | null, rows: Partial<PaymentRequest>[] = []): boolean {
  const displayGroup = normalizeWorkbenchSheet(group);
  const target = batch?.workbench_sheet_targets?.[displayGroup];
  if (target && normalizeWorkbenchSheet(target) !== displayGroup) return false;
  return rows.filter((row) => requestWorkbenchSheet(row) === displayGroup)
    .every((row) => normalizeWorkbenchSheet(row.source_sheet) === displayGroup);
}

export function shouldResetWorkbenchSheets(previousBatchId: number | null, batchId: number | null, hasUnsavedChanges: boolean): boolean {
  return previousBatchId !== batchId || !hasUnsavedChanges;
}

export function withoutWorkbenchSheet<T extends Partial<PaymentRequest>>(request: T): Omit<T, "workbench_sheet"> {
  const { workbench_sheet, ...writable } = request;
  return writable;
}
