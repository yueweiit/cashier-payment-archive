import { ClipboardEvent, DragEvent, FormEvent, Fragment, KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import {
  AlignLeft,
  AlertTriangle,
  Archive,
  ChevronLeft,
  ChevronRight,
  Download,
  Database,
  FileSpreadsheet,
  Filter,
  History,
  Image as ImageIcon,
  LogOut,
  MoreHorizontal,
  MessageSquareText,
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
import {
  api,
  AttachmentLink,
  AuditLog,
  Batch,
  DingtalkWorkflow,
  ExternalExpenseImportResult,
  ExternalExpensePreview,
  ExternalExpensePreviewFilter,
  ExternalExpensePreviewRow,
  ExternalExpenseResultFilter,
  ExternalExpenseSourceType,
  ExternalSourceSnapshot,
  PaymentRecord,
  PaymentRecordPayload,
  PaymentRequest,
  PaymentSummary,
  PaymentVoucher,
  RolloverCopyMode,
  User,
  UserRole,
  WeeklyMergeApplyResult,
  WeeklyMergePreview,
  WeeklyMergeResolution,
  WeeklyMergeRow,
} from "./api";

type Tab = "workspace" | "archive" | "admin";
type RequestEditorTab = "request" | "approval" | "payments" | "workflow" | "attachments";
type PendingEditorNavigation =
  | { kind: "close" }
  | { kind: "switch"; request: Partial<PaymentRequest>; initialTab: RequestEditorTab };

const emptyRequest: Partial<PaymentRequest> = {
  payment_account: "私户",
  invoice_status: "无票",
  currency: "CNY",
};

const financeApprovalOptions = ["未付款", "部分付款", "已付款"];
const generalManagerApprovalOptions = ["同意付款", "延缓批付", "存在争议"];
const generalManagerApprovalFilterOptions = [...generalManagerApprovalOptions, "无需审批"];
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
  "paid_amount",
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
const calculatedRequestFields = new Set<keyof PaymentRequest>([
  "paid_amount",
  "pending_amount",
  "finance_review",
  "actual_payment_date",
  "payer",
  "payment_status",
]);
const moneyFields = new Set<keyof PaymentRequest>(["amount", "paid_amount", "pending_amount"]);

function isPrivilegedRole(role: UserRole) {
  return role === "admin" || role === "general_manager";
}

function canEditRequestField(role: UserRole, field: keyof PaymentRequest) {
  if (calculatedRequestFields.has(field)) return false;
  if (isPrivilegedRole(role)) return true;
  if (role === "finance") return !generalManagerControlledFields.has(field);
  return !financeControlledFields.has(field) && !generalManagerControlledFields.has(field);
}

const fieldLabels: Record<string, string> = {
  dingding_id: "钉钉申请单号",
  applicant: "申请人",
  payment_account: "付款账户",
  expense_type: "费用性质",
  summary: "摘要",
  style_name: "款式",
  amount: "应付金额",
  paid_amount: "已支付金额",
  pending_amount: "待付款金额（自动计算）",
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
  { key: "applicant", label: "申请人", width: 190 },
  { key: "payment_account", label: "付款账户", width: 110 },
  { key: "expense_type", label: "费用性质", width: 120 },
  { key: "summary", label: "摘要", width: 360 },
  { key: "amount", label: "应付金额", width: 120, type: "number" },
  { key: "paid_amount", label: "已支付金额", width: 120, type: "number" },
  { key: "pending_amount", label: "待付款金额", width: 120, type: "number" },
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
  const [passwordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [accountNotice, setAccountNotice] = useState("");

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

  useEffect(() => {
    if (!accountNotice) return;
    const timer = window.setTimeout(() => setAccountNotice(""), 3000);
    return () => window.clearTimeout(timer);
  }, [accountNotice]);

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
          <button className="app-user account-button" type="button" title="修改密码" aria-label={`${user.display_name}，${roleLabels[user.role]}，修改密码`} onClick={() => setPasswordDialogOpen(true)}>
            <span>{user.display_name}</span>
            <small>{roleLabels[user.role]}</small>
          </button>
          {message && <span className="toast">{message}</span>}
          {accountNotice && <span className="toast account-notice" role="status">{accountNotice}</span>}
          <button className="icon-text" onClick={logout}>
            <LogOut size={15} />
            退出
          </button>
        </div>
      </header>
      <main className="main-pane">
        <header className="topbar">
          <h1>{tabTitle(tab)}</h1>
        </header>
        {tab === "workspace" && (
          <Workspace
            user={user}
            batches={batches}
            selectedBatch={selectedBatch}
            setSelectedBatchId={setSelectedBatchId}
            reloadBatches={loadBatches}
            refreshToken={workspaceRefreshToken}
            onImported={() => setWorkspaceRefreshToken((value) => value + 1)}
            setMessage={setMessage}
          />
        )}
        {tab === "archive" && <ArchiveView user={user} batches={batches} selectedBatch={selectedBatch} setSelectedBatchId={setSelectedBatchId} reloadBatches={loadBatches} setMessage={setMessage} />}
        {tab === "admin" && <AdminView setMessage={setMessage} />}
      </main>
      {passwordDialogOpen && (
        <ChangePasswordDialog
          onClose={() => setPasswordDialogOpen(false)}
          onSuccess={(signedOutSessions) => {
            setPasswordDialogOpen(false);
            setAccountNotice(`密码已修改，其他设备的登录会话已退出${signedOutSessions ? `（${signedOutSessions} 个）` : ""}`);
          }}
        />
      )}
    </div>
  );
}

function ChangePasswordDialog({ onClose, onSuccess }: { onClose: () => void; onSuccess: (signedOutSessions: number) => void }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  function close() {
    if (!submitting) onClose();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!currentPassword || !newPassword || !confirmPassword) {
      setError("当前密码、新密码和确认密码不能为空");
      return;
    }
    if (newPassword.length < 6) {
      setError("新密码至少需要 6 位");
      return;
    }
    if (newPassword !== confirmPassword) {
      setError("两次输入的新密码不一致");
      return;
    }
    if (newPassword === currentPassword) {
      setError("新密码不能与当前密码相同");
      return;
    }

    setSubmitting(true);
    try {
      const result = await api.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
        confirm_password: confirmPassword,
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      onSuccess(result.signed_out_sessions);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal title="修改密码" onClose={close} className="change-password-modal">
      <form className="change-password-form" onSubmit={submit}>
        <p className="form-hint">修改成功后，当前设备保持登录，其他设备将自动退出。</p>
        <label>
          当前密码
          <input type="password" autoComplete="current-password" autoFocus value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} disabled={submitting} />
        </label>
        <label>
          新密码
          <input type="password" autoComplete="new-password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} disabled={submitting} />
          <small>至少 6 位，且不能与当前密码相同。</small>
        </label>
        <label>
          确认新密码
          <input type="password" autoComplete="new-password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} disabled={submitting} />
        </label>
        {error && <p className="error-text" role="alert">{error}</p>}
        <div className="change-password-actions">
          <button className="ghost-button" type="button" onClick={close} disabled={submitting}>取消</button>
          <button className="primary-button" type="submit" disabled={submitting}>
            <Save size={16} />
            {submitting ? "修改中" : "确认修改"}
          </button>
        </div>
      </form>
    </Modal>
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
  hasUnsavedChanges,
  reloadBatches,
  onImported,
  setMessage,
}: {
  selectedBatch: Batch | null;
  hasUnsavedChanges: boolean;
  reloadBatches: () => Promise<void>;
  onImported: () => void;
  setMessage: (message: string) => void;
}) {
  const [weeklyFile, setWeeklyFile] = useState<File | null>(null);
  const [dingtalkFile, setDingtalkFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string> | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mappingOpen, setMappingOpen] = useState(false);
  const [externalImportOpen, setExternalImportOpen] = useState(false);
  const [mergePreview, setMergePreview] = useState<WeeklyMergePreview | null>(null);
  const [busyAction, setBusyAction] = useState<"weekly" | "weekly-merge" | "dingtalk" | "sync-metadata" | "rollback" | null>(null);
  const [weeklyInputKey, setWeeklyInputKey] = useState(0);
  const [dingtalkInputKey, setDingtalkInputKey] = useState(0);
  const silentSyncBatchRef = useRef<number | null>(null);

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

  async function previewWeeklyMerge() {
    if (!weeklyFile || !selectedBatch || hasUnsavedChanges) return;
    setBusyAction("weekly-merge");
    setMessage("");
    try {
      const preview = await api.previewWeeklyMerge(weeklyFile, selectedBatch.id);
      setMergePreview(preview);
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
      setMessage(
        `已撤回最近导入：删除 ${res.deleted_requests} 条请款、${res.deleted_payments} 笔付款、${res.deleted_attachments + res.deleted_payment_vouchers} 个附件/凭证`,
      );
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setBusyAction(null);
    }
  }

  async function syncExternalMetadata(silent = false) {
    if (!selectedBatch || selectedBatch.status !== "draft" || hasUnsavedChanges || busyAction !== null) return;
    if (!silent) {
      setMessage("");
      setBusyAction("sync-metadata");
    }
    try {
      const result = await api.syncExternalExpenseMetadata(selectedBatch.id, silent ? 300 : 0);
      if (result.status === "fresh") return;
      await reloadBatches();
      onImported();
      if (!silent) {
        const detail = result.auto_payment_mode === "preview"
          ? `发现 ${result.payment_candidates} 条自动付款候选、${result.review_required} 条待核对`
          : `新增 ${result.auto_payments} 笔自动付款、${result.review_required} 条待核对`;
        const attachmentDetail = `附件新增 ${result.attachment_synced || 0} 个、已存在 ${result.attachment_existing || 0} 个${result.attachment_failed ? `、失败 ${result.attachment_failed} 个` : ""}`;
        setMessage(`钉钉流程同步完成：${detail}；${attachmentDetail}`);
        window.setTimeout(() => setMessage(""), 3000);
      }
    } catch (err) {
      if (!silent) setMessage((err as Error).message);
    } finally {
      if (!silent) setBusyAction(null);
    }
  }

  useEffect(() => {
    if (
      !selectedBatch
      || selectedBatch.status !== "draft"
      || hasUnsavedChanges
      || busyAction !== null
      || silentSyncBatchRef.current === selectedBatch.id
    ) return;
    silentSyncBatchRef.current = selectedBatch.id;
    void syncExternalMetadata(true);
  }, [selectedBatch?.id, selectedBatch?.status, hasUnsavedChanges]);

  return (
    <>
      <section className="import-toolbar-panel" aria-label="数据导入">
        <div className="import-toolbar-title">
          <strong>数据导入</strong>
          <small>导入本批次数据</small>
        </div>
        <div className="topbar-import">
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
              {busyAction === "weekly" ? "导入中" : "新增导入"}
            </button>
            <button
              className="ghost-button compact-import-button"
              type="button"
              onClick={previewWeeklyMerge}
              disabled={!weeklyFile || !selectedBatch || hasUnsavedChanges || busyAction !== null}
              title={hasUnsavedChanges ? "请先保存或放弃未保存修改" : "合并系统导出后人工维护的 Excel"}
            >
              <RefreshCcw size={15} />
              {busyAction === "weekly-merge" ? "解析中" : "合并更新"}
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
          <div className="topbar-import-group external-source-import-group">
            <button
              className="ghost-button compact-import-button"
              type="button"
              onClick={() => setExternalImportOpen(true)}
              disabled={!selectedBatch || selectedBatch.status !== "draft" || hasUnsavedChanges || busyAction !== null}
              title={hasUnsavedChanges ? "请先保存或放弃未保存修改" : selectedBatch?.status === "archived" ? "只能向草稿批次导入" : "从钉钉支出中间表拉取"}
            >
              <Database size={15} />
              从中间表拉取
            </button>
            <button
              className="ghost-button compact-import-button"
              type="button"
              onClick={() => void syncExternalMetadata(false)}
              disabled={!selectedBatch || selectedBatch.status !== "draft" || hasUnsavedChanges || busyAction !== null}
              title={hasUnsavedChanges ? "请先保存或放弃未保存修改" : selectedBatch?.status === "archived" ? "只能同步草稿批次" : "刷新审批状态、流程评论和可信付款证据"}
            >
              <RefreshCcw size={15} />
              {busyAction === "sync-metadata" ? "同步中" : "同步钉钉流程"}
            </button>
          </div>
          <div className="topbar-import-group rollback-import-group">
            <button className="ghost-button danger-button compact-import-button" type="button" onClick={rollbackLatestImport} disabled={!selectedBatch || busyAction !== null}>
              <Undo2 size={15} />
              {busyAction === "rollback" ? "撤回中" : "撤回最近导入"}
            </button>
          </div>
        </div>
      </section>
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
      {externalImportOpen && selectedBatch && (
        <ExternalExpenseImportDialog
          batch={selectedBatch}
          onClose={() => setExternalImportOpen(false)}
          onImported={async () => {
            await reloadBatches();
            onImported();
          }}
          setMessage={setMessage}
        />
      )}
      {mergePreview && selectedBatch && (
        <WeeklyMergeDialog
          batch={selectedBatch}
          preview={mergePreview}
          onClose={() => setMergePreview(null)}
          onApplied={async (result) => {
            setMergePreview(null);
            setWeeklyFile(null);
            setWeeklyInputKey((value) => value + 1);
            await reloadBatches();
            onImported();
            setMessage(
              `合并完成：新增 ${result.summary.create} 条、更新 ${result.summary.update} 条、付款变化 ${result.summary.payment} 条`,
            );
          }}
        />
      )}
    </>
  );
}

type WeeklyMergeFilter = "all" | "create" | "update" | "payment" | "unchanged" | "conflict" | "warning";

function mergeActionLabel(action: WeeklyMergeRow["action"]) {
  return {
    create: "新增",
    update: "更新",
    unchanged: "无变化",
    conflict: "冲突",
    skip: "跳过",
  }[action];
}

function mergeDisplayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "空";
  if (typeof value === "number") return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return String(value);
}

