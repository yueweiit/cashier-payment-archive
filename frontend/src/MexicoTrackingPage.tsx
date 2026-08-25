import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardCopy,
  Clock3,
  ExternalLink,
  FileText,
  History,
  LoaderCircle,
  MapPin,
  Paperclip,
  RefreshCcw,
  Search,
  Settings2,
  SlidersHorizontal,
  UserRound,
  X,
} from "lucide-react";
import {
  api,
  MexicoSyncRun,
  MexicoTrackingDetail,
  MexicoTrackingFilterOptions,
  MexicoTrackingItem,
  MexicoTrackingListParams,
  MexicoTrackingSettings,
  MexicoTrackingSummary,
  MexicoTrackingView,
  MexicoWarningLevel,
  User,
} from "./api";
import { useLanguage } from "./i18n";
import {
  compactDateTime,
  copyTextWithFallback,
  formatOriginalMoney,
  mexicoSourceLabel,
  mexicoSyncPhaseLabel,
} from "./mexicoTracking";

type Props = {
  user: User;
  setMessage: (message: string) => void;
};

type Filters = {
  keyword: string;
  company: string;
  sourceType: string;
  applicant: string;
  approver: string;
  node: string;
  warning: "" | MexicoWarningLevel;
  requestDateFrom: string;
  requestDateTo: string;
};

const emptySummary: MexicoTrackingSummary = {
  pending: 0,
  history: 0,
  review: 0,
  normal: 0,
  yellow: 0,
  red: 0,
};

const emptyOptions: MexicoTrackingFilterOptions = {
  companies: [],
  sheets: [],
  source_types: [],
  applicants: [],
  approvers: [],
  nodes: [],
};

const emptyFilters: Filters = {
  keyword: "",
  company: "",
  sourceType: "",
  applicant: "",
  approver: "",
  node: "",
  warning: "",
  requestDateFrom: "",
  requestDateTo: "",
};

const terminalSyncStates = new Set(["completed", "failed", "interrupted"]);

function workflowStatusLabel(status: string | null | undefined, language: "zh" | "es") {
  const labels: Record<string, [string, string]> = {
    RUNNING: ["审批中", "En aprobación"],
    COMPLETED: ["已完成", "Completada"],
    TERMINATED: ["已终止", "Terminada"],
  };
  const value = labels[String(status || "").toUpperCase()];
  return value ? (language === "es" ? value[1] : value[0]) : status || "—";
}

function warningLabel(level: MexicoWarningLevel, language: "zh" | "es") {
  if (level === "red") return language === "es" ? "Urgente" : "严重超时";
  if (level === "yellow") return language === "es" ? "Atención" : "需要关注";
  return language === "es" ? "Normal" : "正常";
}

function formatReminder(item: MexicoTrackingItem, language: "zh" | "es") {
  if (!item.reminder) return "";
  return `${item.reminder.zh}\n\n${item.reminder.es}`;
}

