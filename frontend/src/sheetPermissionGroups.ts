import type { SheetGroupPermissionState } from "./api";

export type SheetGroupChanges = Record<string, boolean>;

export function sheetGroupPermissionState(
  group: string,
  states: Record<string, SheetGroupPermissionState> = {},
  changes: SheetGroupChanges = {},
): SheetGroupPermissionState {
  if (Object.prototype.hasOwnProperty.call(changes, group)) return changes[group] ? "all" : "none";
  return states[group] === "all" || states[group] === "partial" ? states[group] : "none";
}

export function changeSheetGroupPermission(changes: SheetGroupChanges, group: string, allowed: boolean): SheetGroupChanges {
  return { ...changes, [group]: allowed };
}

export function changeLegacySheetPermission(
  original: readonly string[],
  current: readonly string[],
  permission: string,
  allowed: boolean,
): string[] {
  return original.filter((item) => item === permission ? allowed : current.includes(item));
}

export function sheetPermissionPatch(
  original: readonly string[],
  current: readonly string[],
  changes: SheetGroupChanges,
): { sheet_permissions?: string[]; sheet_group_changes?: SheetGroupChanges } {
  const retained = original.filter((permission) => current.includes(permission));
  return {
    ...(retained.length !== original.length ? { sheet_permissions: retained } : {}),
    ...(Object.keys(changes).length ? { sheet_group_changes: { ...changes } } : {}),
  };
}
