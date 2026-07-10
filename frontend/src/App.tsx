import { ClipboardEvent, FormEvent, KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  AlignLeft,
  Archive,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FileSpreadsheet,
  Filter,
  History,
  Image as ImageIcon,
  LogOut,
  Paperclip,
  Plus,
  RefreshCcw,
  Save,
  Search,
  Shield,
  Trash2,
  Upload,
  Undo2,
  Users,
} from "lucide-react";
import { api, AttachmentLink, AuditLog, Batch, PaymentRequest, RolloverCopyMode, User, UserRole } from "./api";

type Tab = "workspace" | "archive" | "admin";

const emptyRequest: Partial<PaymentRequest> = {
  payment_account: "私户",
  invoice_status: "无票",
  finance_review: "未付款",
  currency: "CNY",
};

const financeApprovalOptions = ["未付款", "部分付款", "已付款"];
const generalManagerApprovalOptions = ["同意付款", "延缓批付", "存在争议"];
const GENERAL_MANAGER_EMPTY_FILTER = "__empty_general_manager_approval__";
const selectOptionsByField: Partial<Record<keyof PaymentRequest, string[]>> = {
  finance_review: financeApprovalOptions,
  general_manager_approval: generalManagerApprovalOptions,
};
const strictSelectFields = new Set<keyof PaymentRequest>(["finance_review", "general_manager_approval"]);

const roleLabels: Record<UserRole, string> = {
  business: "业务人员",
  finance: "财务",
  general_manager: "总经理",
  admin: "管理员",
};

const financeControlledFields = new Set<keyof PaymentRequest>([
  "finance_review",
  "finance_manager_approval",
  "actual_payment_date",
  "overdue_status",
  "payer",
]);
const generalManagerControlledFields = new Set<keyof PaymentRequest>([
  "general_manager_approval",
  "general_manager_approval_date",
  "general_manager_opinion",
]);

function isPrivilegedRole(role: UserRole) {
  return role === "admin" || role === "general_manager";
}

function canEditRequestField(role: UserRole, field: keyof PaymentRequest) {
  if (isPrivilegedRole(role)) return true;
  if (role === "finance") return !generalManagerControlledFields.has(field);
  return !financeControlledFields.has(field) && !generalManagerControlledFields.has(field);
}

const fieldLabels: Record<string, string> = {
  dingding_id: "钉钉申请单号",
  payment_account: "付款账户",
  expense_type: "费用性质",
  summary: "摘要",
  style_name: "款式",
  amount: "应付金额",
  project: "项目归属",
  bu: "BU归属",
  payee_account: "收款账户/账号",
  payee_name: "账户名",
  bank_name: "开户行",
  invoice_status: "开票情况",
  needed_payment_date: "需求付款日期",
  owner_confirmation: "负责人确认",
  finance_review: "财务审批",
  finance_manager_approval: "财务主管审批",
  general_manager_approval: "总经理审批",
  general_manager_approval_date: "总经理审批时间",
  general_manager_opinion: "总经理意见",
  actual_payment_date: "财务付款时间",
  remark: "备注",
  overdue_status: "逾期情况",
  payer: "付款人",
  source_sheet: "来源 Sheet",
};

type GridRow = Partial<PaymentRequest> & {
  __localId: string;
  __isNew?: boolean;
  __deleted?: boolean;
};

type GridColumn = {
  key: keyof PaymentRequest;
  label: string;
  width: number;
  type?: "number" | "date";
};

const gridColumns: GridColumn[] = [
  { key: "dingding_id", label: "钉钉申请单号", width: 190 },
  { key: "payment_account", label: "付款账户", width: 110 },
  { key: "expense_type", label: "费用性质", width: 120 },
  { key: "summary", label: "摘要", width: 360 },
  { key: "amount", label: "应付金额", width: 120, type: "number" },
  { key: "project", label: "项目归属", width: 160 },
  { key: "payee_account", label: "收款信息/账号", width: 210 },
  { key: "payee_name", label: "账户名", width: 140 },
  { key: "bank_name", label: "开户行", width: 180 },
  { key: "invoice_status", label: "开票情况", width: 110 },
  { key: "needed_payment_date", label: "需求付款日期", width: 140, type: "date" },
  { key: "finance_review", label: "财务审批", width: 130 },
  { key: "actual_payment_date", label: "财务付款时间", width: 140, type: "date" },
  { key: "general_manager_approval", label: "总经理审批", width: 130 },
  { key: "general_manager_approval_date", label: "总经理审批时间", width: 150, type: "date" },
  { key: "general_manager_opinion", label: "总经理意见", width: 260 },
  { key: "remark", label: "备注", width: 220 },
  { key: "source_sheet", label: "来源 Sheet", width: 150 },
];

const ALL_SHEET = "__all__";
type SheetTab = {
  key: string;
  label: string;
  count: number;
  deletedCount?: number;
  pendingDelete?: boolean;
};
const wrappableColumnKeys = new Set<keyof PaymentRequest>([
  "payment_account",
  "expense_type",
  "summary",
  "project",
  "payee_account",
  "payee_name",
  "bank_name",
  "invoice_status",
  "general_manager_opinion",
  "remark",
  "source_sheet",
]);

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api
      .me()
      .then((res) => setUser(res.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="center-screen">加载中</div>;
  if (!user) return <Login onLogin={setUser} />;

  return <Shell user={user} message={message} setMessage={setMessage} onLogout={() => setUser(null)} />;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("admin123");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const res = await api.login(username, password);
      onLogin(res.user);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit}>
        <div className="brand-row">
          <FileSpreadsheet size={28} />
          <div>
            <h1>出纳请款明细</h1>
            <span>内网归档工作台</span>
          </div>
        </div>
        <label>
          账号
          <input value={username} onChange={(event) => setUsername(event.target.value)} />
        </label>
        <label>
          密码
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button className="primary-button" type="submit">
          <Shield size={16} />
          登录
        </button>
      </form>
    </main>
  );
}

function Shell({
  user,
  message,
  setMessage,
  onLogout,
}: {
  user: User;
  message: string;
  setMessage: (message: string) => void;
  onLogout: () => void;
}) {
  const [tab, setTab] = useState<Tab>("workspace");
  const [batches, setBatches] = useState<Batch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [workspaceRefreshToken, setWorkspaceRefreshToken] = useState(0);

  const selectedBatch = batches.find((batch) => batch.id === selectedBatchId) || batches[0] || null;

  async function loadBatches() {
    const res = await api.batches();
    setBatches(res.batches);
    if (!res.batches.length) {
      setSelectedBatchId(null);
    } else if (!selectedBatchId || !res.batches.some((batch) => batch.id === selectedBatchId)) {
      setSelectedBatchId(res.batches[0].id);
    }
  }

  useEffect(() => {
    loadBatches().catch((err) => setMessage((err as Error).message));
  }, []);

  async function logout() {
    await api.logout();
    onLogout();
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <FileSpreadsheet />
          <strong>请款明细</strong>
        </div>
        <nav className="app-nav">
          <button className={tab === "workspace" ? "active" : ""} onPointerDown={() => setTab("workspace")} onClick={() => setTab("workspace")} onKeyDown={(event) => activateButtonByKeyboard(event, () => setTab("workspace"))}>
            <FileSpreadsheet size={16} />
            工作台
          </button>
          <button className={tab === "archive" ? "active" : ""} onPointerDown={() => setTab("archive")} onClick={() => setTab("archive")} onKeyDown={(event) => activateButtonByKeyboard(event, () => setTab("archive"))}>
            <Archive size={16} />
            归档
          </button>
          {isPrivilegedRole(user.role) && (
            <button className={tab === "admin" ? "active" : ""} onPointerDown={() => setTab("admin")} onClick={() => setTab("admin")} onKeyDown={(event) => activateButtonByKeyboard(event, () => setTab("admin"))}>
              <Users size={16} />
              管理
            </button>
          )}
        </nav>
        <div className="app-userbar">
          <div className="app-user">
            <span>{user.display_name}</span>
            <small>{roleLabels[user.role]}</small>
          </div>
          {message && <span className="toast">{message}</span>}
          <button className="icon-text" onClick={logout}>
            <LogOut size={15} />
            退出
          </button>
        </div>
      </header>
      <main className="main-pane">
        <header className="topbar">
          <div>
            <h1>{tabTitle(tab)}</h1>
            {selectedBatch && <span>{selectedBatch.name}</span>}
          </div>
          {tab === "workspace" && (
            <TopbarImportActions
              selectedBatch={selectedBatch}
              reloadBatches={loadBatches}
              onImported={() => setWorkspaceRefreshToken((value) => value + 1)}
              setMessage={setMessage}
            />
          )}
        </header>
        {tab === "workspace" && (
          <Workspace
            user={user}
            batches={batches}
            selectedBatch={selectedBatch}
            setSelectedBatchId={setSelectedBatchId}
            reloadBatches={loadBatches}
            refreshToken={workspaceRefreshToken}
            setMessage={setMessage}
          />
        )}
        {tab === "archive" && <ArchiveView batches={batches} selectedBatch={selectedBatch} setSelectedBatchId={setSelectedBatchId} reloadBatches={loadBatches} setMessage={setMessage} />}
        {tab === "admin" && <AdminView setMessage={setMessage} />}
      </main>
    </div>
  );
}

function tabTitle(tab: Tab) {
  return {
    workspace: "当前周工作台",
    archive: "历史归档",
    admin: "用户管理",
  }[tab];
}