export function MexicoTrackingPage({ user, setMessage }: Props) {
  const { language, t } = useLanguage();
  const [view, setView] = useState<MexicoTrackingView>("pending");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(emptyFilters);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [items, setItems] = useState<MexicoTrackingItem[]>([]);
  const [summary, setSummary] = useState<MexicoTrackingSummary>(emptySummary);
  const [options, setOptions] = useState<MexicoTrackingFilterOptions>(emptyOptions);
  const [settings, setSettings] = useState<MexicoTrackingSettings | null>(null);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<MexicoTrackingDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [syncRun, setSyncRun] = useState<MexicoSyncRun | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const requestSequence = useRef(0);
  const autoSyncStarted = useRef(false);
  const syncPollInFlight = useRef(false);
  const lastRefreshedStateCommit = useRef<string | null>(null);

  const canAdmin = user.role === "admin";

  const listParams = useMemo<MexicoTrackingListParams>(() => ({
    view,
    page,
    page_size: 50,
    keyword: appliedFilters.keyword,
    company: appliedFilters.company,
    source_type: appliedFilters.sourceType,
    applicant: appliedFilters.applicant,
    approver: appliedFilters.approver,
    node: appliedFilters.node,
    warning: appliedFilters.warning,
    request_date_from: appliedFilters.requestDateFrom,
    request_date_to: appliedFilters.requestDateTo,
  }), [view, page, appliedFilters]);

  const loadList = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    setError("");
    try {
      const response = await api.mexicoTrackingList(listParams);
      if (sequence !== requestSequence.current) return;
      setItems(response.items);
      setTotal(response.total);
      setPages(response.pages);
      if (response.page > response.pages) setPage(response.pages);
    } catch (reason) {
      if (sequence !== requestSequence.current) return;
      setError((reason as Error).message);
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [listParams]);

  const loadOverview = useCallback(async () => {
    try {
      const [summaryResponse, optionsResponse, settingsResponse] = await Promise.all([
        api.mexicoTrackingSummary(),
        api.mexicoTrackingFilterOptions(),
        api.mexicoTrackingSettings(),
      ]);
      setSummary(summaryResponse.summary);
      setOptions(optionsResponse.options);
      setSettings(settingsResponse.settings);
    } catch (reason) {
      setError((reason as Error).message);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadList(), loadOverview()]);
  }, [loadList, loadOverview]);

  const reloadDetail = useCallback(async (id: number, closeOnError = false) => {
    setDetailLoading(true);
    try {
      const response = await api.mexicoTrackingDetail(id);
      setDetail(response.item);
    } catch (reason) {
      setMessage((reason as Error).message);
      if (closeOnError) setSelectedId(null);
    } finally {
      setDetailLoading(false);
    }
  }, [setMessage]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  const startSync = useCallback(async (manual: boolean) => {
    try {
      const response = await api.startMexicoTrackingSync(manual ? 0 : 300, manual ? "manual" : "automatic");
      setSyncRun(response.run);
      if (response.run.status === "completed") await refreshAll();
      if (manual) {
        setMessage(response.reused
          ? t("正在复用已有的墨西哥审批同步任务", "Se está reutilizando la sincronización en curso")
          : t("已开始同步墨西哥审批", "La sincronización de México ha comenzado"));
      }
    } catch (reason) {
      if (manual) setMessage((reason as Error).message);
    }
  }, [refreshAll, setMessage, t]);

  useEffect(() => {
    if (autoSyncStarted.current) return;
    autoSyncStarted.current = true;
    startSync(false);
  }, [startSync]);

  useEffect(() => {
    if (!syncRun || terminalSyncStates.has(syncRun.status)) return;
    const timer = window.setInterval(async () => {
      if (syncPollInFlight.current) return;
      syncPollInFlight.current = true;
      try {
        const response = await api.mexicoTrackingSyncRun(syncRun.id);
        setSyncRun(response.run);
        const stateCommit = response.run.state_committed_at || null;
        if (stateCommit && stateCommit !== lastRefreshedStateCommit.current) {
          lastRefreshedStateCommit.current = stateCommit;
          await refreshAll();
          if (selectedId !== null) await reloadDetail(selectedId);
          setMessage(t(
            "审批状态已更新，附件正在后台处理",
            "El estado ya está actualizado; los archivos continúan en segundo plano",
          ));
        }
        if (response.run.status === "completed") {
          await refreshAll();
          setMessage(t("墨西哥审批同步完成", "Sincronización de México completada"));
        } else if (response.run.status === "failed") {
          setMessage(response.run.error_message || t("墨西哥审批同步失败", "Falló la sincronización de México"));
        }
      } catch (reason) {
        setMessage((reason as Error).message);
      } finally {
        syncPollInFlight.current = false;
      }
    }, 1500);
    return () => {
      window.clearInterval(timer);
      syncPollInFlight.current = false;
    };
  }, [syncRun?.id, syncRun?.status, refreshAll, reloadDetail, selectedId, setMessage, t]);

  async function openDetail(id: number) {
    setSelectedId(id);
    setDetail(null);
    await reloadDetail(id, true);
  }

  function changeView(next: MexicoTrackingView) {
    setView(next);
    setPage(1);
    setSelectedId(null);
  }

  function selectView(next: MexicoTrackingView) {
    setFilters(emptyFilters);
    setAppliedFilters(emptyFilters);
    changeView(next);
  }

  function applyFilters() {
    setAppliedFilters({ ...filters });
    setPage(1);
  }

  function resetFilters() {
    setFilters(emptyFilters);
    setAppliedFilters(emptyFilters);
    setPage(1);
  }

  async function copyReminder(item: MexicoTrackingItem) {
    const text = formatReminder(item, language);
    if (!text) return;
    try {
      await copyTextWithFallback(text);
      setMessage(t("双语提醒已复制", "Recordatorio bilingüe copiado"));
    } catch (reason) {
      setMessage((reason as Error).message);
    }
  }

  const activeFilterCount = Object.values(appliedFilters).filter(Boolean).length;
  const syncActive = Boolean(syncRun && !terminalSyncStates.has(syncRun.status));
  const attachmentSyncActive = Boolean(
    syncRun && ["querying_attachments", "syncing_attachments"].includes(syncRun.phase),
  );
  const syncPercent = syncRun?.total_count
    ? Math.min(100, Math.round((syncRun.processed_count / syncRun.total_count) * 100))
    : syncRun?.attachment_total_count
      ? Math.min(100, Math.round((syncRun.attachment_processed_count / syncRun.attachment_total_count) * 100))
      : 0;

  return (
    <section className="mexico-tracking-page" data-page="mexico-tracking">
      <div className="mexico-tracking-hero">
        <div>
          <div className="mexico-eyebrow"><MapPin size={16} /> México</div>
          <h2>{t("墨西哥审批跟进", "Seguimiento de aprobaciones de México")}</h2>
          <p>{t("只跟进流程进度，不参与中国应付统计或付款记账。", "Solo da seguimiento al flujo; no afecta los pagos ni las estadísticas de China.")}</p>
        </div>
        <div className="mexico-hero-actions">
          {canAdmin && <button className="icon-text" type="button" onClick={() => setSettingsOpen(true)}><Settings2 size={16} />{t("预警设置", "Configurar alertas")}</button>}
          <button className="primary icon-text" type="button" disabled={syncActive} onClick={() => startSync(true)}>
            {syncActive ? <LoaderCircle className="spin" size={16} /> : <RefreshCcw size={16} />}
            {syncActive ? mexicoSyncPhaseLabel(syncRun?.phase, language) : t("同步钉钉审批", "Sincronizar DingTalk")}
          </button>
        </div>
      </div>

      {syncRun && (syncActive || syncRun.status === "failed") && (
        <div className={`mexico-sync-status ${syncRun.status}`} role="status">
          <div className="mexico-sync-status-head">
            <span>
              {syncActive && <LoaderCircle className="spin" size={15} />}
              {attachmentSyncActive
                ? t("附件正在后台处理", "Los archivos se procesan en segundo plano")
                : mexicoSyncPhaseLabel(syncRun.phase, language)}
            </span>
            <strong>{syncRun.phase === "syncing_attachments"
              ? `${syncRun.attachment_processed_count}/${syncRun.attachment_total_count}`
              : `${syncRun.processed_count}/${syncRun.total_count}`}</strong>
          </div>
          {syncActive && <div className="mexico-sync-progress"><span style={{ width: `${syncPercent}%` }} /></div>}
          {syncRun.error_message && <small>{syncRun.error_message}</small>}
        </div>
      )}

      <div className="mexico-kpi-grid">
        <button className={view === "pending" && !appliedFilters.warning ? "active" : ""} onClick={() => selectView("pending")}>
          <Clock3 /> <span>{t("待审批", "Pendientes")}</span><strong>{summary.pending}</strong>
        </button>
        <button className={view === "pending" && appliedFilters.warning === "yellow" ? "active warning-yellow" : "warning-yellow"} onClick={() => { setFilters({ ...emptyFilters, warning: "yellow" }); setAppliedFilters({ ...emptyFilters, warning: "yellow" }); changeView("pending"); }}>
          <AlertTriangle /> <span>{t("超过关注时限", "Requieren atención")}</span><strong>{summary.yellow}</strong>
        </button>
        <button className={view === "pending" && appliedFilters.warning === "red" ? "active warning-red" : "warning-red"} onClick={() => { setFilters({ ...emptyFilters, warning: "red" }); setAppliedFilters({ ...emptyFilters, warning: "red" }); changeView("pending"); }}>
          <AlertTriangle /> <span>{t("严重超时", "Urgentes")}</span><strong>{summary.red}</strong>
        </button>
        <button className={view === "history" ? "active" : ""} onClick={() => selectView("history")}>
          <History /> <span>{t("历史流程", "Historial")}</span><strong>{summary.history}</strong>
        </button>
        {canAdmin && (
          <button className={view === "review" ? "active warning-neutral" : "warning-neutral"} onClick={() => selectView("review")}>
            <MapPin /> <span>{t("地区待核对", "Región por revisar")}</span><strong>{summary.review}</strong>
          </button>
        )}
      </div>

      <div className="mexico-filter-panel">
        <div className="mexico-filter-main">
          <label className="mexico-search-field">
            <Search size={17} />
            <input value={filters.keyword} onChange={(event) => setFilters((current) => ({ ...current, keyword: event.target.value }))} onKeyDown={(event) => event.key === "Enter" && applyFilters()} placeholder={t("搜索单号、申请人、摘要", "Buscar número, solicitante o concepto")} />
          </label>
          <select value={filters.company} onChange={(event) => setFilters((current) => ({ ...current, company: event.target.value }))}>
            <option value="">{t("全部子公司", "Todas las empresas")}</option>
            {options.companies.map((value) => <option key={value}>{value}</option>)}
          </select>
          <select value={filters.approver} onChange={(event) => setFilters((current) => ({ ...current, approver: event.target.value }))}>
            <option value="">{t("全部当前审批人", "Todos los aprobadores")}</option>
            {options.approvers.map((value) => <option key={value}>{value}</option>)}
          </select>
          <button className="icon-text" type="button" onClick={() => setShowAdvanced((value) => !value)}><SlidersHorizontal size={16} />{t("更多筛选", "Más filtros")}{activeFilterCount > 0 && <b>{activeFilterCount}</b>}</button>
          <button className="primary" type="button" onClick={applyFilters}>{t("查询", "Buscar")}</button>
        </div>
        {showAdvanced && (
          <div className="mexico-filter-extra">
            <select value={filters.applicant} onChange={(event) => setFilters((current) => ({ ...current, applicant: event.target.value }))}><option value="">{t("全部申请人", "Todos los solicitantes")}</option>{options.applicants.map((value) => <option key={value}>{value}</option>)}</select>
            <select value={filters.node} onChange={(event) => setFilters((current) => ({ ...current, node: event.target.value }))}><option value="">{t("全部流程节点", "Todas las etapas")}</option>{options.nodes.map((value) => <option key={value}>{value}</option>)}</select>
            <select value={filters.sourceType} onChange={(event) => setFilters((current) => ({ ...current, sourceType: event.target.value }))}><option value="">{t("全部来源", "Todos los orígenes")}</option>{options.source_types.map((value) => <option key={value} value={value}>{mexicoSourceLabel(value, language)}</option>)}</select>
            <select value={filters.warning} onChange={(event) => setFilters((current) => ({ ...current, warning: event.target.value as Filters["warning"] }))}><option value="">{t("全部预警", "Todas las alertas")}</option><option value="normal">{t("正常", "Normal")}</option><option value="yellow">{t("需要关注", "Atención")}</option><option value="red">{t("严重超时", "Urgente")}</option></select>
            <label>{t("申请日期从", "Desde")}<input type="date" value={filters.requestDateFrom} onChange={(event) => setFilters((current) => ({ ...current, requestDateFrom: event.target.value }))} /></label>
            <label>{t("至", "Hasta")}<input type="date" value={filters.requestDateTo} onChange={(event) => setFilters((current) => ({ ...current, requestDateTo: event.target.value }))} /></label>
            <button type="button" onClick={resetFilters}>{t("清空筛选", "Limpiar filtros")}</button>
          </div>
        )}
      </div>

      <div className="mexico-list-heading">
        <div><strong>{view === "pending" ? t("当前待审批", "Pendientes actuales") : view === "history" ? t("历史流程", "Historial") : t("地区待核对", "Región por revisar")}</strong><span>{total} {t("条", "registros")}</span></div>
        {settings && <small>{t("预警阈值", "Umbrales")}: {settings.yellow_days}/{settings.red_days} {t("天", "días")}</small>}
      </div>

      {error && <div className="mexico-error" role="alert">{error}<button onClick={loadList}>{t("重试", "Reintentar")}</button></div>}
      <div className={`mexico-table-wrap ${loading ? "loading" : ""}`}>
        {loading && <div className="mexico-loading"><LoaderCircle className="spin" />{t("加载中", "Cargando")}</div>}
        {!loading && !items.length && <div className="mexico-empty"><CheckCircle2 /><strong>{t("当前没有符合条件的审批", "No hay solicitudes que coincidan")}</strong><span>{t("可以调整筛选条件，或同步最新钉钉流程。", "Ajuste los filtros o sincronice DingTalk.")}</span></div>}
        {items.length > 0 && (
          <>
            <table className="mexico-tracking-table">
              <thead><tr><th>{t("预警", "Alerta")}</th><th>{t("钉钉单号", "N.º DingTalk")}</th><th>{t("子公司", "Empresa")}</th><th>{t("申请人", "Solicitante")}</th><th>{t("摘要", "Concepto")}</th><th>{t("当前节点", "Etapa actual")}</th><th>{t("当前审批人", "Aprobador actual")}</th><th>{t("停留", "Tiempo")}</th><th>{t("操作", "Acciones")}</th></tr></thead>
              <tbody>{items.map((item) => (
                <tr key={item.id} className={`mexico-row warning-${item.warning_level}`} onDoubleClick={() => openDetail(item.id)}>
                  <td><span className={`mexico-warning-chip ${item.warning_level}`}>{warningLabel(item.warning_level, language)}</span></td>
                  <td><button className="link-button" onClick={() => openDetail(item.id)}>{item.approval_no}</button><small>{mexicoSourceLabel(item.source_type, language)} · {item.request_date || "—"}</small></td>
                  <td>{item.company_name || item.source_sheet || "—"}</td>
                  <td>{item.applicant_name || "—"}<small>{item.applicant_department || ""}</small></td>
                  <td className="mexico-summary-cell"><span>{item.summary || "—"}</span><small>{formatOriginalMoney(item.amount, item.currency, language)}</small></td>
                  <td><strong>{item.current_node_name || "—"}</strong><small>{workflowStatusLabel(item.workflow_status, language)}</small></td>
                  <td>{item.current_approver_name || "—"}</td>
                  <td><strong>{item.age_days} {t("天", "días")}</strong><small>{compactDateTime(item.current_node_entered_at, language)}</small></td>
                  <td className="mexico-row-actions"><button onClick={() => openDetail(item.id)}>{t("查看", "Ver")}</button>{item.reminder && <button title={t("复制双语提醒", "Copiar recordatorio bilingüe")} onClick={() => copyReminder(item)}><ClipboardCopy size={15} /></button>}</td>
                </tr>
              ))}</tbody>
            </table>
            <div className="mexico-card-list">{items.map((item) => (
              <article key={item.id} className={`mexico-tracking-card warning-${item.warning_level}`} onClick={() => openDetail(item.id)}>
                <header><span className={`mexico-warning-chip ${item.warning_level}`}>{warningLabel(item.warning_level, language)}</span><strong>{item.age_days} {t("天", "días")}</strong></header>
                <h3>{item.summary || item.approval_no}</h3>
                <p>{item.company_name || item.source_sheet || "—"}</p>
                <dl><div><dt>{t("申请人", "Solicitante")}</dt><dd>{item.applicant_name || "—"}</dd></div><div><dt>{t("当前节点", "Etapa")}</dt><dd>{item.current_node_name || "—"}</dd></div><div><dt>{t("当前审批人", "Aprobador")}</dt><dd>{item.current_approver_name || "—"}</dd></div><div><dt>{t("金额", "Importe")}</dt><dd>{formatOriginalMoney(item.amount, item.currency, language)}</dd></div></dl>
                <footer><span>{item.approval_no}</span><div>{item.reminder && <button onClick={(event) => { event.stopPropagation(); copyReminder(item); }}><ClipboardCopy size={15} />{t("提醒", "Recordar")}</button>}<button>{t("查看", "Ver")}</button></div></footer>
              </article>
            ))}</div>
          </>
        )}
      </div>

      {pages > 1 && <div className="mexico-pagination"><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><ChevronLeft /></button><span>{page} / {pages}</span><button disabled={page >= pages} onClick={() => setPage((value) => value + 1)}><ChevronRight /></button></div>}

      {selectedId !== null && (
        <MexicoDetailDrawer
          item={detail}
          loading={detailLoading}
          canAdmin={canAdmin}
          language={language}
          onClose={() => { setSelectedId(null); setDetail(null); }}
          onCopy={() => detail && copyReminder(detail)}
          onResolved={async (resolved) => {
            setDetail((current) => current ? { ...current, ...resolved } : current);
            await refreshAll();
          }}
          setMessage={setMessage}
        />
      )}

      {settingsOpen && settings && (
        <MexicoSettingsDialog settings={settings} onClose={() => setSettingsOpen(false)} onSaved={(saved) => { setSettings(saved); setSettingsOpen(false); refreshAll(); setMessage(t("预警设置已保存", "Configuración guardada")); }} />
      )}
    </section>
  );
}

