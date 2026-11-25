import type { Batch } from "./api";
import { batchWorkbenchSheetOrder, workbenchSheetTarget } from "./workbenchSheets";

export function weeklyImportSheetTarget(group: string, batch?: Partial<Batch> | null): string {
  if (!group || !batchWorkbenchSheetOrder(batch).includes(group)) return "";
  return batch?.workbench_sheet_options?.includes(group) ? group : workbenchSheetTarget(group, batch);
}
