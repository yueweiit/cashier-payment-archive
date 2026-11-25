import type { Batch, PaymentRequest } from "./api";
import { batchWorkbenchSheetOrder, requestWorkbenchSheet } from "./workbenchSheets";

export function requestEditorSheetChoices(batch: Partial<Batch>): string[] {
  return batchWorkbenchSheetOrder(batch).filter((group) => group !== "全部" && group !== "__all__");
}

export function selectRequestEditorSheet<T extends Partial<PaymentRequest>>(
  original: Partial<PaymentRequest>,
  draft: T,
  group: string,
  batch: Partial<Batch>,
  canEdit: boolean,
): T {
  if (!canEdit || draft.erp_payment_owner === "deeplinkerp") return draft;
  if (group === requestWorkbenchSheet(original)) {
    const restored = { ...draft, source_sheet: original.source_sheet, workbench_sheet: original.workbench_sheet };
    if (!("source_sheet" in original)) delete restored.source_sheet;
    if (!("workbench_sheet" in original)) delete restored.workbench_sheet;
    return restored;
  }
  if (!requestEditorSheetChoices(batch).includes(group)) return draft;
  const target = batch.workbench_sheet_options?.includes(group)
    ? group
    : batch.workbench_sheet_targets?.[group] || group;
  return { ...draft, source_sheet: target, workbench_sheet: group };
}
