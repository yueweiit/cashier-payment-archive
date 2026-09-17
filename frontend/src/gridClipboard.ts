type ClipboardColumn = { key: string; type?: "number" | "date" };
type EditableGridRecord = { id?: number; currency?: string; __deleted?: boolean };

export function canDirectlyEditGridField(row: EditableGridRecord, field: string): boolean {
  if (row.__deleted) return false;
  if (!row.id) return true;
  if (field === "currency") return false;
  return field !== "amount" || String(row.currency || "CNY").toUpperCase() === "CNY";
}

export function normalizeCellValue(column: ClipboardColumn, value: string): string | number | undefined {
  if (column.type === "number") {
    if (!value.trim()) return undefined;
    const normalized = value.trim()
      .replace(/^(?:US\$|MX\$|RMB|CNY|USD|MXN|[$¥￥])\s*/i, "")
      .replace(/\s*(?:RMB|CNY|USD|MXN)$/i, "");
    if (!/^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+|\.\d+)(?:\.\d*)?$/.test(normalized)) {
      throw new Error("金额格式无效，请使用数字、小数点及千位分隔符");
    }
    const amount = Number(normalized.replace(/,/g, ""));
    if (!Number.isFinite(amount)) throw new Error("金额超出有效范围");
    return amount;
  }
  if (column.type === "date") {
    const text = value.trim();
    if (!text) return "";
    const parts = text.match(/^(\d{4})([-/])(\d{1,2})\2(\d{1,2})(?:[ T](?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d+)?)?)?$/);
    if (!parts) throw new Error("日期格式无效，请使用 YYYY-MM-DD 或 YYYY/M/D");
    const normalized = `${parts[1]}-${parts[3].padStart(2, "0")}-${parts[4].padStart(2, "0")}`;
    const date = new Date(`${normalized}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== normalized) {
      throw new Error("日期不存在，请检查年月日");
    }
    return normalized;
  }
  if (column.key === "currency") {
    const currency = value.trim().toUpperCase();
    if (!["CNY", "USD", "MXN"].includes(currency)) throw new Error("币种请选择 CNY、USD 或 MXN");
    return currency;
  }
  // Identifiers and account numbers must never pass through Number().
  return value;
}

export function parseClipboardTable(text: string): string[][] {
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let quoteClosed = false;

  for (let index = 0; index < normalized.length; index += 1) {
    const character = normalized[index];
    if (quoted) {
      if (character === '"') {
        if (normalized[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
          quoteClosed = true;
        }
      } else {
        cell += character;
      }
      continue;
    }
    if (character === "\t" || character === "\n") {
      row.push(cell);
      cell = "";
      quoteClosed = false;
      if (character === "\n") {
        rows.push(row);
        row = [];
      }
    } else if (quoteClosed) {
      throw new Error("粘贴内容的引号格式无效，请重新从 Excel 复制");
    } else if (character === '"' && cell === "") {
      quoted = true;
    } else {
      cell += character;
    }
  }
  if (quoted) throw new Error("粘贴内容有未闭合的引号，请重新从 Excel 复制");
  if (row.length || cell || !normalized.endsWith("\n")) rows.push([...row, cell]);
  return rows;
}