function MexicoDetailDrawer({
  item,
  loading,
  canAdmin,
  language,
  onClose,
  onCopy,
  onResolved,
  setMessage,
}: {
  item: MexicoTrackingDetail | null;
  loading: boolean;
  canAdmin: boolean;
  language: "zh" | "es";
  onClose: () => void;
  onCopy: () => void;
  onResolved: (item: MexicoTrackingItem) => Promise<void>;
  setMessage: (message: string) => void;
}) {
  const { t } = useLanguage();
  const [resolving, setResolving] = useState(false);

  async function resolveRegion(region: "china" | "mexico") {
    if (!item) return;
    setResolving(true);
    try {
      const response = await api.resolveMexicoTrackingRegion(item.id, region, item.version);
      await onResolved(response.item);
      setMessage(t("执行地区已确认", "Región confirmada"));
    } catch (reason) {
      setMessage((reason as Error).message);
    } finally {
      setResolving(false);
    }
  }

  return (
    <div className="mexico-detail-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <aside className="mexico-detail-drawer" role="dialog" aria-modal="true" aria-label={t("墨西哥审批详情", "Detalle de aprobación de México")}>
        <header className="mexico-detail-header"><div><small>{t("墨西哥审批跟进", "Seguimiento de México")}</small><h2>{item?.summary || item?.approval_no || t("加载中", "Cargando")}</h2></div><button onClick={onClose} aria-label={t("关闭", "Cerrar")}><X /></button></header>
        {loading && <div className="mexico-detail-loading"><LoaderCircle className="spin" />{t("正在加载审批时间线", "Cargando la línea de tiempo")}</div>}
        {item && (
          <div className="mexico-detail-content">
            <section className="mexico-detail-overview">
              <div><span>{t("钉钉单号", "N.º DingTalk")}</span><strong>{item.approval_no}</strong></div><div><span>{t("状态", "Estado")}</span><strong>{workflowStatusLabel(item.workflow_status, language)}</strong></div><div><span>{t("当前节点", "Etapa actual")}</span><strong>{item.current_node_name || "—"}</strong></div><div><span>{t("当前审批人", "Aprobador actual")}</span><strong>{item.current_approver_name || "—"}</strong></div><div><span>{t("停留时长", "Tiempo en etapa")}</span><strong>{item.age_days} {t("天", "días")}</strong></div><div><span>{t("金额", "Importe")}</span><strong>{formatOriginalMoney(item.amount, item.currency, language)}</strong></div>
            </section>
            {item.reminder && <section className="mexico-reminder-box"><div><strong>{t("双语催办提醒", "Recordatorio bilingüe")}</strong><span>{t("复制后可直接发给流程负责人", "Listo para enviar al responsable")}</span></div><button onClick={onCopy}><ClipboardCopy size={16} />{t("复制双语提醒", "Copiar recordatorio bilingüe")}</button></section>}
            {canAdmin && item.region_review_status === "pending" && <section className="mexico-region-review"><AlertTriangle /><div><strong>{t("执行地区待核对", "Región por revisar")}</strong><p>{item.region_conflict_reason || t("来源信息存在冲突，请人工确认。", "La información de origen es inconsistente.")}</p></div><div><button disabled={resolving} onClick={() => resolveRegion("china")}>{t("归为中国", "Asignar a China")}</button><button className="primary" disabled={resolving} onClick={() => resolveRegion("mexico")}>{t("归为墨西哥", "Asignar a México")}</button></div></section>}
            <section className="mexico-detail-meta">
              <h3>{t("申请信息", "Información de solicitud")}</h3>
              <dl><div><dt>{t("子公司", "Empresa")}</dt><dd>{item.company_name || item.source_sheet || "—"}</dd></div><div><dt>{t("申请人", "Solicitante")}</dt><dd>{item.applicant_name || "—"}</dd></div><div><dt>{t("部门", "Departamento")}</dt><dd>{item.applicant_department || "—"}</dd></div><div><dt>{t("来源", "Origen")}</dt><dd>{mexicoSourceLabel(item.source_type, language)}</dd></div><div><dt>{t("申请日期", "Fecha de solicitud")}</dt><dd>{item.request_date || "—"}</dd></div><div><dt>{t("最近同步", "Última sincronización")}</dt><dd>{compactDateTime(item.last_synced_at, language)}</dd></div></dl>
              {item.workflow_url && <a className="mexico-workflow-link" href={item.workflow_url} target="_blank" rel="noreferrer"><ExternalLink size={15} />{t("在钉钉中查看流程", "Ver flujo en DingTalk")}</a>}
            </section>
            <section className="mexico-timeline-section"><h3>{t("审批时间线", "Línea de aprobación")} <span>{item.events.length}</span></h3>{!item.events.length ? <div className="mexico-inline-empty">{t("尚未同步到流程节点", "Todavía no hay etapas sincronizadas")}</div> : <ol className="mexico-timeline">{item.events.map((event) => <li key={event.event_key || event.id} className={event.is_current ? "current" : ""}><i /><div className="mexico-timeline-card"><header><div><strong>{event.node_name || event.event_type || t("流程事件", "Evento")}</strong>{event.is_current && <span>{t("当前节点", "Actual")}</span>}</div><time>{compactDateTime(event.event_time, language)}</time></header><p className="mexico-event-operator"><UserRound size={14} />{event.operator_name || "—"}{event.result && ` · ${event.result}`}</p>{event.remark && <blockquote>{event.remark}</blockquote>}{((event.images?.length || 0) + (event.attachments?.length || 0)) > 0 && <small><Paperclip size={13} />{t("流程包含附件", "El evento contiene archivos")} {event.images?.length || 0} + {event.attachments?.length || 0}</small>}</div></li>)}</ol>}</section>
            <section className="mexico-attachments-section"><h3>{t("流程附件", "Archivos del flujo")} <span>{item.attachments.length}</span></h3>{!item.attachments.length ? <div className="mexico-inline-empty">{t("暂无已同步附件", "No hay archivos sincronizados")}</div> : <div className="mexico-attachment-list">{item.attachments.map((attachment) => attachment.content_url ? <a key={attachment.id} href={attachment.content_url} target="_blank" rel="noreferrer"><FileText /><span><strong>{attachment.file_name}</strong><small>{attachment.mime_type || attachment.status}</small></span><ExternalLink /></a> : <div key={attachment.id} className="disabled"><FileText /><span><strong>{attachment.file_name}</strong><small>{attachment.last_error || attachment.status}</small></span></div>)}</div>}</section>
            {item.linked_requests.length > 0 && <section className="mexico-linked-section"><h3>{t("关联请款", "Solicitudes relacionadas")} <span>{item.linked_requests.length}</span></h3>{item.linked_requests.map((request) => <div key={request.id}><span>{request.batch_name}</span><strong>{request.summary || request.dingding_id}</strong><small>{formatOriginalMoney(request.amount, request.currency, language)}</small></div>)}</section>}
          </div>
        )}
      </aside>
    </div>
  );
}