function TopbarImportActions({
  selectedBatch,
  reloadBatches,
  onImported,
  setMessage,
}: {
  selectedBatch: Batch | null;
  reloadBatches: () => Promise<void>;
  onImported: () => void;
  setMessage: (message: string) => void;
}) {
  const [weeklyFile, setWeeklyFile] = useState<File | null>(null);
  const [dingtalkFile, setDingtalkFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string> | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mappingOpen, setMappingOpen] = useState(false);
  const [busyAction, setBusyAction] = useState<"weekly" | "dingtalk" | "rollback" | null>(null);
  const [weeklyInputKey, setWeeklyInputKey] = useState(0);
  const [dingtalkInputKey, setDingtalkInputKey] = useState(0);

  async function refreshAfterImport(message: string) {
    await reloadBatches();
    onImported();
    setMessage(message);
  }

  async function uploadWeekly() {
    if (!weeklyFile) return;
    setBusyAction("weekly");
    try {
      await api.uploadWeekly(weeklyFile, selectedBatch?.id);
      setWeeklyFile(null);
      setWeeklyInputKey((value) => value + 1);
      await refreshAfterImport("周报 Excel 已导入");
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  }

  async function uploadDingTalk(withMapping: boolean) {
    if (!dingtalkFile) return;
    setBusyAction("dingtalk");
    try {
      const res = await api.uploadDingTalk(dingtalkFile, selectedBatch?.id, withMapping ? mapping || undefined : undefined);
      if (res.status === "needs_mapping") {
        setHeaders(res.headers as string[]);
        setMapping(res.suggested_mapping as Record<string, string>);
        setMappingOpen(true);
        setMessage("请确认钉钉字段映射");
        return;
      }
      setDingtalkFile(null);
      setDingtalkInputKey((value) => value + 1);
      setMapping(null);
      setHeaders([]);
      setMappingOpen(false);
      await refreshAfterImport("钉钉导出表已导入");
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  }

  async function rollbackLatestImport() {
    if (!selectedBatch || busyAction !== null) return;
    const confirmed = window.confirm("只撤回当前批次最近一次普通导入的数据，原有记录会保留。确认撤回吗？");
    if (!confirmed) return;
    setBusyAction("rollback");
    try {
      const res = await api.rollbackLatestImport(selectedBatch.id);
      await reloadBatches();
      onImported();
      setMessage(`已撤回最近导入：删除 ${res.deleted_requests} 条记录、${res.deleted_attachments} 个附件`);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <>
      <div className="topbar-import" aria-label="导入">
        <div className="topbar-import-group">
          <label className="compact-file-button">
            <FileSpreadsheet size={15} />
            周报 Excel
            <input
              key={weeklyInputKey}
              type="file"
              accept=".xlsx,.xls"
              onChange={(event) => setWeeklyFile(event.target.files?.[0] || null)}
            />
          </label>
          <span className="compact-file-name" title={weeklyFile?.name || ""}>{weeklyFile?.name || "未选择"}</span>
          <button className="primary-button compact-import-button" type="button" onClick={uploadWeekly} disabled={!weeklyFile || busyAction !== null}>
            <Upload size={15} />
            {busyAction === "weekly" ? "导入中" : "导入"}
          </button>
        </div>
        <div className="topbar-import-group">
          <label className="compact-file-button">
            <FileSpreadsheet size={15} />
            钉钉导出表
            <input
              key={dingtalkInputKey}
              type="file"
              accept=".xlsx,.xls,.csv"
              onChange={(event) => {
                setDingtalkFile(event.target.files?.[0] || null);
                setMapping(null);
                setHeaders([]);
              }}
            />
          </label>
          <span className="compact-file-name" title={dingtalkFile?.name || ""}>{dingtalkFile?.name || "未选择"}</span>
          <button className="primary-button compact-import-button" type="button" onClick={() => uploadDingTalk(false)} disabled={!dingtalkFile || busyAction !== null}>
            <Upload size={15} />
            {busyAction === "dingtalk" ? "处理中" : "识别"}
          </button>
        </div>
        <div className="topbar-import-group rollback-import-group">
          <button className="ghost-button danger-button compact-import-button" type="button" onClick={rollbackLatestImport} disabled={!selectedBatch || busyAction !== null}>
            <Undo2 size={15} />
            {busyAction === "rollback" ? "撤回中" : "撤回最近导入"}
          </button>
        </div>
      </div>
      {mappingOpen && mapping && (
        <Modal title="钉钉字段映射" onClose={() => setMappingOpen(false)}>
          <div className="mapping-table compact-mapping-table">
            {Object.keys(fieldLabels).map((field) => (
              <label key={field}>
                {fieldLabels[field]}
                <select value={mapping[field] || ""} onChange={(event) => setMapping({ ...mapping, [field]: event.target.value })}>
                  <option value="">不导入</option>
                  {headers.map((header) => (
                    <option key={header} value={header}>{header}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <div className="drawer-actions">
            <button className="primary-button" type="button" onClick={() => uploadDingTalk(true)} disabled={busyAction !== null}>
              <Upload size={16} />
              {busyAction === "dingtalk" ? "导入中" : "按映射导入"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function Workspace({
  user,
  batches,
  selectedBatch,
  setSelectedBatchId,
  reloadBatches,
  refreshToken,
  setMessage,
}: {
  user: User;
  batches: Batch[];
  selectedBatch: Batch | null;
  setSelectedBatchId: (id: number) => void;
  reloadBatches: () => Promise<void>;
  refreshToken: number;
  setMessage: (message: string) => void;
}) {
  const [gridRows, setGridRows] = useState<GridRow[]>([]);
  const [dirtyCells, setDirtyCells] = useState<Set<string>>(new Set());
  const [deletedLocalIds, setDeletedLocalIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState({ q: "", payment_account: "", invoice_status: "", finance_review: "", general_manager_approval: "" });
  const [activeSheet, setActiveSheet] = useState(ALL_SHEET);
  const [editingSheet, setEditingSheet] = useState<{ key: string; value: string } | null>(null);
  const [newSheetName, setNewSheetName] = useState<string | null>(null);
  const [wrapText, setWrapText] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [rolloverDialogOpen, setRolloverDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Partial<PaymentRequest> | null>(null);
  const [editorDraft, setEditorDraft] = useState<Partial<PaymentRequest> | null>(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [attachmentCounts, setAttachmentCounts] = useState<Record<number, number>>({});
  const [attachmentsByRequest, setAttachmentsByRequest] = useState<Record<number, AttachmentLink[]>>({});
  const [previewImages, setPreviewImages] = useState<{ images: AttachmentLink[]; index: number } | null>(null);
  const [selectedRows, setSelectedRows] = useState<number[]>([]);
  const [bulkStatus, setBulkStatus] = useState("已付款");
  const [reason, setReason] = useState("");
  const hasUnsavedChanges = dirtyCells.size > 0 || deletedLocalIds.size > 0;

  async function loadRequests() {
    if (!selectedBatch) return;
    const [requestsRes, attachmentsRes] = await Promise.all([api.requests(selectedBatch.id, {}), api.batchAttachments(selectedBatch.id)]);
    const attachmentGroups = groupAttachmentsByRequest(attachmentsRes.attachments);
    setGridRows(toGridRows(requestsRes.requests));
    setAttachmentsByRequest(attachmentGroups);
    setAttachmentCounts(countAttachmentsByGroup(attachmentGroups));
    setDirtyCells(new Set());
    setDeletedLocalIds(new Set());
    setSelectedRows([]);
    setPreviewImages(null);
  }

  async function refreshAttachmentCounts() {
    if (!selectedBatch) {
      setAttachmentCounts({});
      setAttachmentsByRequest({});
      return;
    }
    const res = await api.batchAttachments(selectedBatch.id);
    const attachmentGroups = groupAttachmentsByRequest(res.attachments);
    setAttachmentsByRequest(attachmentGroups);
    setAttachmentCounts(countAttachmentsByGroup(attachmentGroups));
  }

  useEffect(() => {
    loadRequests().catch((err) => setMessage((err as Error).message));
  }, [selectedBatch?.id, refreshToken]);

  const sheetTabs = useMemo(() => getSheetTabs(gridRows), [gridRows]);
  const visibleRows = useMemo(() => gridRows.filter((row) => rowMatchesFilters(row, filters, activeSheet)), [gridRows, filters, activeSheet]);
  const visibleActiveRows = useMemo(() => visibleRows.filter((row) => !row.__deleted), [visibleRows]);
  const activeSheetRows = useMemo(
    () => (activeSheet === ALL_SHEET ? [] : gridRows.filter((row) => normalizeSheetName(row.source_sheet) === activeSheet)),
    [activeSheet, gridRows],
  );
  const visibleTotals = useMemo(
    () => ({
      count: visibleActiveRows.length,
      amount: visibleActiveRows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0),
    }),
    [visibleActiveRows],
  );
  const financeReviewCounts = useMemo(
    () => ({
      paid: visibleActiveRows.filter((row) => row.finance_review === "已付款").length,
      partial: visibleActiveRows.filter((row) => row.finance_review === "部分付款").length,
      unpaid: visibleActiveRows.filter((row) => row.finance_review === "未付款").length,
    }),
    [visibleActiveRows],
  );
  const defaultSourceSheet = activeSheet === ALL_SHEET ? "手工录入" : activeSheet;
  const canArchiveBatch = ["finance", "general_manager", "admin"].includes(user.role);
  const canRestoreBatch = isPrivilegedRole(user.role);
  const canEditGrid = selectedBatch?.status !== "archived" || isPrivilegedRole(user.role);
  const canBulkUpdatePayment = canEditGrid && canEditRequestField(user.role, "finance_review");
  const activeSheetPendingDeleteCount = activeSheetRows.filter((row) => row.__deleted).length;
  const canDeleteActiveSheet = canEditGrid && activeSheet !== ALL_SHEET && activeSheetRows.some((row) => !row.__deleted);
  const canRestoreActiveSheetDelete = canEditGrid && activeSheet !== ALL_SHEET && activeSheetRows.some((row) => row.__deleted);

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!hasUnsavedChanges) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasUnsavedChanges]);

  useEffect(() => {
    if (activeSheet === ALL_SHEET) return;
    if (!sheetTabs.some((tab) => tab.key === activeSheet)) setActiveSheet(ALL_SHEET);
  }, [activeSheet, sheetTabs]);

  function guardedSelectBatch(id: number) {
    if (hasUnsavedChanges && !window.confirm("当前表格有未保存更改，切换批次会丢失这些更改。继续切换吗？")) return;
    setSelectedBatchId(id);
  }

  async function saveRequest(payload: Partial<PaymentRequest>, options: { closeEditor?: boolean } = {}) {
    if (!selectedBatch) return;
    if (payload.id) {
      await api.updateRequest(selectedBatch.id, payload.id, { ...payload, reason });
    } else {
      await api.createRequest(selectedBatch.id, payload);
    }
    if (options.closeEditor ?? true) setEditing(null);
    setEditorDraft(null);
    setEditorDirty(false);
    setReason("");
    await loadRequests();
    await reloadBatches();
    setMessage("已保存");
  }

  async function openRequestEditor(request: Partial<PaymentRequest>) {
    if (editing && editorDirty) {
      const shouldSave = window.confirm("当前抽屉有未保存改动，是否先保存再切换到这条请款？");
      if (!shouldSave) return;
      try {
        await saveRequest(editorDraft || editing, { closeEditor: false });
      } catch (err) {
        setMessage((err as Error).message);
        return;
      }
    }
    setEditing(request);
    setEditorDraft(null);
    setEditorDirty(false);
    setReason("");
  }

  function openAttachmentsFromGrid(request: PaymentRequest) {
    const attachments = attachmentsByRequest[request.id] || [];
    if (attachments.length > 0 && attachments.every(isImageAttachment)) {
      setPreviewImages({ images: attachments, index: 0 });
      return;
    }
    openRequestEditor(request);
  }

  async function bulkUpdate() {
    if (!selectedBatch || selectedRows.length === 0 || !canBulkUpdatePayment) return;
    await Promise.all(selectedRows.map((id) => api.updateRequest(selectedBatch.id, id, { finance_review: bulkStatus, reason })));
    setSelectedRows([]);
    setReason("");
    await loadRequests();
    setMessage("批量更新完成");
  }

  async function saveGridChanges() {
    if (!selectedBatch || !hasUnsavedChanges) return;
    const creates = gridRows
      .filter((row) => row.__isNew && !row.__deleted && rowHasContent(row))
      .map(stripGridRow);
    const updates = gridRows
      .filter((row) => row.id && dirtyRowIds(dirtyCells).has(row.__localId) && !row.__deleted)
      .map((row) => ({ id: row.id!, ...stripGridRow(row) }));
    const deletes = Array.from(deletedLocalIds)
      .map((localId) => gridRows.find((row) => row.__localId === localId)?.id)
      .filter((id): id is number => Boolean(id));
    await api.bulkSaveRequests(selectedBatch.id, { creates, updates, deletes, reason });
    setReason("");
    await loadRequests();
    await reloadBatches();
    setMessage("表格更改已保存");
  }

  async function archiveCurrentBatch() {
    if (!selectedBatch) return;
    if (hasUnsavedChanges) {
      setMessage("请先保存表格更改，再修改批次状态");
      return;
    }
    await api.archive(selectedBatch.id);
    await reloadBatches();
    setMessage("批次已归档");
  }

  async function restoreCurrentBatchDraft() {
    if (!selectedBatch) return;
    if (hasUnsavedChanges) {
      setMessage("请先保存表格更改，再修改批次状态");
      return;
    }
    await api.unarchive(selectedBatch.id);
    await reloadBatches();
    setMessage("批次已恢复为草稿");
  }

  function mergeVisibleRows(nextVisibleRows: GridRow[]) {
    setGridRows((previousRows) => {
      const nextByLocalId = new Map(nextVisibleRows.map((row) => [row.__localId, row]));
      const seen = new Set<string>();
      const mergedRows = previousRows.map((row) => {
        const next = nextByLocalId.get(row.__localId);
        if (!next) return row;
        seen.add(row.__localId);
        return next;
      });
      const appendedRows = nextVisibleRows.filter((row) => !seen.has(row.__localId) && !previousRows.some((previous) => previous.__localId === row.__localId));
      return [...mergedRows, ...appendedRows];
    });
  }

  function addBlankRow() {
    const row = { ...emptyRequest, __localId: newLocalId(), __isNew: true, source_sheet: defaultSourceSheet };
    setGridRows([...gridRows, row]);
    const nextDirty = new Set(dirtyCells);
    nextDirty.add(`${row.__localId}:source_sheet`);
    setDirtyCells(nextDirty);
  }

  function beginCreateSheet() {
    if (!canEditGrid) return;
    setEditingSheet(null);
    setNewSheetName(nextSheetName(sheetTabs));
  }

  function commitCreateSheet() {
    if (newSheetName === null) return;
    const name = newSheetName.trim();
    if (!name) {
      setNewSheetName(null);
      setMessage("Sheet 名称不能为空，已取消新增");
      return;
    }
    if (name === "全部") {
      setNewSheetName(null);
      setMessage("Sheet 名称不能叫“全部”，已取消新增");
      return;
    }
    if (sheetTabs.some((tab) => tab.key !== ALL_SHEET && tab.key === name)) {
      setNewSheetName(null);
      setMessage("Sheet 名称已存在，已取消新增");
      return;
    }
    const row = { ...emptyRequest, __localId: newLocalId(), __isNew: true, source_sheet: name };
    setGridRows([...gridRows, row]);
    const nextDirty = new Set(dirtyCells);
    nextDirty.add(`${row.__localId}:source_sheet`);
    setDirtyCells(nextDirty);
    setFilters({ q: "", payment_account: "", invoice_status: "", finance_review: "", general_manager_approval: "" });
    setActiveSheet(name);
    setNewSheetName(null);
    setMessage(`已新增 Sheet：${name}，请保存更改`);
  }

  function handleCreateSheetKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    event.stopPropagation();
    if (event.key === "Enter") {
      event.preventDefault();
      commitCreateSheet();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setNewSheetName(null);
    }
  }

  function beginRenameSheet(sheetKey: string) {
    if (sheetKey === ALL_SHEET) return;
    const rowsInSheet = gridRows.filter((row) => normalizeSheetName(row.source_sheet) === sheetKey);
    if (rowsInSheet.length > 0 && rowsInSheet.every((row) => row.__deleted)) return;
    setNewSheetName(null);
    setActiveSheet(sheetKey);
    setEditingSheet({ key: sheetKey, value: sheetKey });
  }

  function commitRenameSheet() {
    if (!editingSheet) return;
    const oldName = editingSheet.key;
    const newName = editingSheet.value.trim();
    if (!newName || newName === oldName) {
      setEditingSheet(null);
      return;
    }
    if (sheetTabs.some((tab) => tab.key !== ALL_SHEET && tab.key !== oldName && tab.key === newName)) {
      setMessage("Sheet 名称已存在，已取消重命名");
      setEditingSheet(null);
      return;
    }
    const affectedRows = gridRows.filter((row) => normalizeSheetName(row.source_sheet) === oldName);
    if (affectedRows.length === 0) {
      setEditingSheet(null);
      return;
    }
    const nextDirty = new Set(dirtyCells);
    affectedRows.forEach((row) => nextDirty.add(`${row.__localId}:source_sheet`));
    setGridRows(gridRows.map((row) => (normalizeSheetName(row.source_sheet) === oldName ? { ...row, source_sheet: newName } : row)));
    setDirtyCells(nextDirty);
    setActiveSheet(newName);
    setEditingSheet(null);
    setMessage(`Sheet 已重命名为 ${newName}，请保存更改`);
  }

  function handleSheetRenameKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    event.stopPropagation();
    if (event.key === "Enter") {
      event.preventDefault();
      commitRenameSheet();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setEditingSheet(null);
    }
  }

  function markDeleteSelected() {
    const selected = new Set(selectedRows);
    const nextDeleted = new Set(deletedLocalIds);
    const nextRows = gridRows.map((row) => {
      if (row.id && selected.has(row.id)) {
        nextDeleted.add(row.__localId);
        return { ...row, __deleted: true };
      }
      return row;
    });
    setGridRows(nextRows);
    setDeletedLocalIds(nextDeleted);
    setSelectedRows([]);
  }

  function markDeleteActiveSheet() {
    if (!canDeleteActiveSheet) return;
    const targetRows = activeSheetRows.filter((row) => !row.__deleted);
    if (!targetRows.length) return;
    const confirmed = window.confirm(`确定删除 Sheet“${activeSheet}”吗？\n\n将把 ${targetRows.length} 行标记为待删除，点击“保存更改”后才会真正删除。`);
    if (!confirmed) return;
    const targetLocalIds = new Set(targetRows.map((row) => row.__localId));
    const nextDeleted = new Set(deletedLocalIds);
    targetLocalIds.forEach((localId) => nextDeleted.add(localId));
    setGridRows(gridRows.map((row) => (targetLocalIds.has(row.__localId) ? { ...row, __deleted: true } : row)));
    setDeletedLocalIds(nextDeleted);
    setSelectedRows([]);
    setEditingSheet(null);
    setNewSheetName(null);
    setMessage(`Sheet“${activeSheet}”已标记删除，可撤回或保存更改`);
  }

  function restoreActiveSheetDelete() {
    if (!canRestoreActiveSheetDelete) return;
    const targetLocalIds = new Set(activeSheetRows.filter((row) => row.__deleted).map((row) => row.__localId));
    const nextDeleted = new Set(deletedLocalIds);
    targetLocalIds.forEach((localId) => nextDeleted.delete(localId));
    setGridRows(gridRows.map((row) => (targetLocalIds.has(row.__localId) ? { ...row, __deleted: false } : row)));
    setDeletedLocalIds(nextDeleted);
    setMessage(`已撤回 Sheet“${activeSheet}”的删除标记`);
  }

  if (!selectedBatch) {
    return (
      <div className="workspace-grid empty-workspace">
        <section className="content-panel empty-state">
          <h2>还没有批次</h2>
          <p>先创建一个本周草稿，再开始录入或从 Excel 导入。</p>
          <button className="primary-button" onClick={() => setCreateDialogOpen(true)}>
            <Plus size={16} />
            新建批次
          </button>
        </section>
        {createDialogOpen && (
          <Modal title="新建批次" onClose={() => setCreateDialogOpen(false)}>
            <CreateBatchPanel
              reloadBatches={reloadBatches}
              setMessage={setMessage}
              onCreated={(batch) => {
                setCreateDialogOpen(false);
                setSelectedBatchId(batch.id);
              }}
            />
          </Modal>
        )}
      </div>
    );
  }

  return (
    <div className="workspace-grid">
      <section className="batch-toolbar-panel">
        <div className="batch-picker">
          <label>
            当前批次
            <select value={selectedBatch.id} onChange={(event) => guardedSelectBatch(Number(event.target.value))}>
              {batches.map((batch) => (
                <option key={batch.id} value={batch.id}>{batch.name}</option>
              ))}
            </select>
          </label>
        </div>
        <div className="batch-summary">
          <span>状态</span>
          <div className="status-control">
            <StatusPill value={selectedBatch.status === "archived" ? "已归档" : "草稿"} />
            {selectedBatch.status === "draft" && canArchiveBatch && (
              <button className="mini-button" onClick={archiveCurrentBatch} type="button">
                <Archive size={14} />
                归档
              </button>
            )}
            {selectedBatch.status === "archived" && canRestoreBatch && (
              <button className="mini-button" onClick={restoreCurrentBatchDraft} type="button">
                <RefreshCcw size={14} />
                恢复草稿
              </button>
            )}
          </div>
        </div>
        <div className="batch-summary">
          <span>期间</span>
          <strong>{formatDateRange(selectedBatch.start_date, selectedBatch.end_date)}</strong>
        </div>
        <div className="batch-summary">
          <span>批次记录</span>
          <strong>{selectedBatch.request_count || 0} 条</strong>
        </div>
        <div className="batch-summary">
          <span>批次金额</span>
          <strong>{formatMoney(selectedBatch.total_amount || 0)}</strong>
        </div>
        <div className="batch-actions">
          <button className="ghost-button" onClick={() => setCreateDialogOpen(true)}>
            <Plus size={16} />
            新建批次
          </button>
          <button className="primary-button" onClick={() => setRolloverDialogOpen(true)}>
            <Archive size={16} />
            从上周生成本周
          </button>
        </div>
      </section>
      <section className="content-panel">
        <div className="metric-row">
          <Metric label="当前记录数" value={`${visibleTotals.count}`} />
          <Metric label="当前金额合计" value={formatMoney(visibleTotals.amount)} />
          <Metric label="已付款单数" value={`${financeReviewCounts.paid} 单`} />
          <Metric label="部分付款单数" value={`${financeReviewCounts.partial} 单`} />
          <Metric label="未付款单数" value={`${financeReviewCounts.unpaid} 单`} />
        </div>
        <div className="toolbar">
          <div className="search-box">
            <Search size={16} />
            <input value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="搜索单号、摘要、收款方、项目" />
          </div>
          <input value={filters.payment_account} onChange={(event) => setFilters({ ...filters, payment_account: event.target.value })} placeholder="付款账户" />
          <input value={filters.invoice_status} onChange={(event) => setFilters({ ...filters, invoice_status: event.target.value })} placeholder="开票情况" />
          <select value={filters.finance_review} onChange={(event) => setFilters({ ...filters, finance_review: event.target.value })} aria-label="财务审批">
            <option value="">全部财务审批</option>
            {financeApprovalOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <select value={filters.general_manager_approval} onChange={(event) => setFilters({ ...filters, general_manager_approval: event.target.value })} aria-label="总经理审批">
            <option value="">全部总经理审批</option>
            <option value={GENERAL_MANAGER_EMPTY_FILTER}>未选择</option>
            {generalManagerApprovalOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <button className="ghost-button" onClick={() => setMessage("筛选已应用")} onKeyDown={(event) => activateButtonByKeyboard(event, () => setMessage("筛选已应用"))}>
            <Filter size={16} />
            筛选
          </button>
          <button className="primary-button" onClick={() => openRequestEditor({ ...emptyRequest, source_sheet: defaultSourceSheet })} onKeyDown={(event) => activateButtonByKeyboard(event, () => openRequestEditor({ ...emptyRequest, source_sheet: defaultSourceSheet }))}>
            <Plus size={16} />
            新增
          </button>
          <button className="ghost-button" onClick={addBlankRow} onKeyDown={(event) => activateButtonByKeyboard(event, addBlankRow)}>
            <Plus size={16} />
            插入空行
          </button>
          <button
            className={wrapText ? "ghost-button active-toggle" : "ghost-button"}
            onClick={() => setWrapText(!wrapText)}
            onKeyDown={(event) => activateButtonByKeyboard(event, () => setWrapText(!wrapText))}
            aria-pressed={wrapText}
          >
            <AlignLeft size={16} />
            {wrapText ? "取消换行" : "换行"}
          </button>
          <button className="primary-button" onClick={saveGridChanges} onKeyDown={(event) => activateButtonByKeyboard(event, saveGridChanges)} disabled={!hasUnsavedChanges}>
            <Save size={16} />
            保存更改
          </button>
        </div>
        <div className="sheet-tabs" role="tablist" aria-label="Sheet 分页">
          {sheetTabs.map((tab) => (
            editingSheet?.key === tab.key ? (
              <div key={tab.key} className="sheet-tab active editing" role="tab" aria-selected="true">
                <input
                  autoFocus
                  value={editingSheet.value}
                  onChange={(event) => setEditingSheet({ ...editingSheet, value: event.target.value })}
                  onBlur={commitRenameSheet}
                  onKeyDown={handleSheetRenameKeyDown}
                  aria-label="Sheet 名称"
                />
                <small>{tab.count}</small>
                {tab.pendingDelete && <em>待删</em>}
              </div>
            ) : (
              <button
                key={tab.key}
                className={[
                  activeSheet === tab.key ? "sheet-tab active" : "sheet-tab",
                  tab.pendingDelete ? "pending-delete" : "",
                ].filter(Boolean).join(" ")}
                onPointerDown={() => setActiveSheet(tab.key)}
                onClick={() => setActiveSheet(tab.key)}
                onFocus={() => setActiveSheet(tab.key)}
                onDoubleClick={() => beginRenameSheet(tab.key)}
                onKeyDown={(event) => activateButtonByKeyboard(event, () => setActiveSheet(tab.key))}
                role="tab"
                aria-selected={activeSheet === tab.key}
                type="button"
                title={tab.key === ALL_SHEET ? "全部 Sheet" : "双击重命名"}
              >
                <span>{tab.label}</span>
                <small>{tab.count}</small>
                {tab.pendingDelete && <em>待删</em>}
              </button>
            )
          ))}
          {newSheetName !== null ? (
            <div className="sheet-tab active editing sheet-create" role="tab" aria-selected="true">
              <input
                autoFocus
                value={newSheetName}
                onChange={(event) => setNewSheetName(event.target.value)}
                onBlur={commitCreateSheet}
                onKeyDown={handleCreateSheetKeyDown}
                aria-label="新增 Sheet 名称"
              />
              <small>0</small>
            </div>
          ) : (
            canEditGrid && (
              <button
                className="sheet-tab add-sheet-tab"
                onClick={beginCreateSheet}
                onKeyDown={(event) => activateButtonByKeyboard(event, beginCreateSheet)}
                type="button"
                title="新增 Sheet"
              >
                <Plus size={15} />
                <span>Sheet</span>
              </button>
            )
          )}
        </div>
        {activeSheet !== ALL_SHEET && canEditGrid && (
          <div className="sheet-actionbar">
            <span>
              当前 Sheet：{activeSheet}
              {activeSheetPendingDeleteCount > 0 && `，${activeSheetPendingDeleteCount} 行待删除`}
            </span>
            <button className="ghost-button danger-button" type="button" onClick={markDeleteActiveSheet} disabled={!canDeleteActiveSheet}>
              <Trash2 size={16} />
              删除当前 Sheet
            </button>
            <button className="ghost-button" type="button" onClick={restoreActiveSheetDelete} disabled={!canRestoreActiveSheetDelete}>
              <RefreshCcw size={16} />
              撤回删除
            </button>
          </div>
        )}
        {hasUnsavedChanges && (
          <div className="dirty-banner">
            有 {dirtyCells.size} 个修改待保存单元格、{deletedLocalIds.size} 行待删除
            {selectedBatch.status === "archived" && <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="归档更正原因" />}
          </div>
        )}
        {selectedRows.length > 0 && (
          <div className="bulk-bar">
            <span>已选 {selectedRows.length} 条</span>
            {canBulkUpdatePayment && (
              <>
                <select value={bulkStatus} onChange={(event) => setBulkStatus(event.target.value)}>
                  {financeApprovalOptions.map((option) => <option key={option} value={option}>{option}</option>)}
                </select>
                {selectedBatch.status === "archived" && isPrivilegedRole(user.role) && <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="更正原因" />}
                <button className="primary-button" onClick={bulkUpdate}>
                  <CheckCircle2 size={16} />
                  批量更新财务审批
                </button>
              </>
            )}
            <button className="ghost-button" onClick={markDeleteSelected}>
              <Trash2 size={16} />
              删除所选行
            </button>
          </div>
        )}
        <EditablePaymentGrid
          rows={visibleRows}
          onRowsChange={mergeVisibleRows}
          dirtyCells={dirtyCells}
          setDirtyCells={setDirtyCells}
          deletedLocalIds={deletedLocalIds}
          selectedRows={selectedRows}
          setSelectedRows={setSelectedRows}
          readOnly={selectedBatch.status === "archived" && !isPrivilegedRole(user.role)}
          canEditField={(field) => canEditGrid && canEditRequestField(user.role, field)}
          onEdit={openRequestEditor}
          onOpenAttachments={openAttachmentsFromGrid}
          attachmentCounts={attachmentCounts}
          onSave={saveGridChanges}
          defaultSourceSheet={defaultSourceSheet}
          wrapText={wrapText}
        />
      </section>
      {createDialogOpen && (
        <Modal title="新建批次" onClose={() => setCreateDialogOpen(false)}>
          <CreateBatchPanel
            reloadBatches={reloadBatches}
            setMessage={setMessage}
            onCreated={(batch) => {
              setCreateDialogOpen(false);
              guardedSelectBatch(batch.id);
            }}
          />
        </Modal>
      )}
      {rolloverDialogOpen && (
        <Modal title="从上周生成本周" onClose={() => setRolloverDialogOpen(false)}>
          <RolloverPanel
            batches={batches}
            selectedBatch={selectedBatch}
            onCreated={async (batch) => {
              setRolloverDialogOpen(false);
              await reloadBatches();
              guardedSelectBatch(batch.id);
            }}
            setMessage={setMessage}
          />
        </Modal>
      )}
      {editing && (
        <RequestEditor
          key={editing.id ? `request-${editing.id}` : "request-new"}
          batch={selectedBatch}
          request={editing}
          reason={reason}
          setReason={setReason}
          onCancel={() => {
            setEditing(null);
            setEditorDraft(null);
            setEditorDirty(false);
          }}
          onSave={saveRequest}
          onDraftChange={setEditorDraft}
          onDirtyChange={setEditorDirty}
          onAttachmentsChanged={refreshAttachmentCounts}
          canEditAttachments={selectedBatch.status !== "archived" || isPrivilegedRole(user.role)}
          canEditField={(field) => canEditGrid && canEditRequestField(user.role, field)}
        />
      )}
      {previewImages && (
        <ImagePreviewDialog
          images={previewImages.images}
          index={previewImages.index}
          onIndexChange={(index) => setPreviewImages({ ...previewImages, index })}
          onClose={() => setPreviewImages(null)}
        />
      )}
    </div>
  );
}

function CreateBatchPanel({
  reloadBatches,
  setMessage,
  onCreated,
}: {
  reloadBatches: () => Promise<void>;
  setMessage: (message: string) => void;
  onCreated?: (batch: Batch) => void;
}) {
  const [name, setName] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const generatedName = formatBatchName(startDate, endDate);
  const invalidDateRange = isInvalidDateRange(startDate, endDate);
  const canCreate = Boolean(startDate && endDate && name.trim() && !invalidDateRange);

  useEffect(() => {
    if (!nameTouched) setName(generatedName);
  }, [generatedName, nameTouched]);

  async function create() {
    if (!canCreate) return;
    const res = await api.createBatch({ name, start_date: startDate, end_date: endDate });
    setName("");
    setStartDate("");
    setEndDate("");
    setNameTouched(false);
    await reloadBatches();
    onCreated?.(res.batch);
    setMessage("批次已创建");
  }

  function regenerateName() {
    setName(generatedName);
    setNameTouched(false);
  }

  return (
    <div className="create-box">
      <div className="section-title">新批次</div>
      <div className="date-row">
        <input
          type="date"
          value={startDate}
          onInput={(event) => setStartDate(event.currentTarget.value)}
          onChange={(event) => setStartDate(event.currentTarget.value)}
        />
        <input
          type="date"
          value={endDate}
          onInput={(event) => setEndDate(event.currentTarget.value)}
          onChange={(event) => setEndDate(event.currentTarget.value)}
        />
      </div>
      {invalidDateRange && <p className="error-text">结束日期不能早于开始日期</p>}
      <input
        value={name}
        onChange={(event) => {
          setNameTouched(true);
          setName(event.target.value);
        }}
        placeholder="选择日期后自动生成批次名称"
      />
      {nameTouched && generatedName && (
        <button className="ghost-button" onClick={regenerateName} type="button">
          <RefreshCcw size={16} />
          按日期重新生成
        </button>
      )}
      <button className="primary-button" onClick={create} disabled={!canCreate}>
        <Plus size={16} />
        创建
      </button>
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="modal-panel" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="ghost-button" onClick={onClose} type="button">关闭</button>
        </div>
        {children}
      </section>
    </div>
  );
}

function RolloverPanel({
  batches,
  selectedBatch,
  onCreated,
  setMessage,
}: {
  batches: Batch[];
  selectedBatch?: Batch | null;
  onCreated: (batch: Batch) => void;
  setMessage: (message: string) => void;
}) {
  const defaultSource = selectedBatch?.id || batches.find((batch) => batch.status === "archived")?.id || batches[0]?.id || "";
  const [sourceBatchId, setSourceBatchId] = useState<number | "">(defaultSource);
  const [name, setName] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [copyingMode, setCopyingMode] = useState<RolloverCopyMode | null>(null);
  const generatedName = formatBatchName(startDate, endDate);
  const invalidDateRange = isInvalidDateRange(startDate, endDate);
  const canRollover = Boolean(sourceBatchId && startDate && endDate && name.trim() && !invalidDateRange && !copyingMode);

  useEffect(() => {
    if (!sourceBatchId && defaultSource) setSourceBatchId(defaultSource);
  }, [defaultSource, sourceBatchId]);

  useEffect(() => {
    if (!nameTouched) setName(generatedName);
  }, [generatedName, nameTouched]);

  async function rollover(copyMode: RolloverCopyMode) {
    if (!canRollover) return;
    setCopyingMode(copyMode);
    try {
      const res = await api.rolloverBatch(Number(sourceBatchId), { name, start_date: startDate, end_date: endDate, copy_mode: copyMode });
      setName("");
      setStartDate("");
      setEndDate("");
      setNameTouched(false);
      onCreated(res.batch);
      setMessage(copyMode === "all" ? `已生成本周草稿，复制全部 ${res.copied_count} 条记录` : `已生成本周草稿，复制 ${res.copied_count} 条未完成记录`);
    } finally {
      setCopyingMode(null);
    }
  }

  function regenerateName() {
    setName(generatedName);
    setNameTouched(false);
  }

  return (
    <div className="create-box rollover-box">
      <div className="section-title">从上周生成本周</div>
      <select value={sourceBatchId} onChange={(event) => setSourceBatchId(event.target.value ? Number(event.target.value) : "")}>
        <option value="">选择来源批次</option>
        {batches.map((batch) => (
          <option key={batch.id} value={batch.id}>
            {batch.name}{batch.id === selectedBatch?.id ? "（当前）" : ""} · {batch.status === "archived" ? "已归档" : "草稿"} · {batch.request_count || 0}条
          </option>
        ))}
      </select>
      <div className="date-row">
        <input
          type="date"
          value={startDate}
          onInput={(event) => setStartDate(event.currentTarget.value)}
          onChange={(event) => setStartDate(event.currentTarget.value)}
        />
        <input
          type="date"
          value={endDate}
          onInput={(event) => setEndDate(event.currentTarget.value)}
          onChange={(event) => setEndDate(event.currentTarget.value)}
        />
      </div>
      {invalidDateRange && <p className="error-text">结束日期不能早于开始日期</p>}
      <input
        value={name}
        onChange={(event) => {
          setNameTouched(true);
          setName(event.target.value);
        }}
        placeholder="选择日期后自动生成本周批次名称"
      />
      {nameTouched && generatedName && (
        <button className="ghost-button" onClick={regenerateName} type="button">
          <RefreshCcw size={16} />
          按日期重新生成
        </button>
      )}
      <div className="rollover-actions">
        <button className="ghost-button" onClick={() => rollover("unfinished")} disabled={!canRollover}>
          <Archive size={16} />
          {copyingMode === "unfinished" ? "复制中" : "复制未完成项"}
        </button>
        <button className="primary-button" onClick={() => rollover("all")} disabled={!canRollover}>
          <Archive size={16} />
          {copyingMode === "all" ? "复制中" : "复制全部"}
        </button>
      </div>
    </div>
  );
}

function EditablePaymentGrid({
  rows,
  onRowsChange,
  dirtyCells,
  setDirtyCells,
  deletedLocalIds,
  selectedRows,
  setSelectedRows,
  readOnly,
  onEdit,
  onOpenAttachments,
  attachmentCounts,
  onSave,
  defaultSourceSheet,
  wrapText,
  canEditField,
}: {
  rows: GridRow[];
  onRowsChange: (rows: GridRow[]) => void;
  dirtyCells: Set<string>;
  setDirtyCells: (cells: Set<string>) => void;
  deletedLocalIds: Set<string>;
  selectedRows: number[];
  setSelectedRows: (ids: number[]) => void;
  onEdit: (request: PaymentRequest) => void;
  onOpenAttachments: (request: PaymentRequest) => void;
  attachmentCounts: Record<number, number>;
  readOnly: boolean;
  onSave: () => void;
  defaultSourceSheet: string;
  wrapText: boolean;
  canEditField: (field: keyof PaymentRequest) => boolean;
}) {
  const [activeCell, setActiveCell] = useState<{ row: number; col: number }>({ row: 0, col: 0 });
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const topScrollbarRef = useRef<HTMLDivElement | null>(null);
  const [tableScrollWidth, setTableScrollWidth] = useState(0);

  useEffect(() => {
    const tableWrap = tableWrapRef.current;
    const topScrollbar = topScrollbarRef.current;
    if (!tableWrap || !topScrollbar) return;

    let syncing = false;
    const updateScrollWidth = () => setTableScrollWidth(tableWrap.scrollWidth);
    const syncScroll = (source: HTMLDivElement, target: HTMLDivElement) => {
      if (syncing) return;
      syncing = true;
      target.scrollLeft = source.scrollLeft;
      requestAnimationFrame(() => {
        syncing = false;
      });
    };
    const syncFromTable = () => syncScroll(tableWrap, topScrollbar);
    const syncFromTop = () => syncScroll(topScrollbar, tableWrap);

    updateScrollWidth();
    tableWrap.addEventListener("scroll", syncFromTable);
    topScrollbar.addEventListener("scroll", syncFromTop);
    const resizeObserver = new ResizeObserver(updateScrollWidth);
    resizeObserver.observe(tableWrap);
    const table = tableWrap.querySelector("table");
    if (table) resizeObserver.observe(table);

    return () => {
      tableWrap.removeEventListener("scroll", syncFromTable);
      topScrollbar.removeEventListener("scroll", syncFromTop);
      resizeObserver.disconnect();
    };
  }, [rows.length]);

  function scrollTableBy(delta: number) {
    tableWrapRef.current?.scrollBy({ left: delta, behavior: "smooth" });
  }

  function toggle(id: number) {
    setSelectedRows(selectedRows.includes(id) ? selectedRows.filter((item) => item !== id) : [...selectedRows, id]);
  }

  function updateCell(rowIndex: number, column: GridColumn, value: string) {
    if (readOnly || !canEditField(column.key)) return;
    const nextRows = [...rows];
    const row = { ...nextRows[rowIndex] };
    row[column.key] = normalizeCellValue(column, value) as never;
    nextRows[rowIndex] = row;
    const nextDirty = new Set(dirtyCells);
    nextDirty.add(`${row.__localId}:${column.key}`);
    onRowsChange(nextRows);
    setDirtyCells(nextDirty);
  }

  function focusCell(rowIndex: number, colIndex: number) {
    const boundedRow = Math.max(0, Math.min(rowIndex, rows.length - 1));
    const boundedCol = Math.max(0, Math.min(colIndex, gridColumns.length - 1));
    setActiveCell({ row: boundedRow, col: boundedCol });
    requestAnimationFrame(() => {
      const target = document.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[data-cell="${boundedRow}-${boundedCol}"]`);
      target?.focus();
      if (target && "select" in target) target.select();
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>, rowIndex: number, colIndex: number) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      onSave();
      return;
    }
    if (event.key === "Escape") {
      event.currentTarget.blur();
      return;
    }
    const movement: Record<string, [number, number]> = {
      Enter: [1, 0],
      ArrowDown: [1, 0],
      ArrowUp: [-1, 0],
      ArrowLeft: [0, -1],
      ArrowRight: [0, 1],
    };
    if (event.key === "Tab") {
      event.preventDefault();
      focusCell(rowIndex, colIndex + (event.shiftKey ? -1 : 1));
      return;
    }
    if (movement[event.key]) {
      event.preventDefault();
      const [rowDelta, colDelta] = movement[event.key];
      focusCell(rowIndex + rowDelta, colIndex + colDelta);
    }
  }

  function handleSelectKeyDown(event: KeyboardEvent<HTMLSelectElement>, rowIndex: number, colIndex: number) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      onSave();
      return;
    }
    if (event.key === "Escape") {
      event.currentTarget.blur();
      return;
    }
    if (event.key === "Tab") {
      event.preventDefault();
      focusCell(rowIndex, colIndex + (event.shiftKey ? -1 : 1));
    }
  }

  function handlePaste(event: ClipboardEvent<HTMLInputElement | HTMLTextAreaElement>) {
    if (readOnly) return;
    const text = event.clipboardData.getData("text/plain");
    const matrix = parseClipboardTable(text);
    if (matrix.length <= 1 && (matrix[0]?.length || 0) <= 1) return;
    event.preventDefault();
    const nextRows = [...rows];
    const nextDirty = new Set(dirtyCells);
    matrix.forEach((sourceRow, rowOffset) => {
      const targetRowIndex = activeCell.row + rowOffset;
      while (targetRowIndex >= nextRows.length) {
        nextRows.push({ ...emptyRequest, __localId: newLocalId(), __isNew: true, source_sheet: defaultSourceSheet });
      }
        const targetRow = { ...nextRows[targetRowIndex] };
        sourceRow.forEach((cellValue, colOffset) => {
          const column = gridColumns[activeCell.col + colOffset];
          if (!column || !canEditField(column.key)) return;
          targetRow[column.key] = normalizeCellValue(column, cellValue) as never;
          nextDirty.add(`${targetRow.__localId}:${column.key}`);
      });
      nextRows[targetRowIndex] = targetRow;
    });
    onRowsChange(nextRows);
    setDirtyCells(nextDirty);
  }

  return (
    <div className="grid-scroller">
      <div className="table-scroll-control">
        <button className="grid-scroll-button" type="button" onClick={() => scrollTableBy(-520)} aria-label="向左滚动列" title="向左滚动列">
          <ChevronLeft size={16} />
        </button>
        <div className="table-scrollbar" ref={topScrollbarRef} aria-hidden="true">
          <div style={{ width: tableScrollWidth || "100%" }} />
        </div>
        <button className="grid-scroll-button" type="button" onClick={() => scrollTableBy(520)} aria-label="向右滚动列" title="向右滚动列">
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="table-wrap" ref={tableWrapRef}>
        <table className={wrapText ? "data-table editable-grid wrap-text" : "data-table editable-grid"}>
          <thead>
            <tr>
              <th className="checkbox-col" style={{ width: 52, minWidth: 52 }}></th>
              <th className="attachment-col" style={{ width: 112, minWidth: 112 }}>附件</th>
              {gridColumns.map((column) => (
                <th key={column.key} style={{ width: column.width, minWidth: column.width }}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={row.__localId} className={deletedLocalIds.has(row.__localId) ? "row-deleted" : ""} onDoubleClick={() => row.id && onEdit(row as PaymentRequest)}>
                <td className="checkbox-col">
                  {row.id && <input type="checkbox" checked={selectedRows.includes(row.id)} onChange={() => toggle(row.id!)} />}
                </td>
                <td className="attachment-col">
                  {row.id ? (
                    <button
                      className={attachmentCounts[row.id] ? "attachment-chip has-attachments" : "attachment-chip"}
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenAttachments(row as PaymentRequest);
                      }}
                    >
                      <Paperclip size={14} />
                      {attachmentCounts[row.id] ? `附件 ${attachmentCounts[row.id]}` : "上传"}
                    </button>
                  ) : (
                    <span className="muted-chip">先保存</span>
                  )}
                </td>
	                {gridColumns.map((column, colIndex) => {
	                  const dirty = dirtyCells.has(`${row.__localId}:${column.key}`);
	                  const cellValue = cellDisplayValue(row, column);
	                  const selectOptions = selectOptionsForField(column.key, cellValue);
	                  const shouldWrap = wrapText && wrappableColumnKeys.has(column.key) && column.type !== "number" && column.type !== "date";
	                  const cellReadOnly = readOnly || row.__deleted || !canEditField(column.key);
	                  const fieldClass = [
	                    column.key === "dingding_id" ? "mono" : "",
	                    column.key === "amount" ? "amount-input" : "",
	                    shouldWrap ? "wrap-field" : "",
	                    !canEditField(column.key) ? "readonly-field" : "",
	                  ].filter(Boolean).join(" ");
                  return (
                    <td key={column.key} className={dirty ? "dirty-cell" : ""} style={{ width: column.width, minWidth: column.width, maxWidth: column.width }}>
                      {selectOptions ? (
                        <select
                          data-cell={`${rowIndex}-${colIndex}`}
                          className={fieldClass}
                          value={cellValue}
	                          disabled={cellReadOnly}
                          onFocus={() => setActiveCell({ row: rowIndex, col: colIndex })}
                          onKeyDown={(event) => handleSelectKeyDown(event, rowIndex, colIndex)}
                          onChange={(event) => updateCell(rowIndex, column, event.target.value)}
                        >
                          {selectOptions.map((option) => (
                            <option key={option} value={option}>{option || "未选择"}</option>
                          ))}
                        </select>
                      ) : shouldWrap ? (
                        <textarea
                          data-cell={`${rowIndex}-${colIndex}`}
                          className={fieldClass}
                          rows={wrappedTextRows(cellValue, column)}
                          value={cellValue}
                          title={cellValue}
	                          readOnly={cellReadOnly}
                          onFocus={() => setActiveCell({ row: rowIndex, col: colIndex })}
                          onPaste={handlePaste}
                          onKeyDown={(event) => handleKeyDown(event, rowIndex, colIndex)}
                          onChange={(event) => updateCell(rowIndex, column, event.target.value)}
                        />
                      ) : (
                        <input
                          data-cell={`${rowIndex}-${colIndex}`}
                          className={fieldClass}
                          type={column.type === "number" ? "number" : column.type === "date" ? "date" : "text"}
                          value={cellValue}
                          title={cellValue}
	                          readOnly={cellReadOnly}
                          onFocus={() => setActiveCell({ row: rowIndex, col: colIndex })}
                          onPaste={handlePaste}
                          onKeyDown={(event) => handleKeyDown(event, rowIndex, colIndex)}
                          onChange={(event) => updateCell(rowIndex, column, event.target.value)}
                        />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RequestEditor({
  batch,
  request,
  reason,
  setReason,
  onCancel,
  onSave,
  onDraftChange,
  onDirtyChange,
  onAttachmentsChanged,
  canEditAttachments,
  canEditField,
}: {
  batch: Batch;
  request: Partial<PaymentRequest>;
  reason: string;
  setReason: (value: string) => void;
  onCancel: () => void;
  onSave: (request: Partial<PaymentRequest>) => Promise<void> | void;
  onDraftChange?: (request: Partial<PaymentRequest> | null) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onAttachmentsChanged?: () => Promise<void> | void;
  canEditAttachments: boolean;
  canEditField: (field: keyof PaymentRequest) => boolean;
}) {
  const [form, setForm] = useState<Partial<PaymentRequest>>(request);
  const [attachments, setAttachments] = useState<AttachmentLink[]>([]);
  const [attachmentForm, setAttachmentForm] = useState({ label: "", url_path: "" });
  const [imageLabel, setImageLabel] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [previewImages, setPreviewImages] = useState<{ images: AttachmentLink[]; index: number } | null>(null);
  const fields: Array<keyof PaymentRequest> = [
    "dingding_id",
    "payment_account",
    "expense_type",
    "summary",
    "amount",
    "project",
    "payee_account",
    "payee_name",
    "bank_name",
    "invoice_status",
    "needed_payment_date",
    "finance_review",
    "actual_payment_date",
    "general_manager_approval",
    "general_manager_approval_date",
    "general_manager_opinion",
    "remark",
    "source_sheet",
  ];
  const isDirty = requestFormDirty(request, form, fields);

  useEffect(() => {
    setForm(request);
    setAttachments([]);
    setAttachmentForm({ label: "", url_path: "" });
    setImageLabel("");
    setImageFile(null);
    setPreviewImages(null);
  }, [request]);

  useEffect(() => {
    onDraftChange?.(form);
  }, [form, onDraftChange]);

  useEffect(() => {
    onDirtyChange?.(isDirty);
  }, [isDirty, onDirtyChange]);

  useEffect(
    () => () => {
      onDraftChange?.(null);
      onDirtyChange?.(false);
    },
    [],
  );

  useEffect(() => {
    if (!request.id) {
      setAttachments([]);
      return;
    }
    api.attachments(batch.id, request.id).then((res) => setAttachments(res.attachments)).catch(() => setAttachments([]));
  }, [batch.id, request.id]);

  async function addAttachment() {
    if (!form.id || !canEditAttachments || !attachmentForm.url_path.trim()) return;
    const res = await api.createAttachment(batch.id, form.id, attachmentForm);
    setAttachments([...attachments, res.attachment]);
    setAttachmentForm({ label: "", url_path: "" });
    await onAttachmentsChanged?.();
  }

  async function uploadImageAttachment() {
    if (!form.id || !canEditAttachments || !imageFile) return;
    setUploadingImage(true);
    try {
      const res = await api.uploadImageAttachment(batch.id, form.id, imageFile, imageLabel, reason);
      setAttachments([...attachments, res.attachment]);
      setImageLabel("");
      setImageFile(null);
      await onAttachmentsChanged?.();
    } finally {
      setUploadingImage(false);
    }
  }

  async function removeAttachment(id: number) {
    if (!form.id || !canEditAttachments) return;
    await api.deleteAttachment(batch.id, form.id, id, reason);
    setAttachments(attachments.filter((item) => item.id !== id));
    setPreviewImages(null);
    await onAttachmentsChanged?.();
  }

  function previewAttachment(item: AttachmentLink) {
    const images = attachments.filter(isImageAttachment);
    const index = Math.max(0, images.findIndex((image) => image.id === item.id));
    setPreviewImages({ images, index });
  }

  return (
    <div className="drawer">
      <div className="drawer-head">
        <h2>{form.id ? "编辑请款" : "新增请款"}</h2>
        <button className="ghost-button" onClick={onCancel}>关闭</button>
      </div>
      <div className="form-grid">
        {fields.map((field) => {
          const fieldEditable = canEditField(field);
          return (
          <label key={field} className={field === "summary" || field === "remark" || field === "general_manager_opinion" ? "span-2" : ""}>
            {fieldLabels[field] || field}
            {selectOptionsForField(field, String(form[field] || "")) ? (
              <select
                value={String(form[field] || "")}
                disabled={!fieldEditable}
                onChange={(event) => setForm({ ...form, [field]: event.target.value })}
              >
                {selectOptionsForField(field, String(form[field] || ""))!.map((option) => (
                  <option key={option} value={option}>{option || "未选择"}</option>
                ))}
              </select>
            ) : field === "summary" || field === "remark" || field === "general_manager_opinion" ? (
              <textarea className={field === "summary" ? "summary-textarea" : ""} value={String(form[field] || "")} readOnly={!fieldEditable} onChange={(event) => setForm({ ...form, [field]: event.target.value })} />
            ) : (
              <input
                type={field === "amount" ? "number" : field.includes("date") ? "date" : "text"}
                value={String(form[field] || "")}
                readOnly={!fieldEditable}
                onChange={(event) => setForm({ ...form, [field]: field === "amount" ? Number(event.target.value) : event.target.value })}
              />
            )}
          </label>
          );
        })}
        {batch.status === "archived" && (
          <label className="span-2">
            更正原因
            <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
          </label>
        )}
        {form.id && (
          <div className="span-2 attachment-box">
            <div className="section-title">附件</div>
            <div className="attachment-form image-upload-form">
              <input placeholder="图片名称，可选" value={imageLabel} onChange={(event) => setImageLabel(event.target.value)} disabled={!canEditAttachments} />
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif,image/bmp"
                disabled={!canEditAttachments}
                onChange={(event) => setImageFile(event.target.files?.[0] || null)}
              />
              <button className="primary-button" onClick={uploadImageAttachment} type="button" disabled={!canEditAttachments || !imageFile || uploadingImage}>
                <ImageIcon size={16} />
                {uploadingImage ? "上传中" : "上传图片"}
              </button>
            </div>
            <div className="attachment-form">
              <input placeholder="名称" value={attachmentForm.label} onChange={(event) => setAttachmentForm({ ...attachmentForm, label: event.target.value })} disabled={!canEditAttachments} />
              <input placeholder="流程链接或本地路径" value={attachmentForm.url_path} onChange={(event) => setAttachmentForm({ ...attachmentForm, url_path: event.target.value })} disabled={!canEditAttachments} />
              <button className="ghost-button" onClick={addAttachment} type="button" disabled={!canEditAttachments}>添加</button>
            </div>
            <div className="attachment-list">
              {attachments.map((item) => (
                <div key={item.id} className="attachment-item">
                  {isImageAttachment(item) && (
                    <button className="attachment-thumb-button" type="button" onClick={() => previewAttachment(item)}>
                      <img className="attachment-thumb" src={attachmentImageUrl(item)} alt={attachmentTitle(item, "图片附件")} />
                    </button>
                  )}
                  {!isImageAttachment(item) && (
                    <div className="attachment-thumb-placeholder">
                      <Paperclip size={20} />
                    </div>
                  )}
                  <div className="attachment-meta">
                    <strong>{attachmentTitle(item, "附件")}</strong>
                    <span>{isImageAttachment(item) ? item.original_filename || item.url_path : item.url_path}</span>
                  </div>
                  <div className="attachment-actions">
                    {isImageAttachment(item) && (
                      <button className="ghost-button" type="button" onClick={() => previewAttachment(item)}>
                        <ImageIcon size={14} />
                        预览
                      </button>
                    )}
                    {canEditAttachments && <button type="button" onClick={() => removeAttachment(item.id)}>删除</button>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="drawer-actions">
        <button className="primary-button" onClick={() => onSave(form)}>
          <Save size={16} />
          保存
        </button>
      </div>
      {previewImages && (
        <ImagePreviewDialog
          images={previewImages.images}
          index={previewImages.index}
          onIndexChange={(index) => setPreviewImages({ ...previewImages, index })}
          onClose={() => setPreviewImages(null)}
        />
      )}
    </div>
  );
}

function ImagePreviewDialog({
  images,
  index,
  onIndexChange,
  onClose,
}: {
  images: AttachmentLink[];
  index: number;
  onIndexChange: (index: number) => void;
  onClose: () => void;
}) {
  const imageCount = images.length;
  const activeIndex = imageCount ? Math.min(Math.max(index, 0), imageCount - 1) : 0;
  const activeImage = images[activeIndex];
  const canMove = imageCount > 1;

  function move(delta: number) {
    if (!canMove) return;
    onIndexChange((activeIndex + delta + imageCount) % imageCount);
  }

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "ArrowLeft") {
        move(-1);
      } else if (event.key === "ArrowRight") {
        move(1);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [activeIndex, imageCount, onClose]);

  if (!activeImage) return null;

  return (
    <div className="image-preview-backdrop" role="dialog" aria-modal="true" aria-label="图片预览" onMouseDown={onClose}>
      <div className="image-preview-panel" onMouseDown={(event) => event.stopPropagation()}>
        <div className="image-preview-head">
          <strong>{attachmentTitle(activeImage, "图片附件")}</strong>
          <div className="image-preview-actions">
            {canMove && <span className="image-preview-count">{activeIndex + 1} / {imageCount}</span>}
            <button className="ghost-button" type="button" onClick={onClose}>关闭</button>
          </div>
        </div>
        <div className="image-preview-body">
          {canMove && (
            <button className="image-nav-button prev" type="button" aria-label="上一张图片" title="上一张图片" onClick={() => move(-1)}>
              <ChevronLeft size={22} />
            </button>
          )}
          <img src={attachmentImageUrl(activeImage)} alt={attachmentTitle(activeImage, "图片预览")} />
          {canMove && (
            <button className="image-nav-button next" type="button" aria-label="下一张图片" title="下一张图片" onClick={() => move(1)}>
              <ChevronRight size={22} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ArchiveView({
  batches,
  selectedBatch,
  setSelectedBatchId,
  reloadBatches,
  setMessage,
}: {
  batches: Batch[];
  selectedBatch: Batch | null;
  setSelectedBatchId: (id: number) => void;
  reloadBatches: () => Promise<void>;
  setMessage: (message: string) => void;
}) {
  const [logs, setLogs] = useState<AuditLog[]>([]);

  async function archive() {
    if (!selectedBatch) return;
    await api.archive(selectedBatch.id);
    setMessage("批次已归档");
  }

  async function loadLogs(batchId: number) {
    const res = await api.audit(batchId);
    setLogs(res.logs);
  }

  async function deleteDraft(batch: Batch) {
    if (batch.status !== "draft") return;
    if (!window.confirm(`确定删除草稿批次“${batch.name}”吗？删除后该批次下的请款明细和图片附件也会删除。`)) return;
    await api.deleteBatch(batch.id);
    if (selectedBatch?.id === batch.id) setLogs([]);
    await reloadBatches();
    setMessage("草稿批次已删除");
  }

  return (
    <div className="two-column">
      <section className="content-panel">
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>批次</th>
                <th>期间</th>
                <th>状态</th>
                <th>记录数</th>
                <th>金额</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {batches.map((batch) => (
                <tr key={batch.id} onClick={() => setSelectedBatchId(batch.id)}>
                  <td>{batch.name}</td>
                  <td>{batch.start_date} 至 {batch.end_date}</td>
                  <td><StatusPill value={batch.status === "archived" ? "已归档" : "草稿"} /></td>
                  <td>{batch.request_count || 0}</td>
                  <td className="amount">{formatMoney(batch.total_amount || 0)}</td>
                  <td className="action-cell">
                    <button className="ghost-button" onClick={(event) => {
                      event.stopPropagation();
                      window.open(`/api/batches/${batch.id}/export.xlsx`, "_blank");
                    }}>
                      <Download size={15} />
                      导出
                    </button>
                    <button className="ghost-button" onClick={(event) => {
                      event.stopPropagation();
                      loadLogs(batch.id);
                    }}>
                      <History size={15} />
                      日志
                    </button>
                    {batch.status === "draft" && (
                      <button className="danger-button" onClick={(event) => {
                        event.stopPropagation();
                        deleteDraft(batch);
                      }}>
                        <Trash2 size={15} />
                        删除草稿
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="side-panel">
        <button className="primary-button" onClick={archive} disabled={!selectedBatch || selectedBatch.status === "archived"}>
          <Archive size={16} />
          归档当前批次
        </button>
        <div className="section-title">审计日志</div>
        <div className="audit-list">
          {logs.map((log) => (
            <div key={log.id} className="audit-item">
              <strong>{log.action}</strong>
              <span>{log.actor_name || "系统"} · {log.created_at}</span>
              {log.reason && <p>{log.reason}</p>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function AdminView({ setMessage }: { setMessage: (message: string) => void }) {
  const [users, setUsers] = useState<User[]>([]);
  const [userForm, setUserForm] = useState<{ username: string; password: string; role: UserRole; display_name: string; active: boolean }>({ username: "", password: "", role: "business", display_name: "", active: true });
  const [userDrafts, setUserDrafts] = useState<Record<number, { display_name: string; role: UserRole; active: boolean; password: string }>>({});
  const [userQuery, setUserQuery] = useState("");
  const visibleUsers = useMemo(() => {
    const query = userQuery.trim().toLowerCase();
    if (!query) return users;
    return users.filter((item) => {
      const roleLabel = roleLabels[item.role] || item.role;
      return [item.username, item.display_name, item.role, roleLabel].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [users, userQuery]);

  async function load() {
    const userRes = await api.users();
    setUsers(userRes.users);
    setUserDrafts(Object.fromEntries(userRes.users.map((item) => [item.id, { display_name: item.display_name, role: item.role, active: item.active, password: "" }])));
  }

  useEffect(() => {
    load().catch((err) => setMessage((err as Error).message));
  }, []);

  async function createUser() {
    await api.createUser(userForm);
    setUserForm({ username: "", password: "", role: "business", display_name: "", active: true });
    await load();
    setMessage("用户已创建");
  }

  function updateUserDraft(id: number, patch: Partial<{ display_name: string; role: UserRole; active: boolean; password: string }>) {
    const current = userDrafts[id];
    if (!current) return;
    setUserDrafts({ ...userDrafts, [id]: { ...current, ...patch } });
  }

  async function saveUser(item: User) {
    const draft = userDrafts[item.id];
    if (!draft) return;
    await api.updateUser(item.id, {
      display_name: draft.display_name,
      role: draft.role,
      active: draft.active,
      ...(draft.password ? { password: draft.password } : {}),
    });
    await load();
    setMessage(draft.password ? "用户已保存，密码已修改" : "用户已保存");
  }

  async function toggleUserActive(item: User) {
    const nextActive = !item.active;
    if (!window.confirm(`确定${nextActive ? "启用" : "停用"} ${item.username} 吗？`)) return;
    await api.updateUser(item.id, { active: nextActive });
    await load();
    setMessage(nextActive ? "用户已启用" : "用户已停用");
  }

  async function resetPassword(item: User) {
    if (!window.confirm(`确定将 ${item.username} 的密码重置为 123456 吗？`)) return;
    await api.resetUserPassword(item.id);
    await load();
    setMessage("密码已重置为 123456");
  }

  async function deleteUser(item: User) {
    if (!window.confirm(`确定删除 ${item.username} 吗？删除后该用户会从列表隐藏且不能登录。`)) return;
    await api.deleteUser(item.id);
    await load();
    setMessage("用户已删除");
  }

  return (
    <div className="admin-page">
      <section className="content-panel">
        <div className="section-title">用户</div>
        <div className="admin-form">
          <input placeholder="账号" value={userForm.username} onChange={(event) => setUserForm({ ...userForm, username: event.target.value })} />
          <input placeholder="姓名" value={userForm.display_name} onChange={(event) => setUserForm({ ...userForm, display_name: event.target.value })} />
          <input placeholder="初始密码" value={userForm.password} onChange={(event) => setUserForm({ ...userForm, password: event.target.value })} />
          <select value={userForm.role} onChange={(event) => setUserForm({ ...userForm, role: event.target.value as UserRole })}>
            {Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <button className="primary-button" onClick={createUser}><Plus size={16} />新增用户</button>
        </div>
        <div className="admin-user-tools">
          <div className="search-box">
            <Search size={16} />
            <input value={userQuery} onChange={(event) => setUserQuery(event.target.value)} placeholder="搜索账号、姓名、角色" />
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>状态</th><th>修改密码</th><th>操作</th></tr></thead>
            <tbody>
              {visibleUsers.map((item) => {
                const draft = userDrafts[item.id] || { display_name: item.display_name, role: item.role, active: item.active, password: "" };
                return (
                  <tr key={item.id}>
                    <td>{item.username}</td>
                    <td><input value={draft.display_name} onChange={(event) => updateUserDraft(item.id, { display_name: event.target.value })} /></td>
                    <td>
                      <select value={draft.role} onChange={(event) => updateUserDraft(item.id, { role: event.target.value as UserRole })}>
                        {Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </td>
                    <td>
                      <label className="inline-check">
                        <input type="checkbox" checked={draft.active} onChange={(event) => updateUserDraft(item.id, { active: event.target.checked })} />
                        {draft.active ? "启用" : "停用"}
                      </label>
                    </td>
                    <td><input type="password" placeholder="输入新密码，留空不改" value={draft.password} onChange={(event) => updateUserDraft(item.id, { password: event.target.value })} /></td>
                    <td className="table-actions">
                      <button className="ghost-button" type="button" onClick={() => saveUser(item)}>保存</button>
                      <button className="ghost-button" type="button" onClick={() => resetPassword(item)}>重置密码</button>
                      <button className="ghost-button" type="button" onClick={() => toggleUserActive(item)}>{item.active ? "停用" : "启用"}</button>
                      <button className="ghost-button danger-button" type="button" onClick={() => deleteUser(item)}>删除</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function toGridRows(requests: PaymentRequest[]): GridRow[] {
  return requests.map((request) => ({ ...request, __localId: String(request.id) }));
}

function activateButtonByKeyboard(event: KeyboardEvent<HTMLButtonElement>, action: () => void) {
  if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") return;
  event.preventDefault();
  action();
}

function getSheetTabs(rows: GridRow[]): SheetTab[] {
  const counts = new Map<string, { active: number; deleted: number }>();
  rows.forEach((row) => {
    const sheetName = normalizeSheetName(row.source_sheet);
    const count = counts.get(sheetName) || { active: 0, deleted: 0 };
    if (row.__deleted) {
      count.deleted += 1;
    } else {
      count.active += 1;
    }
    counts.set(sheetName, count);
  });
  const sheetTabs = Array.from(counts.entries())
    .sort(([left], [right]) => left.localeCompare(right, "zh-CN"))
    .map(([sheetName, count]) => ({
      key: sheetName,
      label: sheetName,
      count: count.active,
      deletedCount: count.deleted,
      pendingDelete: count.active === 0 && count.deleted > 0,
    }));
  return [{ key: ALL_SHEET, label: "全部", count: rows.filter((row) => !row.__deleted).length }, ...sheetTabs];
}

function nextSheetName(tabs: Array<{ key: string }>) {
  const existingNames = new Set(tabs.filter((tab) => tab.key !== ALL_SHEET).map((tab) => tab.key));
  const baseName = "新Sheet";
  if (!existingNames.has(baseName)) return baseName;
  let index = 2;
  while (existingNames.has(`${baseName} ${index}`)) index += 1;
  return `${baseName} ${index}`;
}

function normalizeSheetName(sheetName?: string) {
  const normalized = String(sheetName || "").trim();
  return normalized || "未分 Sheet";
}

function selectOptionsForField(field: keyof PaymentRequest, currentValue: string) {
  const baseOptions = selectOptionsByField[field];
  if (!baseOptions) return null;
  const options = ["", ...baseOptions];
  if (strictSelectFields.has(field)) return options;
  const value = currentValue.trim();
  if (value && !options.includes(value)) options.push(value);
  return options;
}

function requestFormDirty(original: Partial<PaymentRequest>, draft: Partial<PaymentRequest>, fields: Array<keyof PaymentRequest>) {
  return fields.some((field) => normalizeFormValue(original[field]) !== normalizeFormValue(draft[field]));
}

function normalizeFormValue(value: unknown) {
  if (value === undefined || value === null) return "";
  return String(value);
}

function rowMatchesFilters(
  row: GridRow,
  filters: { q: string; payment_account: string; invoice_status: string; finance_review: string; general_manager_approval: string },
  activeSheet: string,
) {
  if (activeSheet !== ALL_SHEET && normalizeSheetName(row.source_sheet) !== activeSheet) return false;
  const q = filters.q.trim().toLowerCase();
  if (q) {
    const haystack = [
      row.dingding_id,
      row.summary,
      row.payee_name,
      row.payee_account,
      row.project,
      row.expense_type,
      row.general_manager_opinion,
      row.remark,
    ]
      .map((value) => String(value || "").toLowerCase())
      .join(" ");
    if (!haystack.includes(q)) return false;
  }
  if (filters.payment_account.trim() && !String(row.payment_account || "").includes(filters.payment_account.trim())) return false;
  if (filters.invoice_status.trim() && !String(row.invoice_status || "").includes(filters.invoice_status.trim())) return false;
  if (filters.finance_review.trim() && String(row.finance_review || "") !== filters.finance_review.trim()) return false;
  if (filters.general_manager_approval === GENERAL_MANAGER_EMPTY_FILTER && String(row.general_manager_approval || "").trim()) return false;
  if (
    filters.general_manager_approval.trim()
    && filters.general_manager_approval !== GENERAL_MANAGER_EMPTY_FILTER
    && String(row.general_manager_approval || "") !== filters.general_manager_approval.trim()
  ) return false;
  return true;
}

function newLocalId() {
  return `tmp-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function dirtyRowIds(dirtyCells: Set<string>) {
  const ids = new Set<string>();
  dirtyCells.forEach((cell) => ids.add(cell.split(":")[0]));
  return ids;
}

function stripGridRow(row: GridRow): Partial<PaymentRequest> {
  const output: Partial<PaymentRequest> = {};
  gridColumns.forEach((column) => {
    const value = row[column.key];
    if (value !== undefined && value !== null && value !== "") {
      output[column.key] = value as never;
    }
  });
  if (row.currency) output.currency = row.currency;
  if (row.bu) output.bu = row.bu;
  if (row.style_name) output.style_name = row.style_name;
  if (row.source_row) output.source_row = row.source_row;
  return output;
}

function rowHasContent(row: GridRow) {
  return gridColumns.some((column) => {
    const value = row[column.key];
    return value !== undefined && value !== null && String(value).trim() !== "";
  });
}

function normalizeCellValue(column: GridColumn, value: string) {
  if (column.type === "number") return value.trim() === "" ? undefined : Number(value);
  return value;
}

function cellDisplayValue(row: GridRow, column: GridColumn) {
  const value = row[column.key];
  if (value === undefined || value === null) return "";
  return String(value);
}

function wrappedTextRows(value: string, column: GridColumn) {
  const minimumRows = column.key === "summary" ? 4 : 2;
  if (!value.trim()) return minimumRows;
  const charsPerLine = Math.max(8, Math.floor(column.width / 11));
  const visualLines = value
    .split(/\r?\n/)
    .map((line) => Math.max(1, Math.ceil(line.length / charsPerLine)))
    .reduce((sum, count) => sum + count, 0);
  return Math.max(minimumRows, visualLines);
}

function parseClipboardTable(text: string) {
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const lines = normalized.endsWith("\n") ? normalized.slice(0, -1).split("\n") : normalized.split("\n");
  return lines.map((line) => line.split("\t"));
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatusPill({ value }: { value?: string }) {
  return <span className={`status-pill ${value === "已付款" || value === "已归档" ? "done" : ""}`}>{value || "未填写"}</span>;
}

function countAttachmentsByRequest(attachments: AttachmentLink[]) {
  return countAttachmentsByGroup(groupAttachmentsByRequest(attachments));
}

function groupAttachmentsByRequest(attachments: AttachmentLink[]) {
  return attachments.reduce<Record<number, AttachmentLink[]>>((groups, attachment) => {
    if (!groups[attachment.request_id]) groups[attachment.request_id] = [];
    groups[attachment.request_id].push(attachment);
    return groups;
  }, {});
}

function countAttachmentsByGroup(groups: Record<number, AttachmentLink[]>) {
  return Object.fromEntries(Object.entries(groups).map(([requestId, attachments]) => [requestId, attachments.length]));
}

function isImageAttachment(attachment: AttachmentLink) {
  return attachment.attachment_type === "image";
}

function attachmentImageUrl(attachment: AttachmentLink) {
  return attachment.file_url || api.attachmentFileUrl(attachment.id);
}

function attachmentTitle(attachment: AttachmentLink, fallback: string) {
  return attachment.label || attachment.original_filename || fallback;
}

function formatMoney(value: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(value || 0);
}

function formatDateRange(start?: string, end?: string) {
  if (start && end) return `${start} 至 ${end}`;
  if (start) return `${start} 起`;
  if (end) return `截至 ${end}`;
  return "未设置";
}

function formatBatchName(startDate: string, endDate: string) {
  if (!startDate || !endDate) return "";
  return `${compactDate(startDate)}~${compactDate(endDate)}请款明细`;
}

function compactDate(date: string) {
  return date.replace(/-/g, "");
}

function isInvalidDateRange(startDate: string, endDate: string) {
  return Boolean(startDate && endDate && endDate < startDate);
}