function WeeklyMergeDialog({
  batch,
  preview,
  onClose,
  onApplied,
}: {
  batch: Batch;
  preview: WeeklyMergePreview;
  onClose: () => void;
  onApplied: (result: WeeklyMergeApplyResult) => Promise<void>;
}) {
  const [filter, setFilter] = useState<WeeklyMergeFilter>("all");
  const [resolutions, setResolutions] = useState<Record<string, WeeklyMergeResolution>>({});
  const [paymentDates, setPaymentDates] = useState<Record<string, string>>({});
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const filteredRows = useMemo(
    () =>
      preview.rows.filter((row) => {
        if (filter === "all") return true;
        if (filter === "payment") return row.payment_change;
        if (filter === "warning") return row.warnings.length > 0;
        if (filter === "unchanged") return row.action === "unchanged" || row.action === "skip";
        return row.action === filter;
      }),
    [filter, preview.rows],
  );
  const conflictRows = preview.rows.filter((row) => row.action === "conflict");
  const hardConflicts = conflictRows.filter((row) => row.candidates.length === 0);
  const unresolvedChoices = conflictRows.filter((row) => row.candidates.length > 0 && !resolutions[row.row_id]);
  const requiredDateKeys = Array.from(new Set(preview.rows.flatMap((row) => row.payment_date_keys)));
  const missingDateKeys = requiredDateKeys.filter((key) => !paymentDates[key]);
  const archivedReasonMissing = batch.status === "archived" && !reason.trim();
  const canContinue =
    hardConflicts.length === 0
    && unresolvedChoices.length === 0
    && missingDateKeys.length === 0
    && !archivedReasonMissing;

  function changeResolution(row: WeeklyMergeRow, value: string) {
    setConfirming(false);
    setResolutions((current) => {
      const next = { ...current };
      if (!value) {
        delete next[row.row_id];
      } else if (value === "create" || value === "skip") {
        next[row.row_id] = { row_id: row.row_id, action: value };
      } else {
        next[row.row_id] = { row_id: row.row_id, action: "update", request_id: Number(value) };
      }
      return next;
    });
  }

  async function applyMerge() {
    if (!canContinue || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await api.applyWeeklyMerge(preview.job_id, {
        resolutions: Object.values(resolutions),
        payment_dates: paymentDates,
        reason: reason.trim() || undefined,
      });
      await onApplied(result);
    } catch (err) {
      setError((err as Error).message);
      setConfirming(false);
    } finally {
      setSubmitting(false);
    }
  }

  const filters: Array<{ key: WeeklyMergeFilter; label: string; count: number }> = [
    { key: "all", label: "全部", count: preview.rows.length },
    { key: "create", label: "新增", count: preview.summary.create },
    { key: "update", label: "更新", count: preview.summary.update },
    { key: "payment", label: "付款变化", count: preview.summary.payment },
    { key: "unchanged", label: "无变化", count: preview.summary.unchanged },
    { key: "conflict", label: "冲突", count: preview.summary.conflict },
    { key: "warning", label: "警告", count: preview.summary.warning },
  ];

  return (
    <Modal title="Excel 合并更新预览" onClose={() => { if (!submitting) onClose(); }} className="weekly-merge-modal">
      <div className="weekly-merge-intro">
        <span>批次：{batch.name}</span>
        <span>格式：{preview.format_version >= 2 ? "新版精确标识" : "旧版兼容匹配"}</span>
        <span>Excel 未出现的系统记录、附件和付款凭证会保留。</span>
      </div>
      <div className="weekly-merge-filters" role="group" aria-label="合并结果筛选">
        {filters.map((item) => (
          <button
            key={item.key}
            className={`external-preview-filter${filter === item.key ? " active" : ""}${item.key === "conflict" && item.count ? " danger" : ""}`}
            type="button"
            onClick={() => setFilter(item.key)}
          >
            {item.label} {item.count}
          </button>
        ))}
      </div>
      {error && <div className="form-error">{error}</div>}
      <div className="weekly-merge-table-wrap">
        <table className="weekly-merge-table">
          <thead>
            <tr>
              <th>处理</th>
              <th>请款标识 / 钉钉号</th>
              <th>申请人 / 来源 Sheet</th>
              <th>应付 / 已付变化</th>
              <th>字段差异</th>
              <th>校验与处理</th>
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row) => (
              <tr key={row.row_id} className={row.action === "conflict" ? "merge-conflict-row" : row.warnings.length ? "merge-warning-row" : ""}>
                <td>
                  <span className={`merge-action-badge ${row.action}`}>{mergeActionLabel(row.action)}</span>
                  {row.payment_change && <small>付款变化</small>}
                  {row.attachment_change && <small>新增附件</small>}
                </td>
                <td>
                  <strong>{row.incoming_request_id || row.request_id || "新增"}</strong>
                  <small>{row.dingding_id || "无钉钉号"}</small>
                </td>
                <td>
                  <strong>{row.applicant || "未填写"}</strong>
                  <small>{row.source_sheet || "未分 Sheet"}</small>
                </td>
                <td>
                  <span>应付 ¥{Number(row.amount || 0).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}</span>
                  <small>
                    已付 ¥{row.old_paid_amount.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
                    {row.old_paid_amount !== row.new_paid_amount && ` → ¥${row.new_paid_amount.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`}
                  </small>
                </td>
                <td>
                  {row.changes.length === 0 ? (
                    <span className="muted">无字段变化</span>
                  ) : (
                    <div className="merge-change-list">
                      {row.changes.slice(0, 5).map((change) => (
                        <span key={change.field} title={`${mergeDisplayValue(change.old)} → ${mergeDisplayValue(change.new)}`}>
                          {change.label}：{mergeDisplayValue(change.old)} → {mergeDisplayValue(change.new)}
                        </span>
                      ))}
                      {row.changes.length > 5 && <span>另有 {row.changes.length - 5} 项</span>}
                    </div>
                  )}
                </td>
                <td>
                  {row.errors.map((message) => <div className="merge-error-text" key={message}>{message}</div>)}
                  {row.warnings.map((message) => <div className="merge-warning-text" key={message}>{message}</div>)}
                  {row.candidates.length > 0 && row.action === "conflict" && (
                    <select
                      value={resolutions[row.row_id]?.action === "update" ? String(resolutions[row.row_id]?.request_id) : resolutions[row.row_id]?.action || ""}
                      onChange={(event) => changeResolution(row, event.target.value)}
                    >
                      <option value="">请选择处理方式</option>
                      {row.candidates.map((candidate) => (
                        <option value={candidate.id} key={candidate.id}>
                          关联 #{candidate.id} · {candidate.applicant || candidate.dingding_id || "未命名"} · ¥{Number(candidate.amount || 0).toLocaleString()}
                        </option>
                      ))}
                      <option value="create">作为新增</option>
                      <option value="skip">跳过此行</option>
                    </select>
                  )}
                  {row.payment_date_keys.map((key) => (
                    <label className="merge-payment-date" key={key}>
                      补充付款日期
                      <input
                        type="date"
                        value={paymentDates[key] || ""}
                        onChange={(event) => {
                          setConfirming(false);
                          setPaymentDates((current) => ({ ...current, [key]: event.target.value }));
                        }}
                      />
                    </label>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.sheet_order.changed && (
        <div className="merge-sheet-order-note">
          Sheet 顺序将按 Excel 更新；系统中独有的 Sheet 会保留并追加在末尾。
        </div>
      )}
      {batch.status === "archived" && (
        <label className="merge-archive-reason">
          归档更正原因
          <textarea value={reason} onChange={(event) => { setReason(event.target.value); setConfirming(false); }} placeholder="归档批次提交合并时必填" />
        </label>
      )}
      {confirming && (
        <div className="merge-confirm-summary">
          <strong>确认本次合并</strong>
          <span>
            将新增 {preview.summary.create} 条、更新 {preview.summary.update} 条、处理 {preview.summary.payment} 条付款变化；
            无变化及 Excel 缺失的系统记录均保留。
          </span>
        </div>
      )}
      <div className="drawer-actions weekly-merge-actions">
        <span className="muted">
          {hardConflicts.length > 0
            ? `仍有 ${hardConflicts.length} 个不可提交冲突`
            : unresolvedChoices.length > 0
              ? `仍有 ${unresolvedChoices.length} 项需要选择`
              : missingDateKeys.length > 0
                ? `仍缺 ${missingDateKeys.length} 个付款日期`
                : "校验已通过"}
        </span>
        <button className="ghost-button" type="button" onClick={onClose} disabled={submitting}>取消</button>
        {!confirming ? (
          <button className="primary-button" type="button" onClick={() => setConfirming(true)} disabled={!canContinue || submitting}>
            核对并继续
          </button>
        ) : (
          <button className="primary-button" type="button" onClick={applyMerge} disabled={!canContinue || submitting}>
            <Save size={16} />
            {submitting ? "合并中" : "确认合并"}
          </button>
        )}
      </div>
    </Modal>
  );
}

function ExternalExpenseImportDialog({
  batch,
  onClose,
  onImported,
  setMessage,
}: {
  batch: Batch;
  onClose: () => void;
  onImported: () => Promise<void>;
  setMessage: (message: string) => void;
}) {
  const defaultDates = externalImportDefaultDates(batch);
  const [dateFrom, setDateFrom] = useState(defaultDates.dateFrom);
  const [dateTo, setDateTo] = useState(defaultDates.dateTo);
  const [sourceTypes, setSourceTypes] = useState<ExternalExpenseSourceType[]>(["operation", "purchase"]);
  const [approvalNo, setApprovalNo] = useState("");
  const [applicantIds, setApplicantIds] = useState<string[]>([]);
  const [applicantQuery, setApplicantQuery] = useState("");
  const [preview, setPreview] = useState<ExternalExpensePreview | null>(null);
  const [resultFilter, setResultFilter] = useState<ExternalExpenseResultFilter>("matched");
  const [previewPage, setPreviewPage] = useState(1);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ExternalExpenseImportResult | null>(null);

  useEffect(() => {
    void queryPreview(true);
  }, []);

  function rowKey(row: Pick<ExternalExpensePreviewRow, "source_type" | "source_id">) {
    return `${row.source_type}:${row.source_id}`;
  }

  function validateFilters() {
    if (!sourceTypes.length) return "请至少选择一个支出来源";
    if (approvalNo.trim()) return "";
    if (!dateFrom || !dateTo) return "请选择申请开始和结束日期";
    const start = new Date(`${dateFrom}T00:00:00`);
    const end = new Date(`${dateTo}T00:00:00`);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return "申请日期格式无效";
    if (end < start) return "申请结束日期不能早于开始日期";
    const days = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
    if (days > 31) return "单次查询的申请日期范围不能超过 31 天";
    return "";
  }

  async function queryPreview(resetSelection = false) {
    const validationError = validateFilters();
    if (validationError) {
      setError(validationError);
      return;
    }
    setLoading(true);
    setError("");
    if (resetSelection) setResult(null);
    try {
      const payload: ExternalExpensePreviewFilter = {
        batch_id: batch.id,
        date_from: dateFrom,
        date_to: dateTo,
        source_types: sourceTypes,
        approval_no: approvalNo.trim(),
        applicant_ids: applicantIds,
        result_filter: "matched",
        page: 1,
        page_size: 50,
      };
      const response = await api.previewExternalExpenses(payload);
      setPreview(response);
      setPreviewPage(1);
      if (resetSelection) {
        setResultFilter("matched");
        setSelectedKeys(new Set());
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  function toggleSource(sourceType: ExternalExpenseSourceType) {
    setSourceTypes((current) => current.includes(sourceType) ? current.filter((value) => value !== sourceType) : [...current, sourceType]);
  }

  function toggleApplicant(id: string) {
    setApplicantIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  }

  function toggleRow(row: ExternalExpensePreviewRow) {
    if (!row.importable) return;
    const key = rowKey(row);
    setSelectedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleCurrentPage() {
    if (!preview) return;
    const keys = pagePreviewRows.filter((row) => row.importable).map(rowKey);
    const allSelected = keys.length > 0 && keys.every((key) => selectedKeys.has(key));
    setSelectedKeys((current) => {
      const next = new Set(current);
      keys.forEach((key) => allSelected ? next.delete(key) : next.add(key));
      return next;
    });
  }

  async function importSelected() {
    if (!selectedKeys.size || selectedKeys.size > 200) return;
    setImporting(true);
    setError("");
    setResult(null);
    try {
      const items = Array.from(selectedKeys).map((key) => {
        const separator = key.indexOf(":");
        return {
          source_type: key.slice(0, separator) as ExternalExpenseSourceType,
          source_id: key.slice(separator + 1),
        };
      });
      const response = await api.importExternalExpenses(batch.id, items);
      setResult(response);
      setSelectedKeys(new Set());
      await onImported();
      await queryPreview(false);
      const summaryMessage = `中间表导入完成：新增 ${response.imported_rows} 条，重复 ${response.duplicate_rows} 条，无效 ${response.invalid_rows} 条`;
      setMessage(summaryMessage);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setImporting(false);
    }
  }

  const filteredApplicantOptions = (preview?.applicant_options || []).filter((option) => {
    const query = applicantQuery.trim().toLowerCase();
    return option.name.toLowerCase().includes(query) || String(option.department || "").toLowerCase().includes(query);
  });
  const allPreviewRows = preview?.all_rows || preview?.rows || [];
  const filteredPreviewRows = allPreviewRows.filter((row) => {
    if (resultFilter === "importable") return row.importable;
    if (resultFilter === "duplicates") return row.duplicate != null;
    if (resultFilter === "warnings") return row.warnings.length > 0;
    if (resultFilter === "invalid") return row.errors.length > 0;
    return true;
  });
  const previewPageSize = 50;
  const previewTotalPages = Math.max(1, Math.ceil(filteredPreviewRows.length / previewPageSize));
  const currentPreviewPage = Math.min(previewPage, previewTotalPages);
  const pagePreviewRows = filteredPreviewRows.slice(
    (currentPreviewPage - 1) * previewPageSize,
    currentPreviewPage * previewPageSize,
  );
  const pageImportableKeys = pagePreviewRows.filter((row) => row.importable).map(rowKey);
  const allPageSelected = pageImportableKeys.length > 0 && pageImportableKeys.every((key) => selectedKeys.has(key));
  const resultFilterOptions: Array<{ value: ExternalExpenseResultFilter; label: string; count: number }> = preview ? [
    { value: "matched", label: "匹配", count: preview.summary.matched },
    { value: "importable", label: "可导入", count: preview.summary.importable },
    { value: "duplicates", label: "已存在", count: preview.summary.duplicates },
    { value: "warnings", label: "有警告", count: preview.summary.warnings },
    { value: "invalid", label: "不可导入", count: preview.summary.invalid },
  ] : [];

  return (
    <Modal title="从钉钉支出中间表拉取" onClose={() => { if (!importing) onClose(); }} className="external-expense-modal">
      <form className="external-expense-filters" onSubmit={(event) => { event.preventDefault(); void queryPreview(true); }}>
        <div className="external-source-selector">
          <span>支出来源</span>
          <label><input type="checkbox" checked={sourceTypes.includes("operation")} onChange={() => toggleSource("operation")} />运营支出</label>
          <label><input type="checkbox" checked={sourceTypes.includes("purchase")} onChange={() => toggleSource("purchase")} />采购支出</label>
        </div>
        <label title={approvalNo.trim() ? "已按钉钉单号精确查询，日期范围不参与筛选" : undefined}>申请开始日期<input type="date" value={dateFrom} disabled={Boolean(approvalNo.trim())} onChange={(event) => setDateFrom(event.target.value)} /></label>
        <label title={approvalNo.trim() ? "已按钉钉单号精确查询，日期范围不参与筛选" : undefined}>申请结束日期<input type="date" value={dateTo} disabled={Boolean(approvalNo.trim())} onChange={(event) => setDateTo(event.target.value)} /></label>
        <label>钉钉单号<input value={approvalNo} onChange={(event) => { setApprovalNo(event.target.value); if (event.target.value.trim()) setError(""); }} placeholder="精确匹配，忽略日期" /></label>
        <div className="external-applicant-picker">
          <label>申请人<input value={applicantQuery} onChange={(event) => setApplicantQuery(event.target.value)} placeholder="搜索并多选申请人" /></label>
          <div className="external-applicant-options">
            {filteredApplicantOptions.length === 0 && <span>查询后显示申请人选项</span>}
            {filteredApplicantOptions.map((option) => (
              <label key={option.id} title={`钉钉用户 ID：${option.id}`}>
                <input type="checkbox" checked={applicantIds.includes(option.id)} onChange={() => toggleApplicant(option.id)} />
                <span className="external-applicant-option-text">
                  <strong>{option.name}</strong>
                  <small>{option.department || "部门未知"}</small>
                </span>
                <small>{option.count}</small>
              </label>
            ))}
          </div>
        </div>
        <button className="primary-button external-query-button" type="submit" disabled={loading || importing}>
          <Search size={16} />{loading ? "查询中" : "查询"}
        </button>
      </form>

      {applicantIds.length > 0 && (
        <div className="external-selected-applicants">
          {applicantIds.map((id) => {
            const option = preview?.applicant_options.find((candidate) => candidate.id === id);
            return <button type="button" key={id} onClick={() => toggleApplicant(id)} title={`钉钉用户 ID：${id}`}>{option?.name || "未识别人员"} ×</button>;
          })}
        </div>
      )}
      {error && <p className="error-text external-import-error">{error}</p>}
      {result && (
        <div className="external-import-result">
          已导入 <strong>{result.imported_rows}</strong> 条，跳过重复 <strong>{result.duplicate_rows}</strong> 条，无效 <strong>{result.invalid_rows}</strong> 条，含警告 <strong>{result.warnings}</strong> 条。
        </div>
      )}
      {preview && (
        <>
          <div className="external-preview-summary">
            {resultFilterOptions.map((option) => (
              <button
                className={`external-preview-filter${resultFilter === option.value ? " active" : ""}`}
                type="button"
                key={option.value}
                aria-pressed={resultFilter === option.value}
                disabled={loading || importing}
                onClick={() => {
                  setResultFilter(option.value);
                  setPreviewPage(1);
                }}
              >
                {option.label} <strong>{option.count}</strong>
              </button>
            ))}
          </div>
          <div className="external-preview-table-wrap">
            <table className="data-table external-preview-table">
              <thead>
                <tr>
                  <th className="external-select-col"><input type="checkbox" aria-label="选择当前页可导入记录" checked={allPageSelected} onChange={toggleCurrentPage} /></th>
                  <th>来源</th><th>申请日期</th><th>钉钉单号</th><th>申请人</th><th>状态</th><th>摘要</th><th>金额</th><th>收款信息</th><th>校验结果</th>
                </tr>
              </thead>
              <tbody>
                {pagePreviewRows.length === 0 && <tr><td colSpan={10} className="external-empty-row">没有符合条件的记录</td></tr>}
                {pagePreviewRows.map((row) => (
                  <tr key={rowKey(row)} className={!row.importable ? "external-row-disabled" : row.warnings.length ? "external-row-warning" : ""}>
                    <td className="external-select-col"><input type="checkbox" aria-label={`选择 ${row.approval_no}`} disabled={!row.importable} checked={selectedKeys.has(rowKey(row))} onChange={() => toggleRow(row)} /></td>
                    <td>{row.source_label}</td>
                    <td>{row.application_date || "—"}</td>
                    <td className="mono">{row.approval_no || "—"}</td>
                    <td title={row.applicant_id ? `钉钉用户 ID：${row.applicant_id}` : undefined}><strong>{row.applicant || "—"}</strong><small>{row.applicant_department || ""}</small></td>
                    <td><ExternalApprovalBadge status={row.approval_status} result={row.approval_result} /></td>
                    <td className="external-summary-cell" title={row.summary}>{row.summary || "—"}</td>
                    <td className="amount">{row.amount === undefined || row.amount === null ? "—" : formatMoney(row.amount)}</td>
                    <td className="external-beneficiary-cell" title={row.beneficiary}>{row.beneficiary || "—"}</td>
                    <td><ExternalExpenseValidation row={row} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="external-pagination">
            <span>第 {currentPreviewPage} / {previewTotalPages} 页，共 {filteredPreviewRows.length} 条</span>
            <div>
              <button className="ghost-button" type="button" disabled={loading || currentPreviewPage <= 1} onClick={() => setPreviewPage(currentPreviewPage - 1)}><ChevronLeft size={15} />上一页</button>
              <button className="ghost-button" type="button" disabled={loading || currentPreviewPage >= previewTotalPages} onClick={() => setPreviewPage(currentPreviewPage + 1)}>下一页<ChevronRight size={15} /></button>
            </div>
          </div>
        </>
      )}
      <div className="external-import-actions">
        <span>{selectedKeys.size > 200 ? "单次最多选择 200 条" : `已选择 ${selectedKeys.size} 条`}</span>
        <button className="primary-button" type="button" onClick={importSelected} disabled={importing || selectedKeys.size === 0 || selectedKeys.size > 200}>
          <Download size={16} />{importing ? "导入中" : `导入选中 ${selectedKeys.size} 条`}
        </button>
      </div>
    </Modal>
  );
}

function ExternalExpenseValidation({ row }: { row: ExternalExpensePreviewRow }) {
  if (row.duplicate) return <span className="external-validation duplicate">已存在：{row.duplicate.batch_name}</span>;
  if (row.errors.length) return <span className="external-validation invalid">{row.errors.join("；")}</span>;
  if (row.warnings.length) return <span className="external-validation warning"><AlertTriangle size={13} />{row.warnings.join("；")}</span>;
  return <span className="external-validation ready">可导入</span>;
}

function Workspace({
  user,
  batches,
  selectedBatch,
  setSelectedBatchId,
  reloadBatches,
  refreshToken,
  onImported,
  setMessage,
}: {
  user: User;
  batches: Batch[];
  selectedBatch: Batch | null;
  setSelectedBatchId: (id: number | null) => void;
  reloadBatches: () => Promise<void>;
  refreshToken: number;
  onImported: () => void;
  setMessage: (message: string) => void;
}) {
  const [gridRows, setGridRows] = useState<GridRow[]>([]);
  const [dirtyCells, setDirtyCells] = useState<Set<string>>(new Set());
  const [deletedLocalIds, setDeletedLocalIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState({ q: "", payment_account: "", invoice_status: "", finance_review: "", general_manager_approval: "" });
  const [activeSheet, setActiveSheet] = useState(ALL_SHEET);
  const [editingSheet, setEditingSheet] = useState<{ key: string; value: string } | null>(null);
  const [newSheetName, setNewSheetName] = useState<string | null>(null);
  const [sheetOrder, setSheetOrder] = useState<string[]>([]);
  const [draggedSheet, setDraggedSheet] = useState<string | null>(null);
  const [sheetDropTarget, setSheetDropTarget] = useState<{ key: string; position: "before" | "after" } | null>(null);
  const [sheetOrderSaving, setSheetOrderSaving] = useState(false);
  const [wrapText, setWrapText] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [rolloverDialogOpen, setRolloverDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Partial<PaymentRequest> | null>(null);
  const [editorDraft, setEditorDraft] = useState<Partial<PaymentRequest> | null>(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [attachmentCounts, setAttachmentCounts] = useState<Record<number, number>>({});
  const [selectedRows, setSelectedRows] = useState<number[]>([]);
  const [editorInitialTab, setEditorInitialTab] = useState<RequestEditorTab>("request");
  const [pendingEditorNavigation, setPendingEditorNavigation] = useState<PendingEditorNavigation | null>(null);
  const [editorNavigationBusy, setEditorNavigationBusy] = useState(false);
  const [batchMenuOpen, setBatchMenuOpen] = useState(false);
  const batchMenuRef = useRef<HTMLDivElement | null>(null);
  const [reason, setReason] = useState("");
  const hasUnsavedChanges = dirtyCells.size > 0 || deletedLocalIds.size > 0;

  async function loadRequests() {
    if (!selectedBatch) return;
    const [requestsRes, attachmentsRes] = await Promise.all([api.requests(selectedBatch.id, {}), api.batchAttachments(selectedBatch.id)]);
    const attachmentGroups = groupAttachmentsByRequest(attachmentsRes.attachments);
    setGridRows(toGridRows(requestsRes.requests));
    setAttachmentCounts(countAttachmentsByGroup(attachmentGroups));
    setDirtyCells(new Set());
    setDeletedLocalIds(new Set());
    setSelectedRows([]);
  }

  async function refreshAttachmentCounts() {
    if (!selectedBatch) {
      setAttachmentCounts({});
      return;
    }
    const res = await api.batchAttachments(selectedBatch.id);
    const attachmentGroups = groupAttachmentsByRequest(res.attachments);
    setAttachmentCounts(countAttachmentsByGroup(attachmentGroups));
  }

  useEffect(() => {
    loadRequests().catch((err) => setMessage((err as Error).message));
  }, [selectedBatch?.id, refreshToken]);

  useEffect(() => {
    setSheetOrder(selectedBatch?.sheet_order || []);
    setDraggedSheet(null);
    setSheetDropTarget(null);
  }, [selectedBatch?.id, selectedBatch?.sheet_order]);

  const sheetTabs = useMemo(() => getSheetTabs(gridRows, sheetOrder), [gridRows, sheetOrder]);
  const visibleRows = useMemo(() => gridRows.filter((row) => rowMatchesFilters(row, filters, activeSheet)), [gridRows, filters, activeSheet]);
  const visibleActiveRows = useMemo(() => visibleRows.filter((row) => !row.__deleted), [visibleRows]);
  const hasActiveExportFilter = activeSheet !== ALL_SHEET || Object.values(filters).some((value) => value.trim());
  const activeSheetRows = useMemo(
    () => (activeSheet === ALL_SHEET ? [] : gridRows.filter((row) => normalizeSheetName(row.source_sheet) === activeSheet)),
    [activeSheet, gridRows],
  );
  const visibleTotals = useMemo(
    () => ({
      count: visibleActiveRows.length,
      amount: visibleActiveRows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0),
      paidAmount: visibleActiveRows.reduce((sum, row) => sum + (Number(row.paid_amount) || 0), 0),
      pendingAmount: visibleActiveRows.reduce((sum, row) => sum + (Number(row.pending_amount) || 0), 0),
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
  const canManageDraftState = selectedBatch?.status === "draft" && isPrivilegedRole(user.role);
  const canEditGrid = selectedBatch?.status !== "archived" || isPrivilegedRole(user.role);
  const canReorderSheets = canEditGrid && !hasUnsavedChanges && !sheetOrderSaving;
  const batchPayableAmount = Number(selectedBatch?.total_amount) || 0;
  const batchPaidAmount = Number(selectedBatch?.total_paid_amount) || 0;
  const paymentProgress = batchPayableAmount > 0
    ? Math.min(100, Math.max(0, (batchPaidAmount / batchPayableAmount) * 100))
    : 0;
  const activeSheetPendingDeleteCount = activeSheetRows.filter((row) => row.__deleted).length;
  const canDeleteActiveSheet = canEditGrid && activeSheet !== ALL_SHEET && activeSheetRows.some((row) => !row.__deleted);
  const canRestoreActiveSheetDelete = canEditGrid && activeSheet !== ALL_SHEET && activeSheetRows.some((row) => row.__deleted);

  function handleSheetDragStart(event: DragEvent<HTMLButtonElement>, sheetKey: string) {
    if (!canReorderSheets || sheetKey === ALL_SHEET) {
      event.preventDefault();
      return;
    }
    setDraggedSheet(sheetKey);
    setActiveSheet(sheetKey);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", sheetKey);
  }

  function handleSheetDragOver(event: DragEvent<HTMLButtonElement>, sheetKey: string) {
    if (!draggedSheet || sheetKey === ALL_SHEET || sheetKey === draggedSheet) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    const bounds = event.currentTarget.getBoundingClientRect();
    const position = event.clientX < bounds.left + bounds.width / 2 ? "before" : "after";
    setSheetDropTarget({ key: sheetKey, position });
  }

  async function handleSheetDrop(event: DragEvent<HTMLButtonElement>, targetKey: string) {
    event.preventDefault();
    const sourceKey = draggedSheet || event.dataTransfer.getData("text/plain");
    const position = sheetDropTarget?.key === targetKey ? sheetDropTarget.position : "before";
    setDraggedSheet(null);
    setSheetDropTarget(null);
    if (!selectedBatch || !sourceKey || sourceKey === targetKey || !canReorderSheets) return;

    const previousOrder = sheetTabs.filter((tab) => tab.key !== ALL_SHEET).map((tab) => tab.key);
    const nextOrder = previousOrder.filter((key) => key !== sourceKey);
    const targetIndex = nextOrder.indexOf(targetKey);
    if (targetIndex < 0) return;
    nextOrder.splice(targetIndex + (position === "after" ? 1 : 0), 0, sourceKey);
    if (nextOrder.every((key, index) => key === previousOrder[index])) return;

    setSheetOrder(nextOrder);
    setSheetOrderSaving(true);
    try {
      await api.updateSheetOrder(selectedBatch.id, nextOrder);
      await reloadBatches();
      setMessage("Sheet 顺序已保存");
    } catch (error) {
      setSheetOrder(previousOrder);
      setMessage((error as Error).message);
    } finally {
      setSheetOrderSaving(false);
    }
  }

  function handleSheetDragEnd() {
    setDraggedSheet(null);
    setSheetDropTarget(null);
  }

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

  useEffect(() => {
    if (!batchMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!batchMenuRef.current?.contains(event.target as Node)) setBatchMenuOpen(false);
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setBatchMenuOpen(false);
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [batchMenuOpen]);

  function guardedSelectBatch(id: number) {
    if (hasUnsavedChanges && !window.confirm("当前表格有未保存更改，切换批次会丢失这些更改。继续切换吗？")) return;
    setSelectedBatchId(id);
  }

  async function saveRequest(payload: Partial<PaymentRequest>) {
    if (!selectedBatch) return;
    const writablePayload = withoutDerivedPaymentFields(payload);
    let savedRequest: PaymentRequest;
    if (payload.id) {
      const result = await api.updateRequest(selectedBatch.id, payload.id, { ...writablePayload, reason });
      savedRequest = result.request;
    } else {
      const result = await api.createRequest(selectedBatch.id, writablePayload);
      savedRequest = result.request;
    }
    setEditing(savedRequest);
    setEditorDraft(null);
    setEditorDirty(false);
    setReason("");
    await loadRequests();
    await reloadBatches();
    setMessage("已保存");
    return savedRequest;
  }

  function activateRequestEditor(request: Partial<PaymentRequest>, initialTab: RequestEditorTab) {
    setEditing(request);
    setEditorInitialTab(initialTab);
    setEditorDraft(null);
    setEditorDirty(false);
    setReason("");
  }

  function resetRequestEditor() {
    setEditing(null);
    setEditorInitialTab("request");
    setEditorDraft(null);
    setEditorDirty(false);
    setPendingEditorNavigation(null);
    setReason("");
  }

  function openRequestEditor(request: Partial<PaymentRequest>, initialTab: RequestEditorTab = "request") {
    if (editing?.id && request.id && editing.id === request.id) {
      setEditorInitialTab(initialTab);
      return;
    }
    if (editing && editorDirty) {
      setPendingEditorNavigation({ kind: "switch", request, initialTab });
      return;
    }
    activateRequestEditor(request, initialTab);
  }

  function openAttachmentsFromGrid(request: PaymentRequest) {
    openRequestEditor(request, "attachments");
  }

  function closeRequestEditor() {
    if (editorDirty) {
      setPendingEditorNavigation({ kind: "close" });
      return;
    }
    resetRequestEditor();
  }

  async function saveBeforeEditorNavigation() {
    if (!pendingEditorNavigation || !editing) return;
    const navigation = pendingEditorNavigation;
    setEditorNavigationBusy(true);
    try {
      await saveRequest(editorDraft || editing);
      setPendingEditorNavigation(null);
      if (navigation.kind === "close") resetRequestEditor();
      else activateRequestEditor(navigation.request, navigation.initialTab);
    } catch (err) {
      setMessage((err as Error).message);
    } finally {
      setEditorNavigationBusy(false);
    }
  }

  function discardBeforeEditorNavigation() {
    if (!pendingEditorNavigation) return;
    const navigation = pendingEditorNavigation;
    setPendingEditorNavigation(null);
    if (navigation.kind === "close") resetRequestEditor();
    else activateRequestEditor(navigation.request, navigation.initialTab);
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
    const savedSheetOrder = getSheetTabs(gridRows, sheetOrder)
      .filter((tab) => tab.key !== ALL_SHEET && !tab.pendingDelete)
      .map((tab) => tab.key);
    await api.updateSheetOrder(selectedBatch.id, savedSheetOrder);
    setSheetOrder(savedSheetOrder);
    setReason("");
    await loadRequests();
    await reloadBatches();
    setMessage("表格更改已保存");
  }

  async function discardUnsavedChanges() {
    if (!hasUnsavedChanges && !editorDirty) return;
    if (!window.confirm("确定放弃当前页面尚未保存的修改吗？已保存的数据不会变化。")) return;
    setEditingSheet(null);
    setNewSheetName(null);
    setEditing(null);
    setEditorDraft(null);
    setEditorDirty(false);
    setReason("");
    setSheetOrder(selectedBatch?.sheet_order || []);
    await loadRequests();
    setMessage("未保存修改已放弃");
  }

  function exportCurrentResults() {
    if (!selectedBatch) return;
    if (hasUnsavedChanges || editorDirty) {
      setMessage("请先保存或放弃未保存修改，再导出");
      return;
    }
    if (visibleActiveRows.length === 0) {
      setMessage("当前筛选没有可导出的记录");
      return;
    }
    if (!hasActiveExportFilter) {
      window.open(`/api/batches/${selectedBatch.id}/export.xlsx`, "_blank");
      return;
    }
    const params = new URLSearchParams({ filtered: "true" });
    const exportFilters = {
      q: filters.q.trim(),
      payment_account: filters.payment_account.trim(),
      invoice_status: filters.invoice_status.trim(),
      finance_review: filters.finance_review.trim(),
      general_manager_approval: filters.general_manager_approval.trim(),
    };
    Object.entries(exportFilters).forEach(([key, value]) => {
      if (value) params.set(key, value);
    });
    if (activeSheet !== ALL_SHEET) params.set("source_sheet", activeSheet);
    window.open(`/api/batches/${selectedBatch.id}/export.xlsx?${params.toString()}`, "_blank");
  }

  async function restoreInitialDraftState() {
    if (!selectedBatch || !canManageDraftState) return;
    if (
      !window.confirm(
        `确定将草稿“${selectedBatch.name}”还原到初始状态吗？\n\n当前已经保存的新增、修改、删除、付款和附件变更都会被撤回；页面上未保存的修改也会被放弃。系统会先保留一份还原前快照。`,
      )
    ) return;
    await api.restoreBatchBaseline(selectedBatch.id);
    setEditingSheet(null);
    setNewSheetName(null);
    setEditing(null);
    setEditorDraft(null);
    setEditorDirty(false);
    setReason("");
    await loadRequests();
    await reloadBatches();
    setMessage("草稿已还原到初始状态");
  }

  async function setCurrentBaseline() {
    if (!selectedBatch || !canManageDraftState) return;
    if (hasUnsavedChanges || editorDirty) {
      setMessage("请先保存或放弃未保存修改，再设置还原点");
      return;
    }
    if (!window.confirm(`确定把草稿“${selectedBatch.name}”当前状态设为新的还原点吗？之后一键还原会回到现在。`)) return;
    await api.setBatchBaseline(selectedBatch.id);
    setMessage("当前草稿状态已设为还原点");
  }

  async function deleteCurrentDraft() {
    if (!selectedBatch || !canManageDraftState) return;
    if (!window.confirm(`确定删除草稿批次“${selectedBatch.name}”吗？删除后该批次下的请款、付款明细和附件凭证也会删除。`)) return;
    await api.deleteBatch(selectedBatch.id);
    setEditing(null);
    setEditorDraft(null);
    setEditorDirty(false);
    setSelectedRows([]);
    setActiveSheet(ALL_SHEET);
    setSelectedBatchId(null);
    await reloadBatches();
    setMessage("草稿批次已删除");
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
    setSheetOrder((current) => current.includes(name) ? current : [...current, name]);
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
    setSheetOrder((current) => {
      const next = current.map((name) => name === oldName ? newName : name);
      return next.includes(newName) ? next : [...next, newName];
    });
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
      <section className="batch-overview-panel">
        <div className="batch-overview-head">
          <div className="batch-context">
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
            <button className="ghost-button batch-create-button" onClick={() => setCreateDialogOpen(true)}>
              <Plus size={16} />
              新建批次
            </button>
            <div className="batch-context-meta">
              <div className="batch-context-item">
                <span>状态</span>
                <StatusPill value={selectedBatch.status === "archived" ? "已归档" : "草稿"} />
              </div>
              <div className="batch-context-item batch-period">
                <span>期间</span>
                <strong>{formatDateRange(selectedBatch.start_date, selectedBatch.end_date)}</strong>
              </div>
            </div>
          </div>
          <div className="batch-primary-actions">
            <button className="primary-button" onClick={() => setRolloverDialogOpen(true)}>
              <Archive size={16} />
              从上周生成本周
            </button>
            {selectedBatch.status === "draft" && canArchiveBatch && (
              <button className="ghost-button" onClick={archiveCurrentBatch} type="button">
                <Archive size={16} />
                归档
              </button>
            )}
            {selectedBatch.status === "archived" && canRestoreBatch && (
              <button className="ghost-button" onClick={restoreCurrentBatchDraft} type="button">
                <RefreshCcw size={16} />
                恢复草稿
              </button>
            )}
            {canManageDraftState && (
              <div className="batch-more" ref={batchMenuRef}>
                <button
                  className="ghost-button"
                  type="button"
                  aria-haspopup="menu"
                  aria-expanded={batchMenuOpen}
                  onClick={() => setBatchMenuOpen((open) => !open)}
                >
                  <MoreHorizontal size={16} />
                  更多
                </button>
                {batchMenuOpen && (
                  <div className="batch-more-menu" role="menu">
                    <button type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); void setCurrentBaseline(); }}>
                      <Save size={16} />
                      设为还原点
                    </button>
                    <button type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); void restoreInitialDraftState(); }}>
                      <Undo2 size={16} />
                      还原到初始状态
                    </button>
                    <div className="batch-more-separator" role="separator" />
                    <button className="danger-menu-item" type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); void deleteCurrentDraft(); }}>
                      <Trash2 size={16} />
                      删除当前草稿
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="batch-metric-grid">
          <div className="batch-metric-card">
            <span>批次记录</span>
            <strong>{selectedBatch.request_count || 0} 条</strong>
          </div>
          <div className="batch-metric-card">
            <span>批次应付</span>
            <strong>{formatMoney(batchPayableAmount)}</strong>
          </div>
          <div className="batch-metric-card">
            <span>累计已支付</span>
            <strong>{formatMoney(batchPaidAmount)}</strong>
          </div>
          <div className="batch-metric-card">
            <span>待付款</span>
            <strong>{formatMoney(selectedBatch.total_pending_amount || 0)}</strong>
          </div>
        </div>
        <div className="payment-progress">
          <div className="payment-progress-label">
            <span>付款进度</span>
            <strong>{paymentProgress.toFixed(1)}%</strong>
          </div>
          <div className="payment-progress-track" role="progressbar" aria-label="批次付款进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Number(paymentProgress.toFixed(1))}>
            <div className="payment-progress-fill" style={{ width: `${paymentProgress}%` }} />
          </div>
        </div>
      </section>
      <TopbarImportActions
        selectedBatch={selectedBatch}
        hasUnsavedChanges={hasUnsavedChanges || editorDirty}
        reloadBatches={reloadBatches}
        onImported={onImported}
        setMessage={setMessage}
      />
      <section className="content-panel">
        <div className="toolbar">
          <div className="search-box">
            <Search size={16} />
            <input value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="搜索单号、申请人、摘要、收款方、项目" />
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
            {generalManagerApprovalFilterOptions.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
          <button className="ghost-button" onClick={() => setMessage("筛选已应用")} onKeyDown={(event) => activateButtonByKeyboard(event, () => setMessage("筛选已应用"))}>
            <Filter size={16} />
            筛选
          </button>
          <button
            className="ghost-button"
            type="button"
            onClick={exportCurrentResults}
            onKeyDown={(event) => activateButtonByKeyboard(event, exportCurrentResults)}
            disabled={hasUnsavedChanges || editorDirty || visibleActiveRows.length === 0}
            title={
              hasUnsavedChanges || editorDirty
                ? "请先保存或放弃未保存修改"
                : hasActiveExportFilter
                  ? `导出当前筛选的 ${visibleActiveRows.length} 条记录`
                  : `导出全部 ${visibleActiveRows.length} 条记录`
            }
          >
            <Download size={16} />
            {hasActiveExportFilter ? "导出筛选结果" : "导出全部"}
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
          <button className="ghost-button" onClick={discardUnsavedChanges} onKeyDown={(event) => activateButtonByKeyboard(event, discardUnsavedChanges)} disabled={!hasUnsavedChanges && !editorDirty}>
            <Undo2 size={16} />
            放弃未保存修改
          </button>
        </div>
        <div className="filtered-summary-bar" aria-label="当前筛选结果">
          <div className="filtered-summary-count">
            <span>当前筛选</span>
            <strong>{visibleTotals.count} 条</strong>
          </div>
          <div className="filtered-summary-amounts">
            <span>应付 <strong>{formatMoney(visibleTotals.amount)}</strong></span>
            <span>已付 <strong>{formatMoney(visibleTotals.paidAmount)}</strong></span>
            <span>待付 <strong>{formatMoney(visibleTotals.pendingAmount)}</strong></span>
          </div>
          <div className="filtered-summary-statuses">
            <span className="summary-status paid">已付款 {financeReviewCounts.paid} 单</span>
            <span className="summary-status partial">部分付款 {financeReviewCounts.partial} 单</span>
            <span className="summary-status unpaid">未付款 {financeReviewCounts.unpaid} 单</span>
          </div>
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
                  draggedSheet === tab.key ? "dragging" : "",
                  sheetDropTarget?.key === tab.key ? `drag-over-${sheetDropTarget.position}` : "",
                ].filter(Boolean).join(" ")}
                onPointerDown={() => setActiveSheet(tab.key)}
                onClick={() => setActiveSheet(tab.key)}
                onFocus={() => setActiveSheet(tab.key)}
                onDoubleClick={() => beginRenameSheet(tab.key)}
                onKeyDown={(event) => activateButtonByKeyboard(event, () => setActiveSheet(tab.key))}
                draggable={canReorderSheets && tab.key !== ALL_SHEET}
                onDragStart={(event) => handleSheetDragStart(event, tab.key)}
                onDragOver={(event) => handleSheetDragOver(event, tab.key)}
                onDrop={(event) => handleSheetDrop(event, tab.key)}
                onDragEnd={handleSheetDragEnd}
                role="tab"
                aria-selected={activeSheet === tab.key}
                aria-grabbed={draggedSheet === tab.key}
                type="button"
                title={tab.key === ALL_SHEET ? "全部 Sheet" : canReorderSheets ? "拖拽调整顺序，双击重命名" : hasUnsavedChanges ? "请先保存当前修改，再调整顺序" : "双击重命名"}
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
          onOpenPayments={(request) => openRequestEditor(request, "payments")}
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
          user={user}
          request={editing}
          initialTab={editorInitialTab}
          confirmationOpen={Boolean(pendingEditorNavigation)}
          reason={reason}
          setReason={setReason}
          onCancel={closeRequestEditor}
          onSave={saveRequest}
          onDraftChange={setEditorDraft}
          onDirtyChange={setEditorDirty}
          onAttachmentsChanged={refreshAttachmentCounts}
          onPaymentsChanged={async (updatedRequest) => {
            setEditing(updatedRequest);
            await loadRequests();
            await reloadBatches();
          }}
          canEditAttachments={selectedBatch.status !== "archived" || isPrivilegedRole(user.role)}
          canEditField={(field) => canEditGrid && canEditRequestField(user.role, field)}
        />
      )}
      {pendingEditorNavigation && (
        <Modal
          title={pendingEditorNavigation.kind === "close" ? "关闭编辑请款" : "切换请款记录"}
          onClose={() => setPendingEditorNavigation(null)}
        >
          <p className="editor-navigation-message">
            当前请款有未保存修改，请选择如何处理。
          </p>
          <div className="editor-navigation-actions">
            <button className="ghost-button" type="button" onClick={() => setPendingEditorNavigation(null)} disabled={editorNavigationBusy}>
              返回编辑
            </button>
            <button className="danger-button" type="button" onClick={discardBeforeEditorNavigation} disabled={editorNavigationBusy}>
              {pendingEditorNavigation.kind === "close" ? "放弃并关闭" : "放弃并切换"}
            </button>
            <button className="primary-button" type="button" onClick={saveBeforeEditorNavigation} disabled={editorNavigationBusy}>
              <Save size={16} />
              {editorNavigationBusy
                ? "保存中"
                : pendingEditorNavigation.kind === "close" ? "保存并关闭" : "保存并切换"}
            </button>
          </div>
        </Modal>
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

function Modal({ title, onClose, children, className = "" }: { title: string; onClose: () => void; children: ReactNode; className?: string }) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className={`modal-panel ${className}`.trim()} role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
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
  onOpenPayments,
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
  onOpenPayments: (request: PaymentRequest) => void;
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
    const row = withPaymentAmountChange(
      { ...nextRows[rowIndex] },
      column.key,
      normalizeCellValue(column, value),
    );
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
        let targetRow = { ...nextRows[targetRowIndex] };
        sourceRow.forEach((cellValue, colOffset) => {
          const column = gridColumns[activeCell.col + colOffset];
          if (!column || !canEditField(column.key)) return;
          targetRow = withPaymentAmountChange(targetRow, column.key, normalizeCellValue(column, cellValue));
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
              <th className="payment-detail-col" style={{ width: 120, minWidth: 120 }}>付款明细</th>
              <th className="attachment-col" style={{ width: 112, minWidth: 112 }}>附件</th>
              <th className="external-status-col">钉钉状态</th>
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
                <td className="payment-detail-col">
                  {row.id ? (
                    <button
                      className={row.payment_count ? "payment-detail-chip has-payments" : "payment-detail-chip"}
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenPayments(row as PaymentRequest);
                      }}
                    >
                      {row.payment_count ? `付款 ${row.payment_count} 笔` : "未付款"}
                    </button>
                  ) : (
                    <span className="muted-chip">先保存</span>
                  )}
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
	                <td className="external-status-col">
	                  {row.raw_extra?.external_source
	                    ? <ExternalApprovalBadge source={row.raw_extra.external_source} snapshot />
	                    : <span className="external-status-empty">—</span>}
	                </td>
	                {gridColumns.map((column, colIndex) => {
	                  const dirty = dirtyCells.has(`${row.__localId}:${column.key}`);
	                  const cellValue = cellDisplayValue(row, column);
	                  const selectOptions = selectOptionsForField(column.key, cellValue);
	                  const shouldWrap = wrapText && wrappableColumnKeys.has(column.key) && column.type !== "number" && column.type !== "date";
	                  const terminatedManagerField = requestDingTalkTerminated(row) && generalManagerControlledFields.has(column.key);
	                  const cellReadOnly = readOnly || row.__deleted || !canEditField(column.key) || terminatedManagerField;
	                  const fieldClass = [
	                    column.key === "dingding_id" ? "mono" : "",
	                    moneyFields.has(column.key) ? "amount-input" : "",
	                    shouldWrap ? "wrap-field" : "",
	                    cellReadOnly ? "readonly-field" : "",
	                  ].filter(Boolean).join(" ");
                  return (
                    <td key={column.key} className={`${dirty ? "dirty-cell " : ""}${column.key === "applicant" ? "applicant-edit-cell" : ""}`.trim()} style={{ width: column.width, minWidth: column.width, maxWidth: column.width }}>
                        {column.key === "applicant" ? (
                          <div className="applicant-editor-cell">
                            <input
                              data-cell={`${rowIndex}-${colIndex}`}
                              className={fieldClass}
                              type="text"
                              value={cellValue}
                              title={requestApplicantTitle(row)}
                              readOnly={cellReadOnly}
                              placeholder="请输入申请人"
                              onFocus={() => setActiveCell({ row: rowIndex, col: colIndex })}
                              onPaste={handlePaste}
                              onKeyDown={(event) => handleKeyDown(event, rowIndex, colIndex)}
                              onChange={(event) => updateCell(rowIndex, column, event.target.value)}
                            />
                            <small>{requestApplicantMeta(row)}</small>
                          </div>
                        ) : selectOptions ? (
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
                            min={column.key === "paid_amount" ? 0 : undefined}
                            step={column.type === "number" ? "0.01" : undefined}
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
  user,
  request,
  initialTab,
  confirmationOpen,
  reason,
  setReason,
  onCancel,
  onSave,
  onDraftChange,
  onDirtyChange,
  onAttachmentsChanged,
  onPaymentsChanged,
  canEditAttachments,
  canEditField,
}: {
  batch: Batch;
  user: User;
  request: Partial<PaymentRequest>;
  initialTab: RequestEditorTab;
  confirmationOpen: boolean;
  reason: string;
  setReason: (value: string) => void;
  onCancel: () => void;
  onSave: (request: Partial<PaymentRequest>) => Promise<PaymentRequest | undefined>;
  onDraftChange?: (request: Partial<PaymentRequest> | null) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onAttachmentsChanged?: () => Promise<void> | void;
  onPaymentsChanged?: (request: PaymentRequest) => Promise<void> | void;
  canEditAttachments: boolean;
  canEditField: (field: keyof PaymentRequest) => boolean;
}) {
  const [form, setForm] = useState<Partial<PaymentRequest>>(request);
  const [activeTab, setActiveTab] = useState<RequestEditorTab>(initialTab);
  const [attachments, setAttachments] = useState<AttachmentLink[]>([]);
  const [workflow, setWorkflow] = useState<DingtalkWorkflow | null>(null);
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [workflowError, setWorkflowError] = useState("");
  const [attachmentForm, setAttachmentForm] = useState({ label: "", url_path: "" });
  const [imageLabel, setImageLabel] = useState("");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [attachmentMode, setAttachmentMode] = useState<"image" | "link" | null>(null);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [previewImages, setPreviewImages] = useState<{ images: AttachmentLink[]; index: number } | null>(null);
  const fields: Array<keyof PaymentRequest> = [
    "dingding_id",
    "applicant",
    "payment_account",
    "expense_type",
    "summary",
    "amount",
    "paid_amount",
    "pending_amount",
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
  const canManagePayments = ["finance", "general_manager", "admin"].includes(user.role)
    && (batch.status === "draft" || isPrivilegedRole(user.role));
  const canCorrectArchived = batch.status === "archived" && isPrivilegedRole(user.role);
  const payableAmount = Number(form.amount || 0);
  const paidAmount = Number(form.paid_amount || 0);
  const pendingAmount = Number(form.pending_amount ?? Math.max(0, payableAmount - paidAmount));

  useEffect(() => {
    setForm(request);
    setAttachments([]);
    setWorkflow(null);
    setWorkflowError("");
    setAttachmentForm({ label: "", url_path: "" });
    setImageLabel("");
    setImageFile(null);
    setAttachmentMode(null);
    setSaveError("");
    setPreviewImages(null);
  }, [request]);

  useEffect(() => {
    setActiveTab(!request.id && (initialTab === "payments" || initialTab === "workflow" || initialTab === "attachments") ? "request" : initialTab);
  }, [initialTab, request.id]);

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

  useEffect(() => {
    if (!request.id || activeTab !== "workflow") return;
    setWorkflowLoading(true);
    setWorkflowError("");
    api.dingtalkWorkflow(batch.id, request.id)
      .then(setWorkflow)
      .catch((error) => {
        setWorkflow(null);
        setWorkflowError((error as Error).message);
      })
      .finally(() => setWorkflowLoading(false));
  }, [activeTab, batch.id, request.id]);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !previewImages && !confirmationOpen) onCancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirmationOpen, onCancel, previewImages]);

  async function addAttachment() {
    if (!form.id || !canEditAttachments || !attachmentForm.url_path.trim()) return;
    const res = await api.createAttachment(batch.id, form.id, attachmentForm);
    setAttachments([...attachments, res.attachment]);
    setAttachmentForm({ label: "", url_path: "" });
    setAttachmentMode(null);
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
      setAttachmentMode(null);
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

  function renderField(field: keyof PaymentRequest, options: { span?: boolean } = {}) {
    const fieldEditable = canEditField(field) && !(requestDingTalkTerminated(form) && generalManagerControlledFields.has(field));
    const className = options.span ? "editor-field span-2" : "editor-field";
    const label = fieldLabels[field] || field;
    const value = field === "applicant" ? requestApplicantName(form) : form[field];
    if (!fieldEditable) {
      return (
        <div className={`${className} editor-readonly-field`} key={field}>
          <span>{label}</span>
          {field === "finance_review" ? (
            <StatusPill value={String(value || "未付款")} />
          ) : (
            <strong>{moneyFields.has(field) ? formatMoney(Number(value || 0)) : String(value || "未填写")}</strong>
          )}
        </div>
      );
    }
    return (
      <label className={className} key={field}>
        {label}
        {selectOptionsForField(field, String(value || "")) ? (
          <select value={String(value || "")} onChange={(event) => setForm(withPaymentAmountChange(form, field, event.target.value))}>
            {selectOptionsForField(field, String(value || ""))!.map((option) => (
              <option key={option} value={option}>{option || "未选择"}</option>
            ))}
          </select>
        ) : field === "summary" || field === "remark" || field === "general_manager_opinion" ? (
          <textarea className={field === "summary" ? "summary-textarea" : ""} value={String(value || "")} onChange={(event) => setForm({ ...form, [field]: event.target.value })} />
        ) : (
          <input
            type={moneyFields.has(field) ? "number" : field.includes("date") ? "date" : "text"}
            step={moneyFields.has(field) ? "0.01" : undefined}
            value={String(value ?? "")}
            onChange={(event) => {
              const nextValue = moneyFields.has(field)
                ? (event.target.value === "" ? undefined : Number(event.target.value))
                : event.target.value;
              setForm(withPaymentAmountChange(form, field, nextValue));
            }}
          />
        )}
      </label>
    );
  }

  async function saveRequestForm() {
    if (!isDirty || saving) return;
    setSaving(true);
    setSaveError("");
    try {
      const savedRequest = await onSave(form);
      if (savedRequest) setForm(savedRequest);
    } catch (err) {
      setSaveError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function discardRequestChanges() {
    setForm(request);
    setSaveError("");
  }

  const editorTabs: Array<{ key: RequestEditorTab; label: string; disabled?: boolean }> = [
    { key: "request", label: "请款信息" },
    { key: "approval", label: "审批信息" },
    { key: "payments", label: `付款明细 ${Number(form.payment_count || 0)}`, disabled: !form.id },
    { key: "workflow", label: `钉钉流程 ${workflow?.summary.total ?? 0}`, disabled: !form.id },
    { key: "attachments", label: `附件 ${attachments.length}`, disabled: !form.id },
  ];

  return (
    <div className="drawer">
      <header className="request-editor-header">
        <div className="request-editor-title-row">
          <div className="request-editor-title">
            <div>
              <h2>{form.id ? "编辑请款" : "新增请款"}</h2>
              <StatusPill value={String(form.finance_review || "未付款")} />
              {form.raw_extra?.external_source && (
                <ExternalApprovalBadge source={form.raw_extra.external_source} snapshot />
              )}
            </div>
            <span title={String(form.summary || "")}>{form.summary || "尚未填写摘要"}</span>
            <small>{form.dingding_id ? `钉钉单号：${form.dingding_id}` : "钉钉单号未填写"}</small>
          </div>
          <button className="ghost-button" onClick={onCancel} type="button">关闭</button>
        </div>
        <div className="request-editor-overview">
          <div><span>应付金额</span><strong>{formatMoney(payableAmount)}</strong></div>
          <div><span>累计已付</span><strong>{formatMoney(paidAmount)}</strong></div>
          <div><span>待付款</span><strong>{formatMoney(pendingAmount)}</strong></div>
        </div>
        <nav className="request-editor-tabs" aria-label="请款编辑区域">
          {editorTabs.map((tab) => (
            <button
              key={tab.key}
              className={activeTab === tab.key ? "active" : ""}
              type="button"
              disabled={tab.disabled}
              title={tab.disabled ? "请先保存请款" : undefined}
              aria-current={activeTab === tab.key ? "page" : undefined}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </header>
      <div className="request-editor-content">
        {activeTab === "request" && (
          <div className="editor-tab-panel">
            {!form.id && <div className="editor-info-banner">首次保存后即可录入付款和上传附件。</div>}
            <section className="editor-form-section">
              <div className="editor-section-head">
                <div><h3>基本信息</h3><p>请款来源、归属和用途说明</p></div>
              </div>
              <div className="editor-form-grid">
                {renderField("dingding_id")}
                <div className="editor-applicant-field">
                  {renderField("applicant")}
                  <div className="editor-applicant-meta">
                    <span>{requestApplicantMeta(form)}</span>
                    {form.raw_extra?.external_source?.applicant && form.applicant != null && (
                      <button type="button" className="text-button" onClick={() => setForm({ ...form, applicant: null })}>
                        恢复为钉钉姓名
                      </button>
                    )}
                  </div>
                </div>
                {renderField("payment_account")}
                {renderField("expense_type")}
                {renderField("project")}
                {renderField("source_sheet")}
                {renderField("summary", { span: true })}
              </div>
            </section>
            <section className="editor-form-section">
              <div className="editor-section-head">
                <div><h3>金额与收款</h3><p>应付金额、收款资料和开票信息</p></div>
              </div>
              <div className="editor-form-grid">
                {renderField("amount")}
                {renderField("needed_payment_date")}
                {renderField("payee_account")}
                {renderField("payee_name")}
                {renderField("bank_name")}
                {renderField("invoice_status")}
                {renderField("remark", { span: true })}
              </div>
            </section>
          </div>
        )}
        {activeTab === "approval" && (
          <div className="editor-tab-panel">
            <section className="editor-form-section">
              <div className="editor-section-head">
                <div><h3>审批信息</h3><p>付款状态由付款明细自动计算，审批字段继续按角色维护</p></div>
              </div>
              <div className="editor-form-grid">
                {renderField("finance_review")}
                {renderField("actual_payment_date")}
                {renderField("general_manager_approval")}
                {renderField("general_manager_approval_date")}
                {renderField("general_manager_opinion", { span: true })}
              </div>
            </section>
          </div>
        )}
        {activeTab === "payments" && form.id && (
          <div className="editor-tab-panel">
            <PaymentDetailsPanel
              batch={batch}
              request={form as PaymentRequest}
              reason={reason}
              canManage={canManagePayments}
              onRequestChanged={async (updatedRequest) => {
                setForm((current) => ({ ...current, ...updatedRequest }));
                await onPaymentsChanged?.(updatedRequest);
              }}
            />
          </div>
        )}
        {activeTab === "workflow" && form.id && (
          <div className="editor-tab-panel">
            <section className="editor-form-section dingtalk-workflow-panel">
              <div className="editor-section-head workflow-section-head">
                <div>
                  <h3>钉钉审批流程</h3>
                  <p>展示最近一次同步缓存；普通搜索、筛选和切换 Sheet 不会访问钉钉数据库</p>
                </div>
                {workflow?.last_synced_at && <small>同步于 {formatDateTime(workflow.last_synced_at)}</small>}
              </div>
              {workflowLoading && <div className="workflow-empty-state">正在读取本地流程缓存…</div>}
              {!workflowLoading && workflowError && <div className="error-text">{workflowError}</div>}
              {!workflowLoading && !workflowError && !form.dingding_id && (
                <div className="workflow-empty-state">该请款尚未填写钉钉申请单号。</div>
              )}
              {!workflowLoading && !workflowError && form.dingding_id && workflow?.lookup_status === "conflict" && (
                <div className="workflow-empty-state warning">同一钉钉单号存在多个来源，暂不展示错误的流程信息。</div>
              )}
              {!workflowLoading && !workflowError && form.dingding_id && workflow?.lookup_status === "unmatched" && workflow.events.length === 0 && (
                <div className="workflow-empty-state">未在钉钉中间库匹配到该申请单号。</div>
              )}
              {!workflowLoading && !workflowError && form.dingding_id && workflow && workflow.events.length === 0 && workflow.lookup_status !== "unmatched" && workflow.lookup_status !== "conflict" && (
                <div className="workflow-empty-state">尚无已缓存的流程记录，请点击“同步钉钉流程”。</div>
              )}
              {!!workflow?.events.length && (
                <div className="workflow-timeline">
                  {workflow.events.map((event) => {
                    const mediaCount = event.images.length + event.attachments.length;
                    const paymentApplied = Boolean(event.payment_record_id);
                    const needsReview = event.classification === "review_required" || event.classification === "source_missing";
                    const previewCandidate = event.classification === "preview_candidate";
                    return (
                      <article className={`workflow-event ${event.current ? "current" : ""} ${!event.active ? "inactive" : ""}`} key={event.id}>
                        <div className="workflow-event-marker" aria-hidden="true" />
                        <div className="workflow-event-card">
                          <header>
                            <div>
                              <strong>{event.stage_name || "流程操作"}</strong>
                              {event.current && <span className="workflow-state-badge current">当前节点</span>}
                              {event.result && <span className={`workflow-state-badge ${event.result.toLowerCase()}`}>{workflowResultLabel(event.result)}</span>}
                            </div>
                            <time>{event.event_time ? formatDateTime(event.event_time) : "时间未知"}</time>
                          </header>
                          <div className="workflow-operator">
                            <span>{event.operator_name || "未识别人员"}</span>
                            {event.trusted_finance && <span className="workflow-finance-badge">可信财务节点</span>}
                          </div>
                          {event.comment && <p className="workflow-comment"><MessageSquareText size={15} />{event.comment}</p>}
                          {mediaCount > 0 && <p className="workflow-media-note">包含 {event.images.length} 张图片、{event.attachments.length} 个附件</p>}
                          {(paymentApplied || needsReview || previewCandidate) && (
                            <div className={`workflow-decision ${paymentApplied ? "applied" : needsReview ? "review" : "preview"}`}>
                              <div>
                                <strong>
                                  {paymentApplied
                                    ? `已生成付款 ${formatMoney(Number(event.payment_amount || 0))}`
                                    : needsReview
                                      ? "付款待核对"
                                      : "自动付款候选"}
                                </strong>
                                <span>{event.classification_reason}</span>
                              </div>
                              {needsReview && (
                                <button className="ghost-button" type="button" onClick={() => setActiveTab("payments")}>
                                  去付款明细
                                </button>
                              )}
                            </div>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          </div>
        )}
        {activeTab === "attachments" && form.id && (
          <div className="editor-tab-panel">
            <section className="editor-form-section attachment-manager">
              <div className="editor-section-head attachment-section-head">
                <div><h3>请款附件</h3><p>合同、发票等资料；付款凭证请在对应付款记录中维护</p></div>
                {canEditAttachments && (
                  <div className="attachment-mode-actions">
                    <button className={attachmentMode === "image" ? "ghost-button active-toggle" : "ghost-button"} type="button" onClick={() => setAttachmentMode(attachmentMode === "image" ? null : "image")}>
                      <ImageIcon size={16} />上传图片
                    </button>
                    <button className={attachmentMode === "link" ? "ghost-button active-toggle" : "ghost-button"} type="button" onClick={() => setAttachmentMode(attachmentMode === "link" ? null : "link")}>
                      <Paperclip size={16} />添加链接
                    </button>
                  </div>
                )}
              </div>
              {attachmentMode === "image" && (
                <div className="attachment-create-panel">
                  <input placeholder="图片名称，可选" value={imageLabel} onChange={(event) => setImageLabel(event.target.value)} />
                  <input type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/bmp" onChange={(event) => setImageFile(event.target.files?.[0] || null)} />
                  <button className="primary-button" onClick={uploadImageAttachment} type="button" disabled={!imageFile || uploadingImage}>
                    <Upload size={16} />{uploadingImage ? "上传中" : "上传图片"}
                  </button>
                </div>
              )}
              {attachmentMode === "link" && (
                <div className="attachment-create-panel">
                  <input placeholder="名称" value={attachmentForm.label} onChange={(event) => setAttachmentForm({ ...attachmentForm, label: event.target.value })} />
                  <input placeholder="流程链接或本地路径" value={attachmentForm.url_path} onChange={(event) => setAttachmentForm({ ...attachmentForm, url_path: event.target.value })} />
                  <button className="primary-button" onClick={addAttachment} type="button" disabled={!attachmentForm.url_path.trim()}>添加链接</button>
                </div>
              )}
              <div className="attachment-list">
                {attachments.length === 0 && <div className="empty-attachment-state">尚无请款附件</div>}
                {attachments.map((item) => (
                  <div key={item.id} className="attachment-item">
                    {isImageAttachment(item) ? (
                      <button className="attachment-thumb-button" type="button" onClick={() => previewAttachment(item)}>
                        <img className="attachment-thumb" src={attachmentImageUrl(item)} alt={attachmentTitle(item, "图片附件")} />
                      </button>
                    ) : (
                      <div className="attachment-thumb-placeholder"><Paperclip size={20} /></div>
                    )}
                    <div className="attachment-meta">
                      <strong>{attachmentTitle(item, "附件")}</strong>
                      <span>
                        {isDingtalkAttachment(item)
                          ? `钉钉关键凭证${item.file_size ? ` · ${formatFileSize(item.file_size)}` : ""}`
                          : isImageAttachment(item)
                            ? item.original_filename || item.url_path
                            : item.url_path}
                      </span>
                    </div>
                    <div className="attachment-actions">
                      {isImageAttachment(item) && <button className="ghost-button" type="button" onClick={() => previewAttachment(item)}><ImageIcon size={14} />预览</button>}
                      {!isImageAttachment(item) && item.file_url && (
                        <a className="ghost-button" href={item.file_url} target="_blank" rel="noreferrer">
                          <Download size={14} />{isPdfAttachment(item) ? "查看" : "下载"}
                        </a>
                      )}
                      {canEditAttachments && !isDingtalkAttachment(item) && <button type="button" onClick={() => removeAttachment(item.id)}>删除</button>}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
        )}
      </div>
      <footer className="request-editor-footer">
        {canCorrectArchived && (
          <label className="archived-reason-field">
            归档更正原因
            <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="修改请款、付款或附件前必须填写" />
          </label>
        )}
        {saveError && <p className="error-text editor-save-error">{saveError}</p>}
        <div className="request-editor-actions">
          <div className={isDirty ? "editor-save-state dirty" : "editor-save-state"}>
            <strong>{isDirty ? "有未保存修改" : "已保存"}</strong>
            <span>{activeTab === "payments" || activeTab === "workflow" || activeTab === "attachments" ? "付款、流程和附件单独同步生效" : "保存请款后继续留在当前抽屉"}</span>
          </div>
          <div>
            <button className="ghost-button" type="button" onClick={discardRequestChanges} disabled={!isDirty || saving}>放弃修改</button>
            <button className="primary-button" type="button" onClick={saveRequestForm} disabled={!isDirty || saving}>
              <Save size={16} />{saving ? "保存中" : "保存请款"}
            </button>
          </div>
        </div>
      </footer>
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

const emptyPaymentForm: PaymentRecordPayload = {
  amount: 0,
  payment_date: "",
  payer: "",
  payment_account: "",
  bank_reference: "",
  remark: "",
};

function PaymentDetailsPanel({
  batch,
  request,
  reason,
  canManage,
  onRequestChanged,
}: {
  batch: Batch;
  request: PaymentRequest;
  reason: string;
  canManage: boolean;
  onRequestChanged: (request: PaymentRequest) => Promise<void> | void;
}) {
  const [payments, setPayments] = useState<PaymentRecord[]>([]);
  const [summary, setSummary] = useState<PaymentSummary>({
    amount: request.amount,
    paid_amount: Number(request.paid_amount || 0),
    pending_amount: request.pending_amount,
    finance_review: request.finance_review || "未付款",
    payment_count: Number(request.payment_count || 0),
    actual_payment_date: request.actual_payment_date,
    payer: request.payer,
  });
  const [paymentForm, setPaymentForm] = useState<PaymentRecordPayload>({ ...emptyPaymentForm, payment_account: request.payment_account || "" });
  const [editingPaymentId, setEditingPaymentId] = useState<number | null>(null);
  const [paymentFormOpen, setPaymentFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const paymentFormRef = useRef<HTMLDivElement | null>(null);

  async function loadPayments() {
    const result = await api.payments(batch.id, request.id);
    setPayments(result.payments);
    setSummary(result.summary);
  }

  useEffect(() => {
    setPaymentForm({ ...emptyPaymentForm, payment_account: request.payment_account || "" });
    setEditingPaymentId(null);
    setPaymentFormOpen(false);
    loadPayments().catch((err) => setError((err as Error).message));
  }, [batch.id, request.id]);

  useEffect(() => {
    if (!paymentFormOpen) return;
    const timer = window.setTimeout(() => paymentFormRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 40);
    return () => window.clearTimeout(timer);
  }, [editingPaymentId, paymentFormOpen]);

  const editingPayment = payments.find((payment) => payment.id === editingPaymentId);
  const maxPayable = roundMoney(Number(summary.pending_amount || 0) + Number(editingPayment?.amount || 0));
  const paymentProgress = Number(summary.amount || 0) > 0
    ? Math.min(100, Math.max(0, (Number(summary.paid_amount || 0) / Number(summary.amount || 0)) * 100))
    : 0;

  function resetPaymentForm(closeForm = true) {
    setEditingPaymentId(null);
    setPaymentForm({ ...emptyPaymentForm, payment_account: request.payment_account || "" });
    if (closeForm) setPaymentFormOpen(false);
  }

  function beginCreatePayment() {
    resetPaymentForm(false);
    setError("");
    setPaymentFormOpen(true);
  }

  function beginEdit(payment: PaymentRecord) {
    setEditingPaymentId(payment.id);
    setPaymentForm({
      amount: payment.amount,
      payment_date: payment.payment_date || "",
      payer: payment.payer || "",
      payment_account: payment.payment_account || "",
      bank_reference: payment.bank_reference || "",
      remark: payment.remark || "",
    });
    setError("");
    setPaymentFormOpen(true);
  }

  async function savePayment() {
    if (!canManage || !paymentForm.payment_date || Number(paymentForm.amount) <= 0) {
      setError("请填写有效的本次金额和付款日期");
      return;
    }
    if (Number(paymentForm.amount) > maxPayable + 0.000001) {
      setError(`本次金额不能超过 ${formatMoney(maxPayable)}`);
      return;
    }
    if (batch.status === "archived" && !reason.trim()) {
      setError("归档后更正付款必须填写原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = { ...paymentForm, reason };
      const result = editingPaymentId
        ? await api.updatePayment(batch.id, request.id, editingPaymentId, payload)
        : await api.createPayment(batch.id, request.id, payload);
      resetPaymentForm();
      await loadPayments();
      await onRequestChanged(result.request);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function removePayment(payment: PaymentRecord) {
    if (!canManage || payment.inherited || !window.confirm(`确定删除这笔 ${formatMoney(payment.amount)} 的付款记录吗？`)) return;
    if (batch.status === "archived" && !reason.trim()) {
      setError("归档后删除付款必须填写原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.deletePayment(batch.id, request.id, payment.id, reason);
      if (editingPaymentId === payment.id) resetPaymentForm();
      await loadPayments();
      await onRequestChanged(result.request);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function uploadVouchers(payment: PaymentRecord, files: FileList | null) {
    if (!files?.length || !canManage || payment.inherited) return;
    setBusy(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        await api.uploadPaymentVoucher(batch.id, request.id, payment.id, file, file.name, reason);
      }
      await loadPayments();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function removeVoucher(payment: PaymentRecord, voucher: PaymentVoucher) {
    if (!canManage || payment.inherited) return;
    setBusy(true);
    setError("");
    try {
      await api.deletePaymentVoucher(batch.id, request.id, payment.id, voucher.id, reason);
      await loadPayments();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="payment-details-box">
      <div className="section-title-row">
        <div>
          <div className="section-title">付款明细</div>
          <small>汇总金额和付款状态由明细自动计算</small>
        </div>
        <div className="payment-section-actions">
          <StatusPill value={summary.finance_review} />
          {canManage && maxPayable > 0 && !paymentFormOpen && (
            <button className="primary-button" type="button" onClick={beginCreatePayment}>
              <Plus size={16} />新增付款
            </button>
          )}
        </div>
      </div>
      <div className="payment-summary-grid">
        <div><span>应付金额</span><strong>{formatMoney(summary.amount || 0)}</strong></div>
        <div><span>累计已付</span><strong>{formatMoney(summary.paid_amount || 0)}</strong></div>
        <div><span>待付款</span><strong>{formatMoney(summary.pending_amount || 0)}</strong></div>
      </div>
      <div className="payment-detail-progress">
        <div><span>付款进度</span><strong>{paymentProgress.toFixed(1)}%</strong></div>
        <div className="payment-progress-track" role="progressbar" aria-label="请款付款进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Number(paymentProgress.toFixed(1))}>
          <div className="payment-progress-fill" style={{ width: `${paymentProgress}%` }} />
        </div>
      </div>
      {error && <p className="error-text payment-error">{error}</p>}
      <div className="payment-record-list">
        {payments.length === 0 && (
          <div className="empty-payment-state">
            <span>尚无付款记录</span>
            {canManage && maxPayable > 0 && !paymentFormOpen && (
              <button className="primary-button" type="button" onClick={beginCreatePayment}>录入第一笔付款</button>
            )}
          </div>
        )}
        {payments.map((payment, index) => (
          <article className="payment-record-card" key={payment.id}>
            <div className="payment-record-head">
              <div>
                <strong>第 {index + 1} 笔 · {formatMoney(payment.amount)}</strong>
                <span>{payment.payment_date || "日期未知"} · {payment.payer || "付款人未填写"}</span>
              </div>
              <span className={payment.inherited ? "source-badge inherited" : "source-badge"}>{paymentSourceLabel(payment.source_type)}</span>
            </div>
            <div className="payment-record-meta">
              <span>账户：{payment.payment_account || "未填写"}</span>
              <span>流水号：{payment.bank_reference || "未填写"}</span>
              <span>录入人：{payment.creator_name || "系统"}</span>
              {payment.remark && <span className="payment-remark">备注：{payment.remark}</span>}
            </div>
            <div className="payment-voucher-list">
              {payment.vouchers.map((voucher) => (
                <div className="payment-voucher" key={voucher.id}>
                  <a href={voucher.file_url} target="_blank" rel="noreferrer" title={voucher.original_filename || voucher.label || "付款凭证"}>
                    {voucher.voucher_type === "image" ? (
                      <img src={voucher.file_url} alt={voucher.original_filename || "付款凭证"} />
                    ) : (
                      <span className="pdf-voucher">PDF</span>
                    )}
                  </a>
                  <span>{voucher.original_filename || voucher.label || "付款凭证"}</span>
                  {canManage && !payment.inherited && (
                    <button type="button" onClick={() => removeVoucher(payment, voucher)} disabled={busy}>删除</button>
                  )}
                </div>
              ))}
              {canManage && !payment.inherited && (
                <label className="voucher-upload-button">
                  <Upload size={15} />
                  上传凭证
                  <input
                    type="file"
                    multiple
                    accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,application/pdf"
                    onChange={(event) => {
                      uploadVouchers(payment, event.target.files);
                      event.currentTarget.value = "";
                    }}
                    disabled={busy}
                  />
                </label>
              )}
            </div>
            {canManage && !payment.inherited && (
              <div className="payment-record-actions">
                <button className="ghost-button" type="button" onClick={() => beginEdit(payment)} disabled={busy}>编辑</button>
                <button className="danger-button" type="button" onClick={() => removePayment(payment)} disabled={busy}>删除</button>
              </div>
            )}
          </article>
        ))}
      </div>
      {canManage && paymentFormOpen && (
        <div className="payment-entry-form" ref={paymentFormRef}>
          <div className="payment-form-title span-2">
            <div><strong>{editingPaymentId ? "编辑付款" : "新增付款"}</strong><span>金额和付款日期为必填项</span></div>
            <button className="ghost-button" type="button" onClick={() => resetPaymentForm()} disabled={busy}>取消</button>
          </div>
          <label>
            本次金额（最大 {formatMoney(maxPayable)}）
            <input type="number" min="0.01" max={maxPayable} step="0.01" value={paymentForm.amount || ""} onChange={(event) => setPaymentForm({ ...paymentForm, amount: Number(event.target.value) })} />
          </label>
          <label>
            付款日期
            <input type="date" value={paymentForm.payment_date} onChange={(event) => setPaymentForm({ ...paymentForm, payment_date: event.target.value })} />
          </label>
          <label>
            付款人
            <input value={paymentForm.payer || ""} onChange={(event) => setPaymentForm({ ...paymentForm, payer: event.target.value })} />
          </label>
          <label>
            付款账户
            <input value={paymentForm.payment_account || ""} onChange={(event) => setPaymentForm({ ...paymentForm, payment_account: event.target.value })} />
          </label>
          <label>
            银行流水号
            <input value={paymentForm.bank_reference || ""} onChange={(event) => setPaymentForm({ ...paymentForm, bank_reference: event.target.value })} />
          </label>
          <label className="span-2">
            备注
            <textarea value={paymentForm.remark || ""} onChange={(event) => setPaymentForm({ ...paymentForm, remark: event.target.value })} />
          </label>
          <div className="payment-entry-actions span-2">
            <button className="primary-button" type="button" onClick={savePayment} disabled={busy || maxPayable <= 0}>
              <Save size={16} />{busy ? "保存中" : editingPaymentId ? "保存付款修改" : "新增付款"}
            </button>
          </div>
        </div>
      )}
    </section>
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
  user,
  batches,
  selectedBatch,
  setSelectedBatchId,
  reloadBatches,
  setMessage,
}: {
  user: User;
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
    if (!window.confirm(`确定删除草稿批次“${batch.name}”吗？删除后该批次下的请款、付款明细和附件凭证也会删除。`)) return;
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
                <th>应付金额</th>
                <th>已支付金额</th>
                <th>待付款金额</th>
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
                  <td className="amount">{formatMoney(batch.total_paid_amount || 0)}</td>
                  <td className="amount">{formatMoney(batch.total_pending_amount || 0)}</td>
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
                    {batch.status === "draft" && isPrivilegedRole(user.role) && (
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
        <div className="section-title">操作日志</div>
        <div className="audit-list">
          {logs.map((log) => (
            <div key={log.id} className="audit-item">
              <strong>{auditActionLabel(log.action)}</strong>
              <span>{log.actor_name || "系统"} · {log.created_at}</span>
              {auditDetail(log) && <p>{auditDetail(log)}</p>}
              {log.reason && <p>{log.reason}</p>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function auditActionLabel(action: string) {
  const labels: Record<string, string> = {
    "external_expenses.metadata_sync": "同步钉钉流程",
    "payment.auto_create_from_dingtalk": "钉钉流程自动生成付款",
  };
  return labels[action] || action;
}

function auditDetail(log: AuditLog) {
  if (log.action !== "external_expenses.metadata_sync" || !log.new_value || typeof log.new_value !== "object") return "";
  const value = log.new_value as Partial<{
    matched: number;
    unmatched: number;
    conflicts: number;
    updated_requests: number;
    workflow_events: number;
    payment_candidates: number;
    auto_payments: number;
    review_required: number;
    auto_payment_mode: string;
  }>;
  const paymentText = value.auto_payment_mode === "preview"
    ? `候选付款 ${Number(value.payment_candidates || 0)} 笔`
    : `自动付款 ${Number(value.auto_payments || 0)} 笔`;
  return `匹配 ${Number(value.matched || 0)} 个单号，未匹配 ${Number(value.unmatched || 0)} 个，来源冲突 ${Number(value.conflicts || 0)} 个，流程事件 ${Number(value.workflow_events || 0)} 条，${paymentText}，待核对 ${Number(value.review_required || 0)} 条，更新 ${Number(value.updated_requests || 0)} 条请款`;
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

function getSheetTabs(rows: GridRow[], sheetOrder: string[] = []): SheetTab[] {
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
  const orderIndex = new Map(sheetOrder.map((name, index) => [name, index]));
  const sheetTabs = Array.from(counts.entries())
    .sort(([left], [right]) => {
      const leftIndex = orderIndex.get(left);
      const rightIndex = orderIndex.get(right);
      if (leftIndex !== undefined || rightIndex !== undefined) {
        if (leftIndex === undefined) return 1;
        if (rightIndex === undefined) return -1;
        return leftIndex - rightIndex;
      }
      return left.localeCompare(right, "zh-CN");
    })
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
  const value = currentValue.trim();
  if (strictSelectFields.has(field)) {
    if (field === "general_manager_approval" && value === "无需审批") options.push(value);
    return options;
  }
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
      requestApplicantName(row),
      requestApplicantDepartment(row),
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
    if (calculatedRequestFields.has(column.key)) return;
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

function withoutDerivedPaymentFields(request: Partial<PaymentRequest>): Partial<PaymentRequest> {
  const output: Partial<PaymentRequest> = {};
  Object.entries(request).forEach(([key, value]) => {
    if (key === "id" || calculatedRequestFields.has(key as keyof PaymentRequest)) return;
    output[key as keyof PaymentRequest] = value as never;
  });
  return output;
}

function paymentSourceLabel(sourceType: string) {
  const labels: Record<string, string> = {
    manual: "本周录入",
    rollover: "结转继承",
    legacy_migration: "历史迁移",
    legacy_normalization: "历史归一",
    snapshot_legacy: "历史快照",
    excel_summary: "Excel 汇总导入",
    excel_detail: "Excel 明细导入",
    dingtalk_workflow: "钉钉流程自动识别",
  };
  return labels[sourceType] || sourceType || "系统记录";
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

function withPaymentAmountChange<T extends Partial<PaymentRequest>>(
  request: T,
  field: keyof PaymentRequest,
  value: unknown,
): T {
  const next = { ...request, [field]: value } as T;
  if (!moneyFields.has(field) && field !== "finance_review") return next;

  const amount = optionalNumber(next.amount);
  let paidAmount = optionalNumber(next.paid_amount) ?? 0;
  if (field === "finance_review" && value === "已付款" && amount !== undefined) {
    paidAmount = amount;
    next.paid_amount = paidAmount;
  } else if (field === "finance_review" && value === "未付款") {
    paidAmount = 0;
    next.paid_amount = 0;
  } else if (field === "finance_review" && value === "部分付款" && amount !== undefined && paidAmount >= amount) {
    paidAmount = 0;
    next.paid_amount = 0;
  }

  next.pending_amount = amount === undefined ? undefined : roundMoney(amount - paidAmount);
  if ((field === "amount" || field === "paid_amount") && paidAmount > 0 && amount !== undefined) {
    next.finance_review = paidAmount >= amount ? "已付款" : "部分付款";
  } else if (field === "paid_amount" && paidAmount === 0) {
    next.finance_review = "未付款";
  }
  return next;
}

function optionalNumber(value: unknown) {
  if (value === undefined || value === null || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function roundMoney(value: number) {
  return Math.round((value + Number.EPSILON) * 100) / 100;
}

function cellDisplayValue(row: GridRow, column: GridColumn) {
  if (column.key === "applicant") return requestApplicantName(row);
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

function externalImportDefaultDates(batch: Batch) {
  const today = new Date();
  const fallbackEnd = localIsoDate(today);
  const fallbackStartDate = new Date(today);
  fallbackStartDate.setDate(fallbackStartDate.getDate() - 6);
  const batchStart = batch.start_date || "";
  const batchEnd = batch.end_date || "";
  if (!batchStart || !batchEnd) return { dateFrom: localIsoDate(fallbackStartDate), dateTo: fallbackEnd };
  const start = new Date(`${batchStart}T00:00:00`);
  const end = new Date(`${batchEnd}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) {
    return { dateFrom: localIsoDate(fallbackStartDate), dateTo: fallbackEnd };
  }
  if ((end.getTime() - start.getTime()) / 86400000 > 30) {
    const clampedStart = new Date(end);
    clampedStart.setDate(clampedStart.getDate() - 30);
    return { dateFrom: localIsoDate(clampedStart), dateTo: batchEnd };
  }
  return { dateFrom: batchStart, dateTo: batchEnd };
}

function localIsoDate(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function requestApplicantName(request: Partial<PaymentRequest>) {
  if (request.applicant !== undefined && request.applicant !== null) return String(request.applicant).trim();
  const sourceName = String(request.raw_extra?.external_source?.applicant || "").trim();
  if (["unknown", "unknown user", "null", "none", "n/a", "-"].includes(sourceName.toLowerCase())) return "未识别人员";
  return sourceName;
}

function requestApplicantDepartment(request: Partial<PaymentRequest>) {
  return String(request.raw_extra?.external_source?.applicant_department || "").trim();
}

function requestDingTalkTerminated(request: Partial<PaymentRequest>) {
  return String(request.raw_extra?.external_source?.approval_status || "").trim().toUpperCase() === "TERMINATED";
}

function requestApplicantMeta(request: Partial<PaymentRequest>) {
  const department = requestApplicantDepartment(request);
  const isManual = request.applicant !== undefined && request.applicant !== null;
  if (isManual) return department ? `人工填写 · ${department}` : "人工填写";
  return department ? `钉钉同步 · ${department}` : "钉钉同步";
}

function requestApplicantTitle(request: Partial<PaymentRequest>) {
  const source = request.raw_extra?.external_source;
  const parts = [requestApplicantMeta(request)];
  if (source?.applicant_id) parts.push(`钉钉用户 ID：${source.applicant_id}`);
  if (source?.applicant) parts.push(`钉钉姓名：${source.applicant}`);
  return parts.join("\n");
}

function ExternalApprovalBadge({
  status,
  result,
  source,
  snapshot = false,
}: {
  status?: string;
  result?: string;
  source?: ExternalSourceSnapshot;
  snapshot?: boolean;
}) {
  const lookupStatus = source?.lookup_status;
  const normalized = String(source?.approval_status || status || "").toUpperCase();
  const normalizedResult = String(source?.approval_result || result || "").toLowerCase();
  const state = lookupStatus === "conflict"
    ? "conflict"
    : lookupStatus === "unmatched"
      ? "unmatched"
      : normalizedResult === "refuse"
        ? "refused"
        : normalized === "TERMINATED"
          ? "terminated"
          : normalized === "COMPLETED"
            ? "completed"
            : normalized === "RUNNING"
              ? "running"
              : "unknown";
  const labels: Record<string, string> = {
    completed: "已完成",
    running: "审批中",
    terminated: "已终止",
    refused: "已拒绝",
    unmatched: "未匹配",
    conflict: "来源冲突",
    unknown: source || status ? "未知" : "—",
  };
  const syncedAt = source?.metadata_synced_at;
  const title = syncedAt
    ? `最近同步：${formatDateTime(syncedAt)}`
    : snapshot
      ? "钉钉审批状态为导入时快照，可点击“同步钉钉流程”刷新"
      : undefined;
  return (
    <span className={`external-approval-badge ${state}`} title={title}>
      {snapshot && !syncedAt && state !== "unmatched" && state !== "conflict" ? "导入时·" : ""}{labels[state]}
    </span>
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

function isPdfAttachment(attachment: AttachmentLink) {
  return attachment.mime_type === "application/pdf"
    || String(attachment.original_filename || "").toLowerCase().endsWith(".pdf");
}

function isDingtalkAttachment(attachment: AttachmentLink) {
  return attachment.source_system === "dingtalk_expense_database";
}

function attachmentImageUrl(attachment: AttachmentLink) {
  return attachment.file_url || api.attachmentFileUrl(attachment.id);
}

function attachmentTitle(attachment: AttachmentLink, fallback: string) {
  return attachment.label || attachment.original_filename || fallback;
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatMoney(value: number) {
  return new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 2 }).format(value || 0);
}

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function workflowResultLabel(value: string) {
  const normalized = String(value || "").toUpperCase();
  return {
    AGREE: "同意",
    REFUSE: "拒绝",
    NONE: "处理",
  }[normalized] || value;
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
