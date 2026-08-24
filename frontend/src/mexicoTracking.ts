import type { Language } from "./i18n";

export async function copyTextWithFallback(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch {
      // HTTP deployments and restrictive browser policies can reject the
      // modern clipboard API. Fall through to the selection-based copy path.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.readOnly = true;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("无法复制，请手动选择文字复制");
}

export function mexicoSourceLabel(source: string | null | undefined, language: Language): string {
  const labels: Record<string, [string, string]> = {
    operation: ["运营支出", "Gasto operativo"],
    purchase: ["采购支出", "Gasto de compra"],
    monthly: ["月结付款", "Pago mensual"],
  };
  const value = labels[String(source || "")];
  if (!value) return source || "—";
  return language === "es" ? value[1] : value[0];
}

export function mexicoSyncPhaseLabel(phase: string | null | undefined, language: Language): string {
  const labels: Record<string, [string, string]> = {
    queued: ["等待同步", "En espera"],
    querying_sources: ["正在查询审批来源", "Consultando solicitudes"],
    resolving_regions: ["正在判定执行地区", "Clasificando región"],
    querying_workflows: ["正在查询流程节点", "Consultando etapas"],
    committing_state: ["正在更新流程状态", "Actualizando estados"],
    querying_attachments: ["正在查询附件", "Consultando archivos"],
    syncing_attachments: ["正在同步附件", "Sincronizando archivos"],
    complete: ["同步完成", "Sincronización completada"],
    completed: ["同步完成", "Sincronización completada"],
    failed: ["同步失败", "Error de sincronización"],
    interrupted: ["同步已中断", "Sincronización interrumpida"],
  };
  const value = labels[String(phase || "queued")];
  if (!value) return phase || "—";
  return language === "es" ? value[1] : value[0];
}

export function compactDateTime(value: string | null | undefined, language: Language): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language === "es" ? "es-MX" : "zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatOriginalMoney(amount: number | null | undefined, currency: string | null | undefined, language: Language): string {
  if (amount === null || amount === undefined) return "—";
  const code = currency || "CNY";
  try {
    return new Intl.NumberFormat(language === "es" ? "es-MX" : "zh-CN", {
      style: "currency",
      currency: code,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${code} ${Number(amount).toLocaleString()}`;
  }
}
