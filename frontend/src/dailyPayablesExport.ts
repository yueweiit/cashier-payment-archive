export type DailyPayablesExportErrorCode =
  | "EXPORT_DATE_REQUIRED"
  | "HISTORY_NOT_AVAILABLE"
  | "INVALID_EXPORT_RANGE"
  | "FUTURE_EXPORT_DATE"
  | "EXPORT_RANGE_TOO_LARGE";

function parseIsoDate(value: string) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

function formatIsoDate(value: Date) {
  return value.toISOString().slice(0, 10);
}

function shiftDays(value: string, days: number) {
  const result = parseIsoDate(value);
  result.setUTCDate(result.getUTCDate() + days);
  return formatIsoDate(result);
}

function shiftCalendarMonths(value: string, months: number) {
  const original = parseIsoDate(value);
  const targetMonthStart = new Date(Date.UTC(original.getUTCFullYear(), original.getUTCMonth() + months, 1));
  const targetMonthEnd = new Date(Date.UTC(targetMonthStart.getUTCFullYear(), targetMonthStart.getUTCMonth() + 1, 0));
  targetMonthStart.setUTCDate(Math.min(original.getUTCDate(), targetMonthEnd.getUTCDate()));
  return formatIsoDate(targetMonthStart);
}

export function defaultDailyPayablesExportRange(selectedDate: string, historyStart: string, today: string) {
  const end = selectedDate && selectedDate < today ? selectedDate : today;
  const candidateStart = shiftDays(end, -29);
  return {
    start: candidateStart < historyStart ? historyStart : candidateStart,
    end,
  };
}

export function validateDailyPayablesExportRange(
  start: string,
  end: string,
  historyStart: string,
  today: string,
): DailyPayablesExportErrorCode | null {
  if (!start || !end) return "EXPORT_DATE_REQUIRED";
  if (start < historyStart) return "HISTORY_NOT_AVAILABLE";
  if (start > end) return "INVALID_EXPORT_RANGE";
  if (end > today) return "FUTURE_EXPORT_DATE";
  if (end >= shiftCalendarMonths(start, 6)) return "EXPORT_RANGE_TOO_LARGE";
  return null;
}