function MexicoSettingsDialog({ settings, onClose, onSaved }: { settings: MexicoTrackingSettings; onClose: () => void; onSaved: (settings: MexicoTrackingSettings) => void }) {
  const { t } = useLanguage();
  const [form, setForm] = useState(settings);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setSaving(true);
    setError("");
    try {
      const response = await api.updateMexicoTrackingSettings(form);
      onSaved(response.settings);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && !saving && onClose()}>
      <div className="modal-card mexico-settings-dialog" role="dialog" aria-modal="true">
        <header><div><h2>{t("墨西哥审批预警设置", "Alertas de aprobación de México")}</h2><p>{t("根据当前节点停留天数分级提醒。", "Clasifica alertas según los días en la etapa actual.")}</p></div><button disabled={saving} onClick={onClose}><X /></button></header>
        <div className="mexico-settings-form"><label>{t("黄色提醒（天）", "Alerta amarilla (días)")}<input type="number" min={1} value={form.yellow_days} onChange={(event) => setForm((current) => ({ ...current, yellow_days: Number(event.target.value) }))} /></label><label>{t("红色提醒（天）", "Alerta roja (días)")}<input type="number" min={2} value={form.red_days} onChange={(event) => setForm((current) => ({ ...current, red_days: Number(event.target.value) }))} /></label><label>{t("自动同步缓存（秒）", "Caché automático (segundos)")}<input type="number" min={0} value={form.cache_stale_seconds} onChange={(event) => setForm((current) => ({ ...current, cache_stale_seconds: Number(event.target.value) }))} /></label></div>
        {error && <p className="mexico-error">{error}</p>}
        <footer><button disabled={saving} onClick={onClose}>{t("取消", "Cancelar")}</button><button className="primary" disabled={saving || form.yellow_days >= form.red_days} onClick={save}>{saving ? t("保存中", "Guardando") : t("保存", "Guardar")}</button></footer>
      </div>
    </div>
  );
}
