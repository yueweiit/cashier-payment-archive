import { ClipboardEvent, DragEvent, FormEvent, Fragment, KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlignLeft,
  AlertTriangle,
  ArrowRight,
  Archive,
  ChevronLeft,
  ChevronRight,
  Download,
  Database,
  FileSpreadsheet,
  Filter,
  History,
  Image as ImageIcon,
  LayoutList,
  Languages,
  LogOut,
  MoreHorizontal,
  MessageSquareText,
  Paperclip,
  Plus,
  RefreshCcw,
  Save,
  Search,
  Shield,
  SlidersHorizontal,
  Table2,
  Trash2,
  Upload,
  Undo2,
  Users,
} from "lucide-react";
import {
  api,
  isApiError,
  AttachmentLink,
  AuditLog,
  Batch,
  BatchOperation,
  CurrencyCode,
  CurrencyConversionPreview,
  DailyPayableCurrencyTotal,
  DailyPayableDetail,
  DailyPayableSnapshot,
  DailyPayableTrend,
  ForeignAmountCorrectionPreview,
  HistoricalCurrencyRestorePreview,
  DingtalkWorkflow,
  EmployeeDepartmentImportResult,
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
  RequestGridPreference,
  RolloverCopyMode,
  User,
  UserRole,
  WeeklyMergeApplyResult,
  WeeklyMergePreview,
  WeeklyMergeResolution,
  WeeklyMergeRow,
} from "./api";
import { currentLanguage, LanguageProvider, useLanguage } from "./i18n";
import { buildDirtyGridPayload, sameSheetOrder } from "./gridSave";
import { AppNavigation, type AppTab } from "./AppNavigation";
import { MexicoTrackingPage } from "./MexicoTrackingPage";

type Tab = AppTab;
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
  currency: ["CNY", "USD", "MXN"],
  finance_review: financeApprovalOptions,
  general_manager_approval: generalManagerApprovalOptions,
};
const strictSelectFields = new Set<keyof PaymentRequest>(["currency", "finance_review", "general_manager_approval"]);

const roleLabels: Record<UserRole, string> = {
  business: "业务人员",
  finance: "财务",
  general_manager: "总经理",
  admin: "管理员",
};

const financeControlledFields = new Set<keyof PaymentRequest>([
  "currency",
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
  currency: "货币类型",
  base_amount_cny: "折合人民币金额",
  fx_rate_cny_per_unit: "人民币兑本币汇率",
  fx_rate_date: "汇率日期",
  fx_rate_actual_date: "实际汇率日期",
};

type GridRow = Partial<PaymentRequest> & {
  __localId: string;
  __isNew?: boolean;
  __deleted?: boolean;
};

type GridHeaderLanguage = "zh" | "es";
type MobileQuickFilter = "all" | "pending_approval" | "unpaid" | "partial" | "paid";
type MobileViewOverride = "cards" | "table" | null;

type GridColumn = {
  key: keyof PaymentRequest;
  labelZh: string;
  labelEs: string;
  width: number;
  type?: "number" | "date";
};

const gridColumns: GridColumn[] = [
  { key: "dingding_id", labelZh: "钉钉申请单号", labelEs: "Número de solicitud en DingTalk", width: 220 },
  { key: "source_sheet", labelZh: "应付款公司", labelEs: "Empresa a pagar", width: 220 },
  { key: "applicant", labelZh: "申请人", labelEs: "Solicitante", width: 210 },
  { key: "payment_account", labelZh: "账户性质", labelEs: "Tipo de cuenta", width: 150 },
  { key: "expense_type", labelZh: "支出性质", labelEs: "Naturaleza del gasto", width: 190 },
  { key: "style_name", labelZh: "支出类别", labelEs: "Categoría del gasto", width: 190 },
  { key: "summary", labelZh: "摘要", labelEs: "Resumen / Concepto", width: 360 },
  { key: "amount", labelZh: "应付金额", labelEs: "Monto a pagar", width: 140, type: "number" },
  { key: "paid_amount", labelZh: "已支付金额", labelEs: "Monto pagado", width: 140, type: "number" },
  { key: "pending_amount", labelZh: "待付款金额", labelEs: "Monto pendiente de pago", width: 180, type: "number" },
  { key: "currency", labelZh: "货币类型", labelEs: "Tipo de moneda", width: 140 },
  { key: "project", labelZh: "项目归属", labelEs: "Proyecto al que pertenece", width: 210 },
  { key: "bu", labelZh: "BU 归属", labelEs: "Unidad de negocio", width: 180 },
  { key: "payee_name", labelZh: "收款人名称", labelEs: "Nombre del beneficiario", width: 200 },
  { key: "payee_account", labelZh: "收款账号", labelEs: "Cuenta del beneficiario", width: 220 },
  { key: "bank_name", labelZh: "收款行", labelEs: "Banco / Sucursal del beneficiario", width: 240 },
  { key: "invoice_status", labelZh: "是否开具发票", labelEs: "¿Factura emitida?", width: 180 },
  { key: "needed_payment_date", labelZh: "需求付款日期", labelEs: "Fecha de pago requerida", width: 190, type: "date" },
  { key: "owner_confirmation", labelZh: "负责人确认", labelEs: "Confirmación del responsable", width: 190 },
  { key: "finance_review", labelZh: "财务审批", labelEs: "Revisión financiera", width: 170 },
  { key: "finance_manager_approval", labelZh: "财务主管审批", labelEs: "Aprobación del responsable financiero", width: 220 },
  { key: "general_manager_approval", labelZh: "总经理审批", labelEs: "Aprobación de dirección general", width: 210 },
  { key: "general_manager_approval_date", labelZh: "总经理审批时间", labelEs: "Fecha de aprobación de dirección", width: 220, type: "date" },
  { key: "general_manager_opinion", labelZh: "总经理意见", labelEs: "Opinión de dirección general", width: 260 },
  { key: "actual_payment_date", labelZh: "财务付款时间", labelEs: "Fecha real de pago", width: 190, type: "date" },
  { key: "payer", labelZh: "付款人", labelEs: "Pagador", width: 170 },
  { key: "remark", labelZh: "备注", labelEs: "Observaciones", width: 240 },
  { key: "overdue_status", labelZh: "逾期情况", labelEs: "Estado de vencimiento", width: 180 },
];

const defaultVisibleGridColumnKeys = new Set<keyof PaymentRequest>([
  "dingding_id",
  "source_sheet",
  "payment_account",
  "summary",
  "amount",
  "paid_amount",
  "pending_amount",
  "currency",
  "project",
  "payee_name",
  "needed_payment_date",
  "remark",
  "overdue_status",
]);

function defaultGridPreference(): RequestGridPreference {
  return {
    version: 1,
    order: gridColumns.map((column) => String(column.key)),
    hidden: gridColumns.filter((column) => !defaultVisibleGridColumnKeys.has(column.key)).map((column) => String(column.key)),
  };
}

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
  "style_name",
  "summary",
  "project",
  "payee_account",
  "payee_name",
  "bank_name",
  "invoice_status",
  "remark",
  "overdue_status",
  "source_sheet",
]);

function useSmallScreen() {
  const [isSmallScreen, setIsSmallScreen] = useState(() => window.matchMedia("(max-width: 980px)").matches);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 980px)");
    const update = () => setIsSmallScreen(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  return isSmallScreen;
}

export function App() {
  return <LanguageProvider><AppContent /></LanguageProvider>;
}

function AppContent() {
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

  const { t } = useLanguage();
  if (loading) return <div className="center-screen">{t("加载中", "Cargando")}</div>;
  if (!user) return <Login onLogin={setUser} />;

  return <Shell user={user} message={message} setMessage={setMessage} onLogout={() => setUser(null)} />;
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const { language, t, toggleLanguage } = useLanguage();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!username.trim() || !password) {
      setError("请输入账号和密码");
      return;
    }
    try {
      const res = await api.login(username.trim(), password);
      onLogin(res.user);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit} autoComplete="off">
        <button className="login-language-button ghost-button" type="button" onClick={toggleLanguage}>
          <Languages size={15} />{language === "zh" ? "Español" : "中文"}
        </button>
        <div className="brand-row">
          <FileSpreadsheet size={28} />
          <div>
            <h1>{t("出纳请款明细", "Detalle de solicitudes de pago de tesorería")}</h1>
            <span>{t("内网归档工作台", "Centro interno de pagos y archivo")}</span>
          </div>
        </div>
        <label>
          {t("账号", "Usuario")}
          <input
            value={username}
            name="cashier-username"
            autoComplete="off"
            autoFocus
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label>
          {t("密码", "Contraseña")}
          <input
            type="password"
            value={password}
            name="cashier-password"
            autoComplete="new-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && <p className="error-text">{error}</p>}
        <button className="primary-button" type="submit">
          <Shield size={16} />
          {t("登录", "Iniciar sesión")}
        </button>
      </form>
    </main>
  );
}

function GlobalFeedback({
  message,
  accountNotice,
  setMessage,
  setAccountNotice,
}: {
  message: string;
  accountNotice: string;
  setMessage: (message: string) => void;
  setAccountNotice: (message: string) => void;
}) {
  const { t } = useLanguage();

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), 5000);
    return () => window.clearTimeout(timer);
  }, [message, setMessage]);

  useEffect(() => {
    if (!accountNotice) return;
    const timer = window.setTimeout(() => setAccountNotice(""), 5000);
    return () => window.clearTimeout(timer);
  }, [accountNotice, setAccountNotice]);

  if (!message && !accountNotice) return null;

  const renderToast = (text: string, onDismiss: () => void, key: string) => {
    const separatorIndex = text.indexOf("：");
    const title = separatorIndex >= 0 ? text.slice(0, separatorIndex) : text;
    const detail = separatorIndex >= 0 ? text.slice(separatorIndex + 1).trim() : "";
    return (
      <div className="global-feedback-toast" role="status" key={key}>
        <div className="global-feedback-copy">
          <strong>{title}</strong>
          {detail && <span>{detail}</span>}
        </div>
        <button
          className="global-feedback-close"
          type="button"
          aria-label={t("关闭提示", "Cerrar aviso")}
          title={t("关闭", "Cerrar")}
          onClick={onDismiss}
        >
          ×
        </button>
      </div>
    );
  };

  return (
    <div className="global-feedback-viewport" aria-live="polite" aria-atomic="true">
      {message && renderToast(message, () => setMessage(""), "message")}
      {accountNotice && renderToast(accountNotice, () => setAccountNotice(""), "account")}
    </div>
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
  const { language, t, toggleLanguage } = useLanguage();
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

  async function logout() {
    await api.logout();
    onLogout();
  }

  return (
    <div className="app-shell">
      <header className="app-header" data-language={language}>
        <div className="app-brand">
          <FileSpreadsheet />
          <strong>{t("出纳请款明细", "Detalle de solicitudes de pago de tesorería")}</strong>
        </div>
        <AppNavigation
          tab={tab}
          canAdmin={isPrivilegedRole(user.role)}
          onSelect={setTab}
        />
        <div className="app-userbar">
          <button className="icon-text language-button" type="button" onClick={toggleLanguage} title={language === "zh" ? "Cambiar a español" : "切换为中文"}>
            <Languages size={15} />{language === "zh" ? "Español" : "中文"}
          </button>
          <button className="app-user account-button" type="button" title="修改密码" aria-label={`${user.display_name}，${roleLabels[user.role]}，修改密码`} onClick={() => setPasswordDialogOpen(true)}>
            <span>{user.display_name}</span>
            <small>{roleLabels[user.role]}</small>
          </button>
          <button className="icon-text" onClick={logout}>
            <LogOut size={15} />
            退出
          </button>
        </div>
      </header>
      <GlobalFeedback
        message={message}
        accountNotice={accountNotice}
        setMessage={setMessage}
        setAccountNotice={setAccountNotice}
      />
      <main className="main-pane">
        {tab !== "mexico-tracking" && (
          <header className="topbar">
            <h1>{tabTitle(tab, language)}</h1>
          </header>
        )}
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
        {tab === "daily-payables" && <DailyPayablesView setMessage={setMessage} />}
        {tab === "mexico-tracking" && <MexicoTrackingPage user={user} setMessage={setMessage} />}
        {tab === "archive" && (
          <ArchiveView
            user={user}
            batches={batches}
            selectedBatch={selectedBatch}
            setSelectedBatchId={setSelectedBatchId}
            onOpenBatch={(batchId) => {
              setSelectedBatchId(batchId);
              setTab("workspace");
            }}
            reloadBatches={loadBatches}
            setMessage={setMessage}
          />
        )}
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

function tabTitle(tab: Tab, language: GridHeaderLanguage = "zh") {
  return language === "es"
    ? { workspace: "Panel de la semana actual", "daily-payables": "Pagos diarios pendientes", "mexico-tracking": "Seguimiento de aprobaciones de México", archive: "Archivo histórico", admin: "Gestión de usuarios" }[tab]
    : { workspace: "当前周工作台", "daily-payables": "每日应付", "mexico-tracking": "墨西哥审批跟进", archive: "历史归档", admin: "用户管理" }[tab];
}

type DailyTrendCurrency = "CNY_EQ" | CurrencyCode;

function shiftIsoDate(value: string, days: number) {
  const dateValue = new Date(`${value}T12:00:00`);
  dateValue.setDate(dateValue.getDate() + days);
  return localIsoDate(dateValue);
}

function dailyCurrencyTotal(snapshot: DailyPayableSnapshot | undefined, currency: CurrencyCode): DailyPayableCurrencyTotal {
  return snapshot?.currency_totals.find((item) => item.currency === currency) || {
    currency,
    due_today: 0,
    paid_today: 0,
    end_pending: 0,
    overdue_pending: 0,
  };
}

function DailyPayablesView({ setMessage }: { setMessage: (message: string) => void }) {
  const { language, t } = useLanguage();
  const [selectedDate, setSelectedDate] = useState(localIsoDate(new Date()));
  const [snapshot, setSnapshot] = useState<DailyPayableSnapshot | null>(null);
  const [details, setDetails] = useState<DailyPayableDetail[]>([]);
  const [trend, setTrend] = useState<DailyPayableTrend | null>(null);
  const [trendCurrency, setTrendCurrency] = useState<DailyTrendCurrency>("CNY_EQ");
  const [detailCurrency, setDetailCurrency] = useState<"" | CurrencyCode>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    api.dailyPayablesDetails(selectedDate, detailCurrency)
      .then(async (response) => {
        const start = shiftIsoDate(selectedDate, -13) < response.history_start_date
          ? response.history_start_date
          : shiftIsoDate(selectedDate, -13);
        const trendResponse = await api.dailyPayablesTrend(start, selectedDate);
        if (cancelled) return;
        setSnapshot(response);
        setDetails(response.items || []);
        setTrend(trendResponse);
      })
      .catch((err) => {
        if (cancelled) return;
        const message = (err as Error).message;
        setSnapshot(null);
        setDetails([]);
        setTrend(null);
        setError(message);
        setMessage(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [detailCurrency, selectedDate, setMessage]);

  const trendValues = useMemo(() => (trend?.points || []).map((point) => {
    if (trendCurrency === "CNY_EQ") return Number(point.totals_cny.end_pending || 0);
    return Number(dailyCurrencyTotal(point as DailyPayableSnapshot, trendCurrency).end_pending || 0);
  }), [trend, trendCurrency]);

  const historyStart = snapshot?.history_start_date || trend?.history_start_date || selectedDate;
  const currencyCards: CurrencyCode[] = ["CNY", "USD", "MXN"];
  const dateLocale = language === "es" ? "es-MX" : "zh-CN";

  return (
    <section className="daily-payables-page">
      <div className="daily-payables-toolbar">
        <div>
          <strong>{t("查看日期", "Fecha de consulta")}</strong>
          <span>{t("按当天结束时的状态计算，不受以后付款影响", "Calculado al cierre del día, sin alterarse por pagos posteriores")}</span>
        </div>
        <label>
          <span>{t("选择日期", "Seleccionar fecha")}</span>
          <input type="date" min={historyStart} value={selectedDate} onChange={(event) => setSelectedDate(event.target.value)} />
        </label>
      </div>

      <div className="daily-history-note">
        <History size={16} />
        {t("历史数据自", "Datos históricos registrados desde")} <strong>{historyStart}</strong> {t("开始记录", "en adelante")}
      </div>

      {error && <div className="daily-payables-error" role="alert">{error}</div>}

      <div className="daily-payables-overview">
        {currencyCards.map((currency) => {
          const total = dailyCurrencyTotal(snapshot || undefined, currency);
          const active = detailCurrency === currency;
          return (
            <button
              className={`daily-currency-card currency-${currency.toLowerCase()}${active ? " active" : ""}`}
              key={currency}
              type="button"
              onClick={() => setDetailCurrency(active ? "" : currency)}
            >
              <span>{currencyLabel(currency)}</span>
              <div><small>{t("当天新增到期", "Nuevo vencimiento hoy")}</small><strong>{formatMoney(total.due_today, currency)}</strong></div>
              <div><small>{t("当日支付", "Pagado hoy")}</small><strong>{formatMoney(total.paid_today, currency)}</strong></div>
              <div className="daily-card-pending"><small>{t("日终待付", "Pendiente al cierre")}</small><strong>{formatMoney(total.end_pending, currency)}</strong></div>
              <small>{t("其中逾期", "Vencido")} {formatMoney(total.overdue_pending, currency)}</small>
            </button>
          );
        })}
      </div>

      <div className="daily-cny-summary">
        <div><span>{t("当天新增到期（折合人民币）", "Nuevo vencimiento hoy (equivalente CNY)")}</span><strong>{formatMoney(snapshot?.totals_cny.due_today || 0)}</strong></div>
        <div><span>{t("当日支付（折合人民币）", "Pagado hoy (equivalente CNY)")}</span><strong>{formatMoney(snapshot?.totals_cny.paid_today || 0)}</strong></div>
        <div><span>{t("日终待付（折合人民币）", "Pendiente al cierre (equivalente CNY)")}</span><strong>{formatMoney(snapshot?.totals_cny.end_pending || 0)}</strong></div>
        <div><span>{t("逾期待付（折合人民币）", "Vencido pendiente (equivalente CNY)")}</span><strong>{formatMoney(snapshot?.totals_cny.overdue_pending || 0)}</strong></div>
      </div>

      <div className="daily-payables-panel">
        <div className="daily-panel-head">
          <div>
            <h2>{t("近 14 日待付变化", "Evolución pendiente de los últimos 14 días")}</h2>
            <span>{t("点击趋势节点可查看当天明细", "Seleccione un punto para consultar el detalle del día")}</span>
          </div>
          <div className="daily-trend-switch" role="group" aria-label={t("趋势币种", "Moneda del gráfico")}>
            {(["CNY_EQ", "CNY", "USD", "MXN"] as DailyTrendCurrency[]).map((currency) => (
              <button className={trendCurrency === currency ? "active" : ""} key={currency} type="button" onClick={() => setTrendCurrency(currency)}>
                {currency === "CNY_EQ" ? t("折合人民币", "Equivalente CNY") : currency}
              </button>
            ))}
          </div>
        </div>
        <DailyPayablesTrendChart
          points={trend?.points || []}
          values={trendValues}
          currency={trendCurrency}
          selectedDate={selectedDate}
          onSelectDate={setSelectedDate}
        />
      </div>

      <div className="daily-payables-panel daily-details-panel">
        <div className="daily-panel-head">
          <div>
            <h2>{t("当天应付明细", "Detalle de pagos del día")}</h2>
            <span>{new Date(`${selectedDate}T12:00:00`).toLocaleDateString(dateLocale)} · {t("截至日终", "al cierre del día")}</span>
          </div>
          <div className="daily-counts">
            <span>{t("当日到期", "Vence hoy")} <strong>{snapshot?.counts.due_today || 0}</strong></span>
            <span>{t("日终未付", "Pendiente al cierre")} <strong>{snapshot?.counts.end_pending || 0}</strong></span>
            <span>{t("其中逾期", "Vencido")} <strong>{snapshot?.counts.overdue_pending || 0}</strong></span>
          </div>
        </div>
        <div className="daily-detail-filter">
          <button className={detailCurrency === "" ? "active" : ""} type="button" onClick={() => setDetailCurrency("")}>{t("全部币种", "Todas las monedas")}</button>
          {currencyCards.map((currency) => <button className={detailCurrency === currency ? "active" : ""} type="button" key={currency} onClick={() => setDetailCurrency(currency)}>{currency}</button>)}
        </div>
        {loading ? (
          <div className="daily-empty">{t("加载中", "Cargando")}</div>
        ) : details.length ? (
          <>
            <div className="daily-detail-table-wrap">
              <table className="daily-detail-table">
                <thead><tr>
                  <th>{t("状态", "Estado")}</th>
                  <th>{t("应付款公司", "Empresa a pagar")}</th>
                  <th>{t("申请人", "Solicitante")}</th>
                  <th>{t("摘要", "Resumen / Concepto")}</th>
                  <th>{t("需求付款日期", "Fecha requerida")}</th>
                  <th>{t("应付金额", "Monto a pagar")}</th>
                  <th>{t("当日支付", "Pagado hoy")}</th>
                  <th>{t("日终待付", "Pendiente al cierre")}</th>
                </tr></thead>
                <tbody>{details.map((item) => <DailyPayableDetailRow item={item} key={item.logical_request_id} />)}</tbody>
              </table>
            </div>
            <div className="daily-detail-cards">{details.map((item) => <DailyPayableDetailCard item={item} key={item.logical_request_id} />)}</div>
          </>
        ) : (
          <div className="daily-empty">{t("当天没有应付或逾期待付记录", "No hay pagos con vencimiento ni pendientes vencidos para este día")}</div>
        )}
      </div>
    </section>
  );
}

function DailyPayablesTrendChart({
  points,
  values,
  currency,
  selectedDate,
  onSelectDate,
}: {
  points: DailyPayableTrend["points"];
  values: number[];
  currency: DailyTrendCurrency;
  selectedDate: string;
  onSelectDate: (value: string) => void;
}) {
  const { t } = useLanguage();
  if (!points.length) return <div className="daily-chart-empty">{t("暂无趋势数据", "No hay datos de tendencia")}</div>;
  const width = 900;
  const height = 210;
  const paddingX = 46;
  const paddingTop = 18;
  const paddingBottom = 40;
  const maxValue = Math.max(...values, 1);
  const x = (index: number) => points.length === 1 ? width / 2 : paddingX + index * ((width - paddingX * 2) / (points.length - 1));
  const y = (value: number) => paddingTop + (1 - value / maxValue) * (height - paddingTop - paddingBottom);
  const polyline = values.map((value, index) => `${x(index)},${y(value)}`).join(" ");
  const displayCurrency = currency === "CNY_EQ" ? "CNY" : currency;
  return (
    <div className="daily-chart-scroll">
      <svg className="daily-trend-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={t("日终待付趋势", "Tendencia pendiente al cierre")}>
        {[0, 0.5, 1].map((ratio) => {
          const lineY = y(maxValue * ratio);
          return <line x1={paddingX} x2={width - paddingX} y1={lineY} y2={lineY} className="daily-chart-grid" key={ratio} />;
        })}
        <polyline points={polyline} className="daily-chart-line" />
        {points.map((point, index) => {
          const active = point.date === selectedDate;
          const showLabel = points.length <= 7 || index === 0 || index === points.length - 1 || index % 2 === 0;
          return <g className="daily-chart-point" key={point.date} onClick={() => onSelectDate(point.date)}>
            <circle cx={x(index)} cy={y(values[index])} r={active ? 7 : 5} className={active ? "active" : ""} />
            <title>{point.date} · {formatMoney(values[index], displayCurrency)}</title>
            {showLabel && <text x={x(index)} y={height - 14} textAnchor="middle">{point.date.slice(5)}</text>}
          </g>;
        })}
      </svg>
    </div>
  );
}

function DailyPayableStatus({ item }: { item: DailyPayableDetail }) {
  const { t } = useLanguage();
  if (item.is_overdue) return <span className="daily-status overdue">{t("逾期待付", "Vencido")}</span>;
  if (item.pending_amount > 0) return <span className="daily-status due">{t("当日待付", "Pendiente hoy")}</span>;
  return <span className="daily-status paid">{t("当日已结清", "Liquidado hoy")}</span>;
}

function DailyPayableDetailRow({ item }: { item: DailyPayableDetail }) {
  return <tr>
    <td><DailyPayableStatus item={item} /></td>
    <td>{item.source_sheet || "—"}</td>
    <td>{item.applicant || "—"}</td>
    <td className="daily-summary-cell" title={item.summary || ""}>{item.summary || "—"}</td>
    <td>{item.needed_payment_date}</td>
    <td className="amount">{formatMoney(item.amount, item.currency)}</td>
    <td className="amount">{formatMoney(item.paid_today, item.currency)}</td>
    <td className="amount daily-pending-amount">{formatMoney(item.pending_amount, item.currency)}</td>
  </tr>;
}

function DailyPayableDetailCard({ item }: { item: DailyPayableDetail }) {
  const { t } = useLanguage();
  return <article className="daily-detail-card">
    <header><DailyPayableStatus item={item} /><strong>{item.source_sheet || "—"}</strong></header>
    <p>{item.summary || "—"}</p>
    <div className="daily-detail-meta"><span>{t("申请人", "Solicitante")}：{item.applicant || "—"}</span><span>{t("需求付款日期", "Fecha requerida")}：{item.needed_payment_date}</span></div>
    <div className="daily-detail-amounts">
      <div><small>{t("应付", "A pagar")}</small><strong>{formatMoney(item.amount, item.currency)}</strong></div>
      <div><small>{t("当日支付", "Pagado hoy")}</small><strong>{formatMoney(item.paid_today, item.currency)}</strong></div>
      <div><small>{t("日终待付", "Pendiente al cierre")}</small><strong>{formatMoney(item.pending_amount, item.currency)}</strong></div>
    </div>
  </article>;
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
  const [employeeFile, setEmployeeFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<Record<string, string> | null>(null);
  const [headers, setHeaders] = useState<string[]>([]);
  const [mappingOpen, setMappingOpen] = useState(false);
  const [externalImportOpen, setExternalImportOpen] = useState(false);
  const [mergePreview, setMergePreview] = useState<WeeklyMergePreview | null>(null);
  const [importToolsOpen, setImportToolsOpen] = useState(false);
  const [busyAction, setBusyAction] = useState<"weekly" | "weekly-merge" | "dingtalk" | "employee-departments" | "rollback" | null>(null);
  const [syncOperation, setSyncOperation] = useState<BatchOperation | null>(null);
  const [weeklyInputKey, setWeeklyInputKey] = useState(0);
  const [dingtalkInputKey, setDingtalkInputKey] = useState(0);
  const [employeeInputKey, setEmployeeInputKey] = useState(0);
  const silentSyncBatchRef = useRef<number | null>(null);
  const syncStatusRefreshedRef = useRef<string | null>(null);
  const syncFinishedRef = useRef<string | null>(null);
  const syncRefreshPendingRef = useRef<string | null>(null);
  const hasUnsavedChangesRef = useRef(hasUnsavedChanges);

  useEffect(() => {
    hasUnsavedChangesRef.current = hasUnsavedChanges;
    if (!hasUnsavedChanges && syncRefreshPendingRef.current) {
      const operationId = syncRefreshPendingRef.current;
      syncRefreshPendingRef.current = null;
      syncStatusRefreshedRef.current = operationId;
      void reloadBatches().then(onImported);
    }
  }, [hasUnsavedChanges]);

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

  async function importEmployeeDepartments() {
    if (!employeeFile || !selectedBatch || selectedBatch.status !== "draft" || hasUnsavedChanges) return;
    setBusyAction("employee-departments");
    setMessage("");
    try {
      const result: EmployeeDepartmentImportResult = await api.importEmployeeDepartments(selectedBatch.id, employeeFile);
      setEmployeeFile(null);
      setEmployeeInputKey((value) => value + 1);
      const retained = result.missing_applicant + result.unmatched_applicant + result.ambiguous_applicant;
      await refreshAfterImport(
        `2级部门归组完成：导入 ${result.mapping_rows} 名员工，匹配 ${result.matched_requests} 条、移动 ${result.moved_requests} 条；${retained} 条未匹配请款保留原 Sheet`,
      );
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
    if (!selectedBatch || selectedBatch.status !== "draft") return;
    const runningForCurrentBatch = syncOperation?.status === "running" && syncOperation.batch_id === selectedBatch.id;
    if (runningForCurrentBatch) {
      if (!silent) setMessage(syncOperation.progress_message || "钉钉流程同步任务正在执行，可继续使用当前页面");
      return;
    }
    if (hasUnsavedChanges || busyAction !== null) return;
    if (!silent) setMessage("");
    try {
      const result = await api.syncExternalExpenseMetadata(selectedBatch.id, silent ? 300 : 0, true);
      if ("operation" in result) {
        setSyncOperation(result.operation);
        if (!silent) {
          setMessage(result.reused
            ? "已接入当前正在执行的钉钉同步任务，页面可继续操作"
            : "钉钉流程同步已在后台开始，页面可继续操作");
        }
        return;
      }
      if (result.status === "fresh") return;
      await reloadBatches();
      onImported();
      if (!silent) {
        const detail = result.auto_payment_mode === "preview"
          ? `发现 ${result.payment_candidates} 条自动付款候选、${result.review_required} 条待核对`
          : `新增 ${result.auto_payments} 笔自动付款、${result.review_required} 条待核对`;
        const attachmentDetail = `新增 ${result.attachment_synced || 0} 个附件${result.attachment_failed ? `、${result.attachment_failed} 个失败` : ""}`;
        setMessage(`钉钉流程同步完成：${detail}；${attachmentDetail}`);
        window.setTimeout(() => setMessage(""), 3000);
      }
    } catch (err) {
      if (!silent) setMessage((err as Error).message);
    }
  }

  function syncProgressLabel(operation: BatchOperation) {
    if (operation.progress_message) return operation.progress_message;
    return ({
      starting: "正在准备同步",
      metadata: "正在查询审批元数据",
      workflow: "正在查询流程状态和评论",
      status_commit: "正在更新流程状态和评论",
      status_committed: "流程状态已更新，正在检查附件",
      attachment_inventory: "正在查询附件清单",
      attachment_download: `正在同步 ${operation.progress_current}/${operation.progress_total} 个附件`,
      attachment_commit: "正在保存附件",
      completed: "钉钉流程同步完成",
      failed: "钉钉流程同步失败",
    } as Record<string, string>)[operation.stage] || "正在同步钉钉流程";
  }

  useEffect(() => {
    const operationId = syncOperation?.id;
    if (!operationId || syncOperation.status !== "running") return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const { operation } = await api.batchOperation(operationId);
        if (cancelled) return;
        setSyncOperation(operation);
        if (operation.partial_result?.status_committed && syncStatusRefreshedRef.current !== operation.id) {
          if (hasUnsavedChangesRef.current) {
            syncRefreshPendingRef.current = operation.id;
            setMessage("钉钉流程状态已更新；您当前的修改不会被覆盖，保存或放弃后列表会自动刷新");
          } else {
            syncStatusRefreshedRef.current = operation.id;
            await reloadBatches();
            onImported();
          }
        }
        if (operation.status === "succeeded") {
          if (syncFinishedRef.current === operation.id) return;
          syncFinishedRef.current = operation.id;
          if (syncStatusRefreshedRef.current !== operation.id && !hasUnsavedChangesRef.current) {
            await reloadBatches();
            onImported();
          } else if (syncStatusRefreshedRef.current !== operation.id) {
            syncRefreshPendingRef.current = operation.id;
          }
          const result = operation.result || {};
          const paymentDetail = result.auto_payment_mode === "preview"
            ? `发现 ${result.payment_candidates || 0} 条自动付款候选、${result.review_required || 0} 条待核对`
            : `新增 ${result.auto_payments || 0} 笔自动付款、${result.review_required || 0} 条待核对`;
          const attachmentDetail = `新增 ${result.attachment_synced || 0} 个附件${result.attachment_failed ? `、${result.attachment_failed} 个失败` : ""}`;
          setMessage(`钉钉流程同步完成：${paymentDetail}；${attachmentDetail}`);
          window.setTimeout(() => {
            setMessage("");
            setSyncOperation((current) => current?.id === operation.id ? null : current);
          }, 5000);
          return;
        }
        if (operation.status === "failed" || operation.status === "interrupted") {
          if (syncFinishedRef.current === operation.id) return;
          syncFinishedRef.current = operation.id;
          setMessage(operation.failure_reason || "钉钉流程同步失败，请重试");
          return;
        }
        timer = window.setTimeout(poll, 900);
      } catch (err) {
        if (!cancelled) timer = window.setTimeout(poll, 1800);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [syncOperation?.id]);

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
      <section className={`import-toolbar-panel${importToolsOpen ? " expanded" : ""}`} aria-label="数据导入与同步">
        <div className="import-toolbar-summary">
          <div className="import-toolbar-title">
            <strong>数据导入与同步</strong>
            <small>常用同步直接执行，文件导入按需展开</small>
          </div>
          <div className="import-toolbar-quick-actions">
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
              disabled={!selectedBatch || selectedBatch.status !== "draft" || (hasUnsavedChanges && !(syncOperation?.status === "running" && syncOperation.batch_id === selectedBatch.id)) || busyAction !== null}
              title={hasUnsavedChanges ? "请先保存或放弃未保存修改" : selectedBatch?.status === "archived" ? "只能同步草稿批次" : "刷新审批状态、流程评论和可信付款证据"}
            >
              <RefreshCcw size={15} className={syncOperation?.status === "running" ? "sync-progress-spinner" : undefined} />
              {syncOperation?.status === "running" && syncOperation.batch_id === selectedBatch?.id ? syncProgressLabel(syncOperation) : "同步钉钉流程"}
            </button>
            <button
              className={importToolsOpen ? "ghost-button active-toggle compact-import-button" : "ghost-button compact-import-button"}
              type="button"
              aria-expanded={importToolsOpen}
              onClick={() => setImportToolsOpen((open) => !open)}
            >
              <FileSpreadsheet size={15} />
              {importToolsOpen ? "收起文件导入" : "文件导入与归组"}
            </button>
          </div>
        </div>
        {syncOperation?.status === "running" && syncOperation.batch_id === selectedBatch?.id && (
          <div className="sync-task-progress" role="status" aria-live="polite">
            <div className="sync-task-progress-copy">
              <strong>{syncProgressLabel(syncOperation)}</strong>
              <span>后台任务执行中，您可以继续浏览和编辑；流程状态会先更新，附件随后补齐。</span>
            </div>
            {syncOperation.progress_total > 0 && (
              <div className="sync-task-progress-meter" aria-label={`同步进度 ${syncOperation.progress_current}/${syncOperation.progress_total}`}>
                <span style={{ width: `${Math.min(100, Math.max(0, syncOperation.progress_current / syncOperation.progress_total * 100))}%` }} />
              </div>
            )}
          </div>
        )}
        {importToolsOpen && <div className="topbar-import">
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
          <div className="topbar-import-group employee-department-import-group">
            <label className="compact-file-button">
              <Users size={15} />
              员工部门表
              <input
                key={employeeInputKey}
                type="file"
                accept=".xls,.xlsx"
                onChange={(event) => setEmployeeFile(event.target.files?.[0] || null)}
              />
            </label>
            <span className="compact-file-name" title={employeeFile?.name || ""}>{employeeFile?.name || "未选择"}</span>
            <button
              className="ghost-button compact-import-button"
              type="button"
              onClick={importEmployeeDepartments}
              disabled={!employeeFile || !selectedBatch || selectedBatch.status !== "draft" || hasUnsavedChanges || busyAction !== null}
              title={hasUnsavedChanges ? "请先保存或放弃未保存修改" : selectedBatch?.status === "archived" ? "只能调整草稿批次" : "按员工表的2级部门重新归组当前批次"}
            >
              <Users size={15} />
              {busyAction === "employee-departments" ? "归组中" : "按2级部门归组"}
            </button>
          </div>
          <div className="topbar-import-group rollback-import-group">
            <button className="ghost-button danger-button compact-import-button" type="button" onClick={rollbackLatestImport} disabled={!selectedBatch || busyAction !== null}>
              <Undo2 size={15} />
              {busyAction === "rollback" ? "撤回中" : "撤回最近导入"}
            </button>
          </div>
        </div>}
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
  const [sourceTypes, setSourceTypes] = useState<ExternalExpenseSourceType[]>(["operation", "purchase", "monthly"]);
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
          <label><input type="checkbox" checked={sourceTypes.includes("monthly")} onChange={() => toggleSource("monthly")} />月结付款</label>
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
                    <td className="amount">{row.amount === undefined || row.amount === null ? "—" : formatMoney(row.amount, row.currency)}</td>
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
  const { language } = useLanguage();
  const isSmallScreen = useSmallScreen();
  const [gridRows, setGridRows] = useState<GridRow[]>([]);
  const [dirtyCells, setDirtyCells] = useState<Set<string>>(new Set());
  const [deletedLocalIds, setDeletedLocalIds] = useState<Set<string>>(new Set());
  const [deletedSheetNames, setDeletedSheetNames] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState({
    q: "",
    payment_account: "",
    invoice_status: "",
    pending_amount_min: "",
    pending_amount_max: "",
    finance_review: "",
    general_manager_approval: "",
    dingtalk_lifecycle: "active",
    execution_region: "",
  });
  const [activeSheet, setActiveSheet] = useState(ALL_SHEET);
  const [editingSheet, setEditingSheet] = useState<{ key: string; value: string } | null>(null);
  const [newSheetName, setNewSheetName] = useState<string | null>(null);
  const [sheetOrder, setSheetOrder] = useState<string[]>([]);
  const [draggedSheet, setDraggedSheet] = useState<string | null>(null);
  const [sheetDropTarget, setSheetDropTarget] = useState<{ key: string; position: "before" | "after" } | null>(null);
  const [sheetOrderSaving, setSheetOrderSaving] = useState(false);
  const [wrapText, setWrapText] = useState(false);
  const gridHeaderLanguage: GridHeaderLanguage = language;
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [rolloverDialogOpen, setRolloverDialogOpen] = useState(false);
  const [editing, setEditing] = useState<Partial<PaymentRequest> | null>(null);
  const [editorDraft, setEditorDraft] = useState<Partial<PaymentRequest> | null>(null);
  const [editorDirty, setEditorDirty] = useState(false);
  const [attachmentCounts, setAttachmentCounts] = useState<Record<number, number>>({});
  const [selectedRows, setSelectedRows] = useState<number[]>([]);
  const [bulkMoveTargetSheet, setBulkMoveTargetSheet] = useState("");
  const [editorInitialTab, setEditorInitialTab] = useState<RequestEditorTab>("request");
  const [pendingEditorNavigation, setPendingEditorNavigation] = useState<PendingEditorNavigation | null>(null);
  const [editorNavigationBusy, setEditorNavigationBusy] = useState(false);
  const [batchMenuOpen, setBatchMenuOpen] = useState(false);
  const [mobileViewOverride, setMobileViewOverride] = useState<MobileViewOverride>(null);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const [mobileToolsOpen, setMobileToolsOpen] = useState(false);
  const [advancedFiltersOpen, setAdvancedFiltersOpen] = useState(false);
  const [currencyConversion, setCurrencyConversion] = useState<{ request: PaymentRequest; target: CurrencyCode } | null>(null);
  const [historicalCurrencyOpen, setHistoricalCurrencyOpen] = useState(false);
  const [columnSettingsOpen, setColumnSettingsOpen] = useState(false);
  const [gridPreference, setGridPreference] = useState<RequestGridPreference>(defaultGridPreference);
  const batchMenuRef = useRef<HTMLDivElement | null>(null);
  const [reason, setReason] = useState("");
  const hasUnsavedChanges = dirtyCells.size > 0 || deletedLocalIds.size > 0 || deletedSheetNames.size > 0;
  const showMobileCards = isSmallScreen && mobileViewOverride !== "table";

  useEffect(() => {
    api.requestGridPreference()
      .then((result) => setGridPreference(result.preference))
      .catch((err) => setMessage((err as Error).message));
  }, [user.id]);

  useEffect(() => {
    if (isSmallScreen) return;
    setMobileViewOverride(null);
    setMobileFiltersOpen(false);
    setMobileToolsOpen(false);
  }, [isSmallScreen]);

  useEffect(() => {
    setMobileFiltersOpen(false);
    setMobileToolsOpen(false);
  }, [selectedBatch?.id]);

  async function loadRequests() {
    if (!selectedBatch) return;
    const [requestsRes, attachmentsRes] = await Promise.all([
      api.requests(selectedBatch.id, { dingtalk_lifecycle: filters.dingtalk_lifecycle }),
      api.batchAttachments(selectedBatch.id),
    ]);
    const attachmentGroups = groupAttachmentsByRequest(attachmentsRes.attachments);
    setGridRows(toGridRows(requestsRes.requests));
    setAttachmentCounts(countAttachmentsByGroup(attachmentGroups));
    setDirtyCells(new Set());
    setDeletedLocalIds(new Set());
    setDeletedSheetNames(new Set());
    setSelectedRows([]);
    setBulkMoveTargetSheet("");
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
  }, [selectedBatch?.id, refreshToken, filters.dingtalk_lifecycle]);

  useEffect(() => {
    setSheetOrder(selectedBatch?.sheet_order || []);
    setDeletedSheetNames(new Set());
    setDraggedSheet(null);
    setSheetDropTarget(null);
  }, [selectedBatch?.id, selectedBatch?.sheet_order]);

  const sheetTabs = useMemo(
    () => getSheetTabs(gridRows, sheetOrder, deletedSheetNames),
    [gridRows, sheetOrder, deletedSheetNames],
  );
  const visibleRows = useMemo(() => gridRows.filter((row) => rowMatchesFilters(row, filters, activeSheet)), [gridRows, filters, activeSheet]);
  const visibleGridColumns = useMemo(() => {
    const byKey = new Map(gridColumns.map((column) => [String(column.key), column]));
    const hidden = new Set(gridPreference.hidden);
    return gridPreference.order
      .map((key) => byKey.get(key))
      .filter((column): column is GridColumn => Boolean(column) && !hidden.has(String(column!.key)));
  }, [gridPreference]);
  const visibleActiveRows = useMemo(() => visibleRows.filter((row) => !row.__deleted), [visibleRows]);
  const hasActiveExportFilter = activeSheet !== ALL_SHEET || Object.entries(filters).some(([key, value]) => (
    key === "dingtalk_lifecycle" ? value !== "active" : value.trim()
  ));
  const activeSheetRows = useMemo(
    () => (activeSheet === ALL_SHEET ? [] : gridRows.filter((row) => normalizeSheetName(row.source_sheet) === activeSheet)),
    [activeSheet, gridRows],
  );
  const visibleTotals = useMemo(
    () => ({
      count: visibleActiveRows.length,
      amount: visibleActiveRows.reduce((sum, row) => sum + requestAmountCny(row, "amount"), 0),
      paidAmount: visibleActiveRows.reduce((sum, row) => sum + requestAmountCny(row, "paid_amount"), 0),
      pendingAmount: visibleActiveRows.reduce((sum, row) => sum + requestAmountCny(row, "pending_amount"), 0),
    }),
    [visibleActiveRows],
  );
  const visibleCurrencyTotals = useMemo(() => currencySubtotals(visibleActiveRows), [visibleActiveRows]);
  const financeReviewCounts = useMemo(
    () => ({
      paid: visibleActiveRows.filter((row) => row.finance_review === "已付款").length,
      partial: visibleActiveRows.filter((row) => row.finance_review === "部分付款").length,
      unpaid: visibleActiveRows.filter((row) => row.finance_review === "未付款").length,
    }),
    [visibleActiveRows],
  );
  const showFilteredAmounts = hasActiveExportFilter;
  const mobileQuickFilter: MobileQuickFilter = filters.general_manager_approval === GENERAL_MANAGER_EMPTY_FILTER
    ? "pending_approval"
    : filters.finance_review === "未付款"
      ? "unpaid"
      : filters.finance_review === "部分付款"
        ? "partial"
        : filters.finance_review === "已付款"
          ? "paid"
          : "all";
  const firstVisibleSheet = sheetTabs.find((tab) => tab.key !== ALL_SHEET)?.key || "";
  const defaultSourceSheet = activeSheet === ALL_SHEET
    ? (user.role === "business" ? firstVisibleSheet : "手工录入")
    : activeSheet;
  const canManageBatchOperations = user.role !== "business";
  const canManageSheets = user.role !== "business";
  const canArchiveBatch = ["finance", "general_manager", "admin"].includes(user.role);
  const canRestoreBatch = isPrivilegedRole(user.role);
  const canManageDraftState = selectedBatch?.status === "draft" && isPrivilegedRole(user.role);
  const canEditGrid = selectedBatch?.status !== "archived" || isPrivilegedRole(user.role);
  const canCreateRequests = canEditGrid && (user.role !== "business" || Boolean(firstVisibleSheet));
  const canReorderSheets = canManageSheets && canEditGrid && !hasUnsavedChanges && !sheetOrderSaving;
  const batchPayableAmount = Number(selectedBatch?.total_amount) || 0;
  const batchPaidAmount = Number(selectedBatch?.total_paid_amount) || 0;
  const paymentProgress = batchPayableAmount > 0
    ? Math.min(100, Math.max(0, (batchPaidAmount / batchPayableAmount) * 100))
    : 0;
  const activeSheetPendingDeleteCount = activeSheetRows.filter((row) => row.__deleted).length;
  const canDeleteActiveSheet = canEditGrid
    && activeSheet !== ALL_SHEET
    && sheetTabs.some((tab) => tab.key === activeSheet)
    && !deletedSheetNames.has(activeSheet);
  const canRestoreActiveSheetDelete = canEditGrid && activeSheet !== ALL_SHEET && deletedSheetNames.has(activeSheet);

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
      await api.updateSheetOrder(selectedBatch.id, nextOrder, selectedBatch.version);
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
      const result = await api.updateRequest(selectedBatch.id, payload.id, {
        ...writablePayload,
        expected_version: Number(payload.version || 1),
        reason,
      });
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

  async function reloadEditingRequest(requestId: number) {
    if (!selectedBatch) return undefined;
    const result = await api.requests(selectedBatch.id, { dingtalk_lifecycle: "all" });
    const latest = result.requests.find((item) => item.id === requestId);
    if (!latest) {
      setMessage("该请款已不存在，可能已被其他用户删除");
      return undefined;
    }
    setEditing(latest);
    setEditorDraft(null);
    setEditorDirty(false);
    await loadRequests();
    await reloadBatches();
    return latest;
  }

  function requestCurrencyConversion(request: Partial<PaymentRequest>, target: string) {
    if (!request.id || !["CNY", "USD", "MXN"].includes(target)) return;
    if (hasUnsavedChanges || editorDirty) {
      setMessage("当前有未保存修改，请先保存或放弃后再切换币种");
      return;
    }
    const currentCurrency = String(request.currency || "CNY").toUpperCase();
    if (currentCurrency === target) return;
    setCurrencyConversion({ request: request as PaymentRequest, target: target as CurrencyCode });
  }

  async function currencyConversionApplied(updatedRequest: PaymentRequest) {
    setCurrencyConversion(null);
    if (editing?.id === updatedRequest.id) setEditing(updatedRequest);
    setEditorDraft(null);
    setEditorDirty(false);
    await loadRequests();
    await reloadBatches();
    setMessage(`币种已切换为 ${currencyLabel(updatedRequest.currency)}`);
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

  function applyMobileQuickFilter(filter: MobileQuickFilter) {
    setFilters((current) => ({
      ...current,
      finance_review: filter === "unpaid"
        ? "未付款"
        : filter === "partial"
          ? "部分付款"
          : filter === "paid"
            ? "已付款"
            : "",
      general_manager_approval: filter === "pending_approval" ? GENERAL_MANAGER_EMPTY_FILTER : "",
    }));
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
      .map((row) => ({
        id: row.id!,
        expected_version: Number(row.version || 1),
        ...buildDirtyGridPayload(
          row as unknown as Record<string, unknown>,
          dirtyCells,
          calculatedRequestFields as unknown as ReadonlySet<string>,
        ),
      }));
    const deletes = Array.from(deletedLocalIds)
      .map((localId) => gridRows.find((row) => row.__localId === localId))
      .filter((row): row is GridRow & { id: number } => Boolean(row?.id))
      .map((row) => ({ id: row.id, expected_version: Number(row.version || 1) }));
    try {
      let batchVersion = selectedBatch.version;
      if (creates.length || updates.length || deletes.length) {
        await api.bulkSaveRequests(selectedBatch.id, { creates, updates, deletes, reason });
        batchVersion = (await api.batch(selectedBatch.id)).batch.version;
      }
      const savedSheetOrder = getSheetTabs(gridRows, sheetOrder, deletedSheetNames)
        .filter((tab) => tab.key !== ALL_SHEET && !deletedSheetNames.has(tab.key))
        .map((tab) => tab.key);
      const sheetOrderChanged = !sameSheetOrder(savedSheetOrder, selectedBatch.sheet_order || []);
      if (canManageSheets && sheetOrderChanged) {
        await api.updateSheetOrder(selectedBatch.id, savedSheetOrder, batchVersion);
      }
      setSheetOrder(savedSheetOrder);
      setReason("");
      setDeletedSheetNames(new Set());
      await loadRequests();
      await reloadBatches();
      setMessage("表格更改已保存");
    } catch (error) {
      if (isApiError(error, "VERSION_CONFLICT")) {
        await loadRequests();
        await reloadBatches();
        setMessage(`${writeErrorMessage(error)}，服务端最新数据已重新加载`);
        return;
      }
      setMessage(writeErrorMessage(error));
    }
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
    setDeletedSheetNames(new Set());
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
      pending_amount_min: filters.pending_amount_min.trim(),
      pending_amount_max: filters.pending_amount_max.trim(),
      finance_review: filters.finance_review.trim(),
      general_manager_approval: filters.general_manager_approval.trim(),
      dingtalk_lifecycle: filters.dingtalk_lifecycle,
      execution_region: filters.execution_region,
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
    await api.restoreBatchBaseline(selectedBatch.id, selectedBatch.version);
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
    await api.setBatchBaseline(selectedBatch.id, selectedBatch.version);
    await reloadBatches();
    setMessage("当前草稿状态已设为还原点");
  }

  async function deleteCurrentDraft() {
    if (!selectedBatch || !canManageDraftState) return;
    if (!window.confirm(`确定删除草稿批次“${selectedBatch.name}”吗？删除后该批次下的请款、付款明细和附件凭证也会删除。`)) return;
    await api.deleteBatch(selectedBatch.id, selectedBatch.version);
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
    await api.archive(selectedBatch.id, selectedBatch.version);
    await reloadBatches();
    setMessage("批次已归档");
  }

  async function restoreCurrentBatchDraft() {
    if (!selectedBatch) return;
    if (hasUnsavedChanges) {
      setMessage("请先保存表格更改，再修改批次状态");
      return;
    }
    await api.unarchive(selectedBatch.id, selectedBatch.version);
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
    if (!canManageSheets || !canEditGrid) return;
    setEditingSheet(null);
    setNewSheetName(nextSheetName(sheetTabs));
  }

  async function commitCreateSheet() {
    if (newSheetName === null) return;
    const rawName = newSheetName.trim();
    if (!rawName) {
      setNewSheetName(null);
      setMessage("Sheet 名称不能为空，已取消新增");
      return;
    }
    const name = normalizeSheetName(rawName);
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
    setNewSheetName(null);
    if (!selectedBatch) return;
    const nextOrder = [...sheetTabs.filter((tab) => tab.key !== ALL_SHEET).map((tab) => tab.key), name];
    setSheetOrderSaving(true);
    try {
      await api.updateSheetOrder(selectedBatch.id, nextOrder, selectedBatch.version);
      setSheetOrder(nextOrder);
      setFilters({
        q: "",
        payment_account: "",
        invoice_status: "",
        pending_amount_min: "",
        pending_amount_max: "",
        finance_review: "",
        general_manager_approval: "",
        dingtalk_lifecycle: "active",
        execution_region: "",
      });
      setActiveSheet(name);
      await reloadBatches();
      setMessage(`已新增 Sheet：${name}`);
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setSheetOrderSaving(false);
    }
  }

  function handleCreateSheetKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    event.stopPropagation();
    if (event.key === "Enter") {
      event.preventDefault();
      void commitCreateSheet();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setNewSheetName(null);
    }
  }

  function beginRenameSheet(sheetKey: string) {
    if (!canManageSheets || sheetKey === ALL_SHEET) return;
    const rowsInSheet = gridRows.filter((row) => normalizeSheetName(row.source_sheet) === sheetKey);
    if (rowsInSheet.length > 0 && rowsInSheet.every((row) => row.__deleted)) return;
    setNewSheetName(null);
    setActiveSheet(sheetKey);
    setEditingSheet({ key: sheetKey, value: sheetKey });
  }

  async function commitRenameSheet() {
    if (!editingSheet) return;
    const oldName = editingSheet.key;
    const rawNewName = editingSheet.value.trim();
    if (!rawNewName) {
      setMessage("Sheet 名称不能为空，已取消重命名");
      setEditingSheet(null);
      return;
    }
    const newName = normalizeSheetName(rawNewName);
    if (newName === oldName) {
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
      if (!selectedBatch) return;
      const nextOrder = sheetOrder.map((name) => name === oldName ? newName : name);
      setSheetOrderSaving(true);
      try {
        await api.updateSheetOrder(selectedBatch.id, nextOrder, selectedBatch.version);
        setSheetOrder(nextOrder);
        setActiveSheet(newName);
        await reloadBatches();
        setMessage(`Sheet 已重命名为 ${newName}`);
      } catch (error) {
        setMessage((error as Error).message);
      } finally {
        setSheetOrderSaving(false);
      }
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
      void commitRenameSheet();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      setEditingSheet(null);
    }
  }

  function markDeleteSelected() {
    if (!canEditGrid) return;
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

  function markMoveSelected() {
    if (!canManageSheets || !canEditGrid || !bulkMoveTargetSheet) return;
    const targetSheet = normalizeSheetName(bulkMoveTargetSheet);
    const selected = new Set(selectedRows);
    const nextDirty = new Set(dirtyCells);
    let changedCount = 0;
    const nextRows = gridRows.map((row) => {
      if (!row.id || !selected.has(row.id) || row.__deleted || normalizeSheetName(row.source_sheet) === targetSheet) {
        return row;
      }
      nextDirty.add(`${row.__localId}:source_sheet`);
      changedCount += 1;
      return { ...row, source_sheet: targetSheet };
    });
    if (changedCount === 0) {
      setMessage(`所选记录已经位于 Sheet“${targetSheet}”`);
      return;
    }
    setGridRows(nextRows);
    setDirtyCells(nextDirty);
    setSelectedRows([]);
    setBulkMoveTargetSheet("");
    setMessage(`已将 ${changedCount} 条记录标记移动到 Sheet“${targetSheet}”，请点击“保存更改”`);
  }

  function markDeleteActiveSheet() {
    if (!canDeleteActiveSheet) return;
    const targetRows = activeSheetRows.filter((row) => !row.__deleted);
    const detail = targetRows.length
      ? `将把 ${targetRows.length} 行标记为待删除，并移除该 Sheet。`
      : "该 Sheet 当前没有请款记录，将直接移除 Sheet。";
    const confirmed = window.confirm(`确定删除 Sheet“${activeSheet}”吗？\n\n${detail}\n点击“保存更改”后生效。`);
    if (!confirmed) return;
    const targetLocalIds = new Set(targetRows.map((row) => row.__localId));
    const nextDeleted = new Set(deletedLocalIds);
    targetLocalIds.forEach((localId) => nextDeleted.add(localId));
    setGridRows(gridRows.map((row) => (targetLocalIds.has(row.__localId) ? { ...row, __deleted: true } : row)));
    setDeletedLocalIds(nextDeleted);
    setDeletedSheetNames(new Set([...deletedSheetNames, activeSheet]));
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
    const nextDeletedSheets = new Set(deletedSheetNames);
    nextDeletedSheets.delete(activeSheet);
    setDeletedSheetNames(nextDeletedSheets);
    setMessage(`已撤回 Sheet“${activeSheet}”的删除标记`);
  }

  if (!selectedBatch) {
    return (
      <div className="workspace-grid empty-workspace">
        <section className="content-panel empty-state">
          <h2>还没有批次</h2>
          <p>{canManageBatchOperations ? "先创建一个本周草稿，再开始录入或从 Excel 导入。" : "当前还没有可查看的批次，请联系管理员。"}</p>
          {canManageBatchOperations && (
            <button className="primary-button" onClick={() => setCreateDialogOpen(true)}>
              <Plus size={16} />
              新建批次
            </button>
          )}
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
            {canManageBatchOperations && (
              <button className="ghost-button batch-create-button" onClick={() => setCreateDialogOpen(true)}>
                <Plus size={16} />
                新建批次
              </button>
            )}
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
          <div className={`batch-primary-actions${isSmallScreen && !mobileToolsOpen ? " mobile-tools-collapsed" : ""}`}>
            {canManageBatchOperations && (
              <button className="primary-button" onClick={() => setRolloverDialogOpen(true)}>
                <Archive size={16} />
                从上周生成本周
              </button>
            )}
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
            {(canManageDraftState || user.role === "admin") && (
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
                    {canManageDraftState && (
                      <>
                        <button type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); void setCurrentBaseline(); }}>
                          <Save size={16} />
                          设为还原点
                        </button>
                        <button type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); void restoreInitialDraftState(); }}>
                          <Undo2 size={16} />
                          还原到初始状态
                        </button>
                      </>
                    )}
                    {user.role === "admin" && (
                      <button type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); setHistoricalCurrencyOpen(true); }}>
                        <RefreshCcw size={16} />
                        历史币种恢复
                      </button>
                    )}
                    {canManageDraftState && (
                      <>
                        <div className="batch-more-separator" role="separator" />
                        <button className="danger-menu-item" type="button" role="menuitem" onClick={() => { setBatchMenuOpen(false); void deleteCurrentDraft(); }}>
                          <Trash2 size={16} />
                          删除当前草稿
                        </button>
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="batch-metric-grid">
          <div className="batch-metric-card">
            <span>批次记录</span>
            <strong>{language === "es" ? `${selectedBatch.request_count || 0} registro(s)` : `${selectedBatch.request_count || 0} 条`}</strong>
          </div>
          <div className="batch-metric-card">
            <span>批次应付（折合人民币）</span>
            <strong>{formatMoney(batchPayableAmount)}</strong>
          </div>
          <div className="batch-metric-card">
            <span>累计已支付（折合人民币）</span>
            <strong>{formatMoney(batchPaidAmount)}</strong>
          </div>
          <div className="batch-metric-card">
            <span>待付款（折合人民币）</span>
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
        {!!selectedBatch.currency_totals?.length && (
          <details className="batch-currency-details">
            <summary>{language === "es" ? "Ver desglose por moneda" : "查看各币种明细"}</summary>
            <div className="batch-currency-subtotals" aria-label="批次各币种小计">
              {selectedBatch.currency_totals.map((subtotal) => (
                <div
                  className={`currency-detail-row currency-${subtotal.currency.toLowerCase()}`}
                  key={subtotal.currency}
                >
                  <strong className="currency-code">{subtotal.currency}</strong>
                  <span>
                    {language === "es"
                      ? `A pagar ${formatMoney(subtotal.amount, subtotal.currency)} · Pagado ${formatMoney(subtotal.paid_amount, subtotal.currency)} · Pendiente ${formatMoney(subtotal.pending_amount, subtotal.currency)}`
                      : `应付 ${formatMoney(subtotal.amount, subtotal.currency)} · 已付 ${formatMoney(subtotal.paid_amount, subtotal.currency)} · 待付 ${formatMoney(subtotal.pending_amount, subtotal.currency)}`}
                  </span>
                </div>
              ))}
            </div>
          </details>
        )}
      </section>
      {isSmallScreen && canManageBatchOperations && (
        <button
          className={mobileToolsOpen ? "ghost-button mobile-tools-toggle active-toggle" : "ghost-button mobile-tools-toggle"}
          type="button"
          aria-expanded={mobileToolsOpen}
          onClick={() => setMobileToolsOpen((open) => !open)}
        >
          <MoreHorizontal size={16} />
          {mobileToolsOpen ? "收起工具" : "更多工具"}
        </button>
      )}
      {canManageBatchOperations && (
        <div className={isSmallScreen && !mobileToolsOpen ? "mobile-import-tools-collapsed" : ""}>
          <TopbarImportActions
            selectedBatch={selectedBatch}
            hasUnsavedChanges={hasUnsavedChanges || editorDirty}
            reloadBatches={reloadBatches}
            onImported={onImported}
            setMessage={setMessage}
          />
        </div>
      )}
      <section className="content-panel">
        {isSmallScreen && (
          <div className="mobile-workspace-controls">
            <div className="mobile-search-row">
              <div className="search-box">
                <Search size={16} />
                <input
                  value={filters.q}
                  onChange={(event) => setFilters({ ...filters, q: event.target.value })}
                  placeholder={gridHeaderLanguage === "es" ? "Buscar solicitud, solicitante o resumen" : "搜索单号、申请人或摘要"}
                />
              </div>
              <button
                className={mobileFiltersOpen ? "ghost-button active-toggle" : "ghost-button"}
                type="button"
                aria-expanded={mobileFiltersOpen}
                onClick={() => setMobileFiltersOpen(true)}
              >
                <SlidersHorizontal size={16} />
                筛选
              </button>
            </div>
            <div className="mobile-view-actions" aria-label="手机显示模式">
              <div className="mobile-view-switcher">
                <button
                  className={showMobileCards ? "active" : ""}
                  type="button"
                  aria-pressed={showMobileCards}
                  onClick={() => setMobileViewOverride("cards")}
                >
                  <LayoutList size={15} />卡片
                </button>
                <button
                  className={!showMobileCards ? "active" : ""}
                  type="button"
                  aria-pressed={!showMobileCards}
                  onClick={() => setMobileViewOverride("table")}
                >
                  <Table2 size={15} />完整表格
                </button>
              </div>
            </div>
            <div className="mobile-quick-filters" aria-label="快捷筛选">
              {([
                ["all", gridHeaderLanguage === "es" ? "Todos" : "全部"],
                ["pending_approval", gridHeaderLanguage === "es" ? "Pendientes de mi aprobación" : "待我审批"],
                ["unpaid", gridHeaderLanguage === "es" ? "No pagado" : "未付款"],
                ["partial", gridHeaderLanguage === "es" ? "Pago parcial" : "部分付款"],
                ["paid", gridHeaderLanguage === "es" ? "Pagado" : "已付款"],
              ] as Array<[MobileQuickFilter, string]>).map(([value, label]) => (
                <button
                  key={value}
                  className={mobileQuickFilter === value ? "active" : ""}
                  type="button"
                  aria-pressed={mobileQuickFilter === value}
                  onClick={() => applyMobileQuickFilter(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}
        {showMobileCards && mobileFiltersOpen && (
          <button className="mobile-filter-backdrop" type="button" aria-label="关闭筛选" onClick={() => setMobileFiltersOpen(false)} />
        )}
        <div className={`toolbar workspace-toolbar${showMobileCards ? " mobile-card-filter-panel" : ""}${mobileFiltersOpen ? " open" : ""}`}>
          {showMobileCards && (
            <div className="mobile-filter-panel-head">
              <div><strong>筛选与操作</strong><span>调整完整筛选条件或执行其他操作</span></div>
              <button className="ghost-button" type="button" onClick={() => setMobileFiltersOpen(false)}>关闭</button>
            </div>
          )}
          <div className="workspace-filter-row">
            <div className="search-box desktop-toolbar-search">
              <Search size={16} />
              <input value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="搜索单号、申请人、摘要、收款方、项目" />
            </div>
            <div className="pending-amount-filter" aria-label="待付金额（折合人民币）区间">
              <span>待付金额</span>
              <input type="number" min="0" step="0.01" value={filters.pending_amount_min} onChange={(event) => setFilters({ ...filters, pending_amount_min: event.target.value })} placeholder="最低" aria-label="最低待付金额" />
              <span>—</span>
              <input type="number" min="0" step="0.01" value={filters.pending_amount_max} onChange={(event) => setFilters({ ...filters, pending_amount_max: event.target.value })} placeholder="最高" aria-label="最高待付金额" />
            </div>
            <select value={filters.finance_review} onChange={(event) => setFilters({ ...filters, finance_review: event.target.value })} aria-label="财务审批">
              <option value="">全部财务审批</option>
              {financeApprovalOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
            <select value={filters.general_manager_approval} onChange={(event) => setFilters({ ...filters, general_manager_approval: event.target.value })} aria-label="总经理审批">
              <option value="">全部总经理审批</option>
              <option value={GENERAL_MANAGER_EMPTY_FILTER}>未选择</option>
              {generalManagerApprovalFilterOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
            {!showMobileCards && (
              <button className={advancedFiltersOpen ? "ghost-button active-toggle" : "ghost-button"} type="button" aria-expanded={advancedFiltersOpen} onClick={() => setAdvancedFiltersOpen((open) => !open)}>
                <SlidersHorizontal size={16} />
                更多筛选
              </button>
            )}
          </div>
          {(advancedFiltersOpen || showMobileCards) && (
            <div className="workspace-advanced-filter-row">
              <input value={filters.payment_account} onChange={(event) => setFilters({ ...filters, payment_account: event.target.value })} placeholder="付款账户" />
              <input value={filters.invoice_status} onChange={(event) => setFilters({ ...filters, invoice_status: event.target.value })} placeholder="开票情况" />
              <select value={filters.execution_region} onChange={(event) => setFilters({ ...filters, execution_region: event.target.value })} aria-label={gridHeaderLanguage === "es" ? "Región de ejecución" : "执行地区"}>
                <option value="">{gridHeaderLanguage === "es" ? "Todas las regiones" : "全部地区"}</option>
                <option value="china">{gridHeaderLanguage === "es" ? "China" : "中国"}</option>
                <option value="mexico">{gridHeaderLanguage === "es" ? "México" : "墨西哥"}</option>
              </select>
              {user.role !== "business" && (
                <select value={filters.dingtalk_lifecycle} onChange={(event) => setFilters({ ...filters, dingtalk_lifecycle: event.target.value })} aria-label="钉钉流程范围" disabled={hasUnsavedChanges || editorDirty} title={hasUnsavedChanges || editorDirty ? "请先保存或放弃未保存修改" : "选择是否查看已终止或已拒绝的钉钉流程"}>
                  <option value="active">正常流程</option>
                  <option value="inactive">已终止/已拒绝</option>
                  <option value="all">全部流程</option>
                </select>
              )}
              <button className="ghost-button" onClick={() => { setMessage("筛选已应用"); setMobileFiltersOpen(false); }} onKeyDown={(event) => activateButtonByKeyboard(event, () => { setMessage("筛选已应用"); setMobileFiltersOpen(false); })}>
                <Filter size={16} />应用筛选
              </button>
            </div>
          )}
          <div className="workspace-action-row">
            <button className="ghost-button" type="button" onClick={exportCurrentResults} onKeyDown={(event) => activateButtonByKeyboard(event, exportCurrentResults)} disabled={hasUnsavedChanges || editorDirty || visibleActiveRows.length === 0} title={hasUnsavedChanges || editorDirty ? "请先保存或放弃未保存修改" : hasActiveExportFilter ? `导出当前筛选的 ${visibleActiveRows.length} 条记录` : `导出全部 ${visibleActiveRows.length} 条记录`}>
              <Download size={16} />{hasActiveExportFilter ? "导出筛选结果" : "导出全部"}
            </button>
            <button className="ghost-button" type="button" onClick={() => setColumnSettingsOpen(true)}>
              <SlidersHorizontal size={16} />列设置
            </button>
            {canCreateRequests && (
              <button className="ghost-button" type="button" onClick={addBlankRow} onKeyDown={(event) => activateButtonByKeyboard(event, addBlankRow)}>
                <Plus size={16} />插入空行
              </button>
            )}
            <button
              className={wrapText ? "ghost-button active-toggle" : "ghost-button"}
              type="button"
              onClick={() => setWrapText(!wrapText)}
              onKeyDown={(event) => activateButtonByKeyboard(event, () => setWrapText(!wrapText))}
              aria-pressed={wrapText}
            >
              <AlignLeft size={16} />{wrapText ? "取消换行" : "换行"}
            </button>
            {canCreateRequests && (
              <button className="primary-button" onClick={() => openRequestEditor({ ...emptyRequest, source_sheet: defaultSourceSheet })} onKeyDown={(event) => activateButtonByKeyboard(event, () => openRequestEditor({ ...emptyRequest, source_sheet: defaultSourceSheet }))}>
                <Plus size={16} />新增请款
              </button>
            )}
            {hasUnsavedChanges && (
              <button className="primary-button" onClick={saveGridChanges} onKeyDown={(event) => activateButtonByKeyboard(event, saveGridChanges)}>
                <Save size={16} />保存更改
              </button>
            )}
            {(hasUnsavedChanges || editorDirty) && (
              <button className="ghost-button" onClick={discardUnsavedChanges} onKeyDown={(event) => activateButtonByKeyboard(event, discardUnsavedChanges)}>
                <Undo2 size={16} />放弃修改
              </button>
            )}
          </div>
        </div>
        <div className="filtered-summary-bar" aria-label="当前筛选结果">
          <div className="filtered-summary-count">
            <span>当前筛选</span>
            <strong>{language === "es" ? `${visibleTotals.count} registro(s)` : `${visibleTotals.count} 条`}</strong>
          </div>
          {showFilteredAmounts && <div className="filtered-summary-amounts">
            <span>折合人民币应付 <strong>{formatMoney(visibleTotals.amount)}</strong></span>
            <span>已付 <strong>{formatMoney(visibleTotals.paidAmount)}</strong></span>
            <span>待付 <strong>{formatMoney(visibleTotals.pendingAmount)}</strong></span>
          </div>}
          {showFilteredAmounts && <div className="currency-subtotals" aria-label="各币种小计">
            {visibleCurrencyTotals.map((subtotal) => (
              <div className={`currency-detail-row currency-${subtotal.currency.toLowerCase()}`} key={subtotal.currency}>
                <strong className="currency-code">{subtotal.currency}</strong>
                <span>{language === "es" ? `A pagar ${formatMoney(subtotal.amount, subtotal.currency)} / Pendiente ${formatMoney(subtotal.pending_amount, subtotal.currency)}` : `应付 ${formatMoney(subtotal.amount, subtotal.currency)} / 待付 ${formatMoney(subtotal.pending_amount, subtotal.currency)}`}</span>
              </div>
            ))}
          </div>}
          <div className="filtered-summary-statuses">
            <button className={`summary-status paid${filters.finance_review === "已付款" ? " active" : ""}`} type="button" onClick={() => setFilters({ ...filters, finance_review: filters.finance_review === "已付款" ? "" : "已付款" })}>{language === "es" ? `Pagado ${financeReviewCounts.paid}` : `已付款 ${financeReviewCounts.paid} 单`}</button>
            <button className={`summary-status partial${filters.finance_review === "部分付款" ? " active" : ""}`} type="button" onClick={() => setFilters({ ...filters, finance_review: filters.finance_review === "部分付款" ? "" : "部分付款" })}>{language === "es" ? `Pago parcial ${financeReviewCounts.partial}` : `部分付款 ${financeReviewCounts.partial} 单`}</button>
            <button className={`summary-status unpaid${filters.finance_review === "未付款" ? " active" : ""}`} type="button" onClick={() => setFilters({ ...filters, finance_review: filters.finance_review === "未付款" ? "" : "未付款" })}>{language === "es" ? `No pagado ${financeReviewCounts.unpaid}` : `未付款 ${financeReviewCounts.unpaid} 单`}</button>
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
                  onBlur={() => void commitRenameSheet()}
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
                onDoubleClick={() => canManageSheets && beginRenameSheet(tab.key)}
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
                onBlur={() => void commitCreateSheet()}
                onKeyDown={handleCreateSheetKeyDown}
                aria-label="新增 Sheet 名称"
              />
              <small>0</small>
            </div>
          ) : (
            canManageSheets && canEditGrid && (
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
        {activeSheet !== ALL_SHEET && canManageSheets && canEditGrid && (
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
            {deletedSheetNames.size > 0 && `、${deletedSheetNames.size} 个 Sheet 待删除`}
            {selectedBatch.status === "archived" && <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="归档更正原因" />}
          </div>
        )}
        {selectedRows.length > 0 && (
          <div className="bulk-bar">
            <span>已选 {selectedRows.length} 条</span>
            {canManageSheets && canEditGrid && (
              <div className="bulk-move-controls">
                <select
                  value={bulkMoveTargetSheet}
                  onChange={(event) => setBulkMoveTargetSheet(event.target.value)}
                  aria-label="选择目标 Sheet"
                >
                  <option value="">选择目标 Sheet</option>
                  {sheetTabs
                    .filter((tab) => tab.key !== ALL_SHEET && !tab.pendingDelete)
                    .map((tab) => (
                      <option key={tab.key} value={tab.key}>
                        {tab.label}（{tab.count} 条）
                      </option>
                    ))}
                </select>
                <button className="ghost-button" type="button" onClick={markMoveSelected} disabled={!bulkMoveTargetSheet}>
                  <ArrowRight size={16} />
                  移动所选行
                </button>
              </div>
            )}
            <button className="ghost-button" onClick={markDeleteSelected} disabled={!canEditGrid}>
              <Trash2 size={16} />
              删除所选行
            </button>
          </div>
        )}
        {showMobileCards ? (
          <MobileRequestCardList
            rows={visibleActiveRows}
            user={user}
            headerLanguage={gridHeaderLanguage}
            attachmentCounts={attachmentCounts}
            onOpen={(request, tab) => openRequestEditor(request, tab)}
          />
        ) : (
          <EditablePaymentGrid
            rows={visibleRows}
            onRowsChange={mergeVisibleRows}
            dirtyCells={dirtyCells}
            setDirtyCells={setDirtyCells}
            deletedLocalIds={deletedLocalIds}
            selectedRows={selectedRows}
            setSelectedRows={setSelectedRows}
            readOnly={selectedBatch.status === "archived" && !isPrivilegedRole(user.role)}
            canEditField={(field) => canEditGrid && canEditRequestField(user.role, field) && (user.role !== "business" || field !== "source_sheet")}
            onEdit={openRequestEditor}
            onSave={saveGridChanges}
            defaultSourceSheet={defaultSourceSheet}
            wrapText={wrapText}
            headerLanguage={gridHeaderLanguage}
            onCurrencyChange={requestCurrencyConversion}
            columns={visibleGridColumns}
            attachmentCounts={attachmentCounts}
          />
        )}
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
          onReloadRequest={reloadEditingRequest}
          onDraftChange={setEditorDraft}
          onDirtyChange={setEditorDirty}
          onAttachmentsChanged={refreshAttachmentCounts}
          onPaymentsChanged={async (updatedRequest) => {
            setEditing(updatedRequest);
            await loadRequests();
            await reloadBatches();
          }}
          canEditAttachments={selectedBatch.status !== "archived" || isPrivilegedRole(user.role)}
          canEditField={(field) => canEditGrid && canEditRequestField(user.role, field) && (user.role !== "business" || field !== "source_sheet")}
          onCurrencyChange={requestCurrencyConversion}
        />
      )}
      {currencyConversion && selectedBatch && (
        <CurrencyConversionDialog
          batch={selectedBatch}
          request={currencyConversion.request}
          targetCurrency={currencyConversion.target}
          reason={reason}
          language={gridHeaderLanguage}
          onClose={() => setCurrencyConversion(null)}
          onApplied={currencyConversionApplied}
          onConflict={async () => {
            const latest = await reloadEditingRequest(currencyConversion.request.id);
            if (latest) {
              setCurrencyConversion((current) => current ? { ...current, request: latest } : current);
            }
            return latest;
          }}
        />
      )}
      {columnSettingsOpen && (
        <ColumnSettingsDialog
          preference={gridPreference}
          language={gridHeaderLanguage}
          onClose={() => setColumnSettingsOpen(false)}
          onSaved={(preference) => {
            setGridPreference(preference);
            setColumnSettingsOpen(false);
            setMessage(gridHeaderLanguage === "es" ? "Configuración de columnas guardada" : "列设置已保存");
          }}
        />
      )}
      {historicalCurrencyOpen && selectedBatch && (
        <HistoricalCurrencyRestoreDialog
          batch={selectedBatch}
          onClose={() => setHistoricalCurrencyOpen(false)}
          onApplied={async (count) => {
            setHistoricalCurrencyOpen(false);
            await loadRequests();
            await reloadBatches();
            setMessage(`已恢复 ${count} 条历史币种记录`);
          }}
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

function ColumnSettingsDialog({
  preference,
  language,
  onClose,
  onSaved,
}: {
  preference: RequestGridPreference;
  language: GridHeaderLanguage;
  onClose: () => void;
  onSaved: (preference: RequestGridPreference) => void;
}) {
  const [order, setOrder] = useState<string[]>(preference.order);
  const [hidden, setHidden] = useState<Set<string>>(new Set(preference.hidden));
  const [dragged, setDragged] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const columnByKey = useMemo(() => new Map(gridColumns.map((column) => [String(column.key), column])), []);

  function move(key: string, offset: number) {
    const index = order.indexOf(key);
    const target = index + offset;
    if (index < 0 || target < 0 || target >= order.length) return;
    const next = [...order];
    [next[index], next[target]] = [next[target], next[index]];
    setOrder(next);
  }

  function dropBefore(target: string) {
    if (!dragged || dragged === target) return;
    const next = order.filter((key) => key !== dragged);
    next.splice(next.indexOf(target), 0, dragged);
    setOrder(next);
    setDragged(null);
  }

  function toggle(key: string, visible: boolean) {
    const next = new Set(hidden);
    if (visible) next.delete(key);
    else if (order.length - next.size > 1) next.add(key);
    setHidden(next);
  }

  function reset() {
    const next = defaultGridPreference();
    setOrder(next.order);
    setHidden(new Set(next.hidden));
  }

  async function save() {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      const result = await api.updateRequestGridPreference({ order, hidden: order.filter((key) => hidden.has(key)) });
      onSaved(result.preference);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const es = language === "es";
  return (
    <Modal title={es ? "Configurar columnas" : "列设置"} onClose={saving ? () => undefined : onClose} className="column-settings-modal">
      <p className="form-hint">
        {es
          ? "El estado de pago queda fijo a la izquierda y los adjuntos a la derecha. Arrastre las demás columnas para ordenarlas."
          : "付款状态固定在左侧，附件固定在右侧；其余列可拖拽排序或隐藏。"}
      </p>
      <div className="column-settings-fixed">
        <span>{es ? "Fija: Estado de pago" : "固定：付款状态"}</span>
        <span>{es ? "Fija: Adjuntos" : "固定：附件"}</span>
      </div>
      <div className="column-settings-list">
        {order.map((key, index) => {
          const column = columnByKey.get(key);
          if (!column) return null;
          const visible = !hidden.has(key);
          return (
            <div
              className={`column-setting-row${dragged === key ? " dragging" : ""}`}
              key={key}
              draggable
              onDragStart={() => setDragged(key)}
              onDragOver={(event) => event.preventDefault()}
              onDrop={() => dropBefore(key)}
              onDragEnd={() => setDragged(null)}
            >
              <span className="column-drag-handle" aria-hidden="true">⋮⋮</span>
              <label>
                <input type="checkbox" checked={visible} onChange={(event) => toggle(key, event.target.checked)} />
                <span>{es ? column.labelEs : column.labelZh}</span>
              </label>
              <div className="column-order-buttons">
                <button type="button" className="icon-button" disabled={index === 0} onClick={() => move(key, -1)} aria-label={es ? "Subir" : "上移"}>↑</button>
                <button type="button" className="icon-button" disabled={index === order.length - 1} onClick={() => move(key, 1)} aria-label={es ? "Bajar" : "下移"}>↓</button>
              </div>
            </div>
          );
        })}
      </div>
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="modal-actions">
        <button className="ghost-button" type="button" onClick={reset} disabled={saving}>{es ? "Restablecer" : "恢复默认"}</button>
        <button className="ghost-button" type="button" onClick={onClose} disabled={saving}>{es ? "Cancelar" : "取消"}</button>
        <button className="primary-button" type="button" onClick={save} disabled={saving}><Save size={16} />{saving ? (es ? "Guardando" : "保存中") : (es ? "Guardar" : "保存")}</button>
      </div>
    </Modal>
  );
}

function CurrencyConversionDialog({
  batch,
  request,
  targetCurrency,
  reason,
  language,
  onClose,
  onApplied,
  onConflict,
}: {
  batch: Batch;
  request: PaymentRequest;
  targetCurrency: CurrencyCode;
  reason: string;
  language: GridHeaderLanguage;
  onClose: () => void;
  onApplied: (request: PaymentRequest) => Promise<void> | void;
  onConflict?: () => Promise<PaymentRequest | undefined>;
}) {
  const [mode, setMode] = useState<"convert" | "correct">("convert");
  const [rateDate, setRateDate] = useState(localIsoDate(new Date()));
  const [preview, setPreview] = useState<CurrencyConversionPreview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const maxDate = localIsoDate(new Date());

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setPreview(null);
    api.previewCurrencyConversion(batch.id, request.id, {
      target_currency: targetCurrency,
      rate_date: rateDate,
      mode,
      reason,
      expected_version: request.version,
      expected_updated_at: request.updated_at,
    })
      .then((result) => active && setPreview(result.preview))
      .catch((err) => active && setError(writeErrorMessage(err)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [batch.id, mode, rateDate, reason, request.id, request.updated_at, request.version, targetCurrency]);

  async function applyConversion() {
    if (!preview || applying) return;
    setApplying(true);
    setError("");
    try {
      const result = await api.applyCurrencyConversion(batch.id, request.id, {
        target_currency: targetCurrency,
        rate_date: rateDate,
        mode,
        reason,
        expected_version: preview.request_version || request.version,
        expected_updated_at: request.updated_at,
      });
      await onApplied(result.request);
    } catch (err) {
      if (isApiError(err, "VERSION_CONFLICT")) {
        const latest = await onConflict?.();
        setPreview(null);
        setError(`${writeErrorMessage(err)}${latest ? "；已刷新最新金额和版本，请重新确认" : ""}`);
      } else {
        setError(writeErrorMessage(err));
      }
    } finally {
      setApplying(false);
    }
  }

  const es = language === "es";
  return (
    <Modal title={es ? "Confirmar cambio de moneda" : "确认币种处理"} onClose={applying ? () => undefined : onClose} className="currency-conversion-modal">
      <div className="currency-conversion-route">
        <strong>{currencyLabel(request.currency)}</strong><ArrowRight size={18} /><strong>{currencyLabel(targetCurrency)}</strong>
      </div>
      <div className="currency-mode-options" role="radiogroup" aria-label={es ? "Modo de cambio" : "币种处理方式"}>
        <label className={mode === "convert" ? "selected" : ""}>
          <input type="radio" name="currency-mode" value="convert" checked={mode === "convert"} onChange={() => setMode("convert")} disabled={applying} />
          <span><strong>{es ? "Convertir por tipo de cambio" : "按汇率换算"}</strong><small>{es ? "El valor equivalente en CNY no cambia." : "人民币基准价值不变，金额按汇率换算。"}</small></span>
        </label>
        <label className={mode === "correct" ? "selected" : ""}>
          <input type="radio" name="currency-mode" value="correct" checked={mode === "correct"} onChange={() => setMode("correct")} disabled={applying} />
          <span><strong>{es ? "Corregir moneda sin cambiar el importe" : "金额不变，仅更正币种"}</strong><small>{es ? "25.000 CNY pasa a 25.000 USD; cambia el equivalente en CNY." : "例如 25,000 CNY 更正为 25,000 USD，人民币折算值随之变化。"}</small></span>
        </label>
      </div>
      <label className="currency-rate-date">
        {es ? "Fecha del tipo de cambio" : "汇率日期"}
        <input type="date" value={rateDate} max={maxDate} onChange={(event) => setRateDate(event.target.value)} disabled={applying} />
      </label>
      {loading && <div className="editor-info-banner">{es ? "Consultando el tipo de cambio…" : "正在读取汇率…"}</div>}
      {error && <p className="error-text" role="alert">{error}</p>}
      {preview && (
        <>
          <div className="currency-rate-summary">
            <div><span>{es ? "Tipo de cambio original" : "原币汇率"}</span><strong>1 {preview.source_currency} = ¥{Number(preview.source_rate || 1).toFixed(6)}</strong></div>
            <div><span>{es ? "Tipo de cambio destino" : "目标币汇率"}</span><strong>1 {preview.target_currency} = ¥{Number(preview.target_rate).toFixed(6)}</strong></div>
            <div><span>{es ? "Fecha seleccionada" : "选择日期"}</span><strong>{preview.requested_rate_date}</strong></div>
            <div><span>{es ? "Fecha aplicada" : "实际命中日期"}</span><strong>{preview.actual_rate_date}{preview.used_previous_rate ? (es ? " (fecha anterior más cercana)" : "（使用此前最近汇率）") : ""}</strong></div>
          </div>
          <div className="currency-amount-comparison">
            <div className="currency-comparison-head"><span>{es ? "Concepto" : "项目"}</span><span>{es ? "Antes" : "处理前"}</span><span>{es ? "Después" : "处理后"}</span></div>
            {(["amount", "paid_amount", "pending_amount"] as const).map((field) => (
              <div key={field}>
                <span>{es ? { amount: "A pagar", paid_amount: "Pagado", pending_amount: "Pendiente" }[field] : { amount: "应付", paid_amount: "已付", pending_amount: "待付" }[field]}</span>
                <strong>{formatMoney(preview.before[field], preview.source_currency)}</strong>
                <strong>{formatMoney(preview.after[field], preview.target_currency)}</strong>
              </div>
            ))}
          </div>
          <p className="currency-anchor-note">
            {mode === "convert"
              ? (es ? `El equivalente base se mantiene en ${formatMoney(preview.base_amount_cny)}; se convertirán ${preview.payment_count} pagos.` : `人民币基准金额保持为 ${formatMoney(preview.base_amount_cny)}；将同步换算 ${preview.payment_count} 笔付款明细。`)
              : (es ? `El importe numérico no cambia; el equivalente base pasa de ${formatMoney(preview.before_base_amount_cny)} a ${formatMoney(preview.base_amount_cny)}. Se corregirán ${preview.payment_count} pagos.` : `金额数值不变；人民币折算值将从 ${formatMoney(preview.before_base_amount_cny)} 更新为 ${formatMoney(preview.base_amount_cny)}，并同步更正 ${preview.payment_count} 笔付款明细。`)}
          </p>
        </>
      )}
      <div className="modal-actions">
        <button className="ghost-button" type="button" onClick={onClose} disabled={applying}>{es ? "Cancelar" : "取消"}</button>
        <button className="primary-button" type="button" onClick={applyConversion} disabled={!preview || loading || applying}>
          {applying ? (es ? "Guardando" : "处理中") : (es ? "Confirmar y guardar" : "确认处理并保存")}
        </button>
      </div>
    </Modal>
  );
}

function ForeignAmountCorrectionDialog({
  batch,
  request,
  amount,
  reason,
  language,
  onClose,
  onApplied,
  onConflict,
}: {
  batch: Batch;
  request: PaymentRequest;
  amount: number;
  reason: string;
  language: GridHeaderLanguage;
  onClose: () => void;
  onApplied: (request: PaymentRequest) => Promise<void> | void;
  onConflict?: () => Promise<PaymentRequest | undefined>;
}) {
  const [rateDate, setRateDate] = useState(localIsoDate(new Date()));
  const [preview, setPreview] = useState<ForeignAmountCorrectionPreview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const maxDate = localIsoDate(new Date());
  const es = language === "es";

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setPreview(null);
    api.previewForeignAmountCorrection(batch.id, request.id, {
      amount,
      rate_date: rateDate,
      reason,
      expected_version: request.version,
      expected_updated_at: request.updated_at,
    })
      .then((result) => active && setPreview(result.preview))
      .catch((err) => active && setError(writeErrorMessage(err)))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [amount, batch.id, rateDate, reason, request.id, request.updated_at, request.version]);

  async function applyCorrection() {
    if (!preview || applying) return;
    setApplying(true);
    setError("");
    try {
      const result = await api.applyForeignAmountCorrection(batch.id, request.id, {
        amount,
        rate_date: rateDate,
        reason,
        expected_version: preview.request_version || request.version,
        expected_updated_at: request.updated_at,
      });
      await onApplied(result.request);
    } catch (err) {
      if (isApiError(err, "VERSION_CONFLICT")) {
        const latest = await onConflict?.();
        setPreview(null);
        setError(`${writeErrorMessage(err)}${latest ? "；已刷新最新金额和版本，请重新确认" : ""}`);
      } else {
        setError(writeErrorMessage(err));
      }
    } finally {
      setApplying(false);
    }
  }

  return (
    <Modal title={es ? "Confirmar corrección del importe" : "确认外币应付金额"} onClose={applying ? () => undefined : onClose} className="currency-conversion-modal">
      <div className="currency-conversion-route">
        <strong>{formatMoney(Number(request.amount || 0), request.currency)}</strong>
        <ArrowRight size={18} />
        <strong>{formatMoney(amount, request.currency)}</strong>
      </div>
      <label className="currency-rate-date">
        {es ? "Fecha del tipo de cambio" : "汇率日期"}
        <input type="date" value={rateDate} max={maxDate} onChange={(event) => setRateDate(event.target.value)} disabled={applying} />
      </label>
      {loading && <div className="editor-info-banner">{es ? "Consultando el tipo de cambio…" : "正在读取汇率…"}</div>}
      {error && <p className="error-text" role="alert">{error}</p>}
      {preview && (
        <>
          <div className="currency-rate-summary">
            <div><span>{es ? "Tipo de cambio" : "采用汇率"}</span><strong>1 {preview.currency} = ¥{Number(preview.rate).toFixed(6)}</strong></div>
            <div><span>{es ? "Fecha seleccionada" : "选择日期"}</span><strong>{preview.requested_rate_date}</strong></div>
            <div><span>{es ? "Fecha aplicada" : "实际命中日期"}</span><strong>{preview.actual_rate_date}{preview.used_previous_rate ? (es ? " (fecha anterior más cercana)" : "（使用此前最近汇率）") : ""}</strong></div>
          </div>
          <div className="currency-amount-comparison">
            <div className="currency-comparison-head"><span>{es ? "Concepto" : "项目"}</span><span>{es ? "Antes" : "修改前"}</span><span>{es ? "Después" : "修改后"}</span></div>
            {(["amount", "paid_amount", "pending_amount"] as const).map((field) => (
              <div key={field}>
                <span>{es ? { amount: "A pagar", paid_amount: "Pagado", pending_amount: "Pendiente" }[field] : { amount: "应付", paid_amount: "已付", pending_amount: "待付" }[field]}</span>
                <strong>{formatMoney(preview.before[field], preview.currency)}</strong>
                <strong>{formatMoney(preview.after[field], preview.currency)}</strong>
              </div>
            ))}
          </div>
          <p className="currency-anchor-note">
            {es
              ? `El equivalente en CNY se actualizará de ${formatMoney(preview.before_base_amount_cny)} a ${formatMoney(preview.base_amount_cny)}. Los ${preview.payment_count} pagos existentes no cambiarán.`
              : `人民币折算值将从 ${formatMoney(preview.before_base_amount_cny)} 更新为 ${formatMoney(preview.base_amount_cny)}；已有 ${preview.payment_count} 笔付款金额保持不变。`}
          </p>
        </>
      )}
      <div className="modal-actions">
        <button className="ghost-button" type="button" onClick={onClose} disabled={applying}>{es ? "Cancelar" : "取消"}</button>
        <button className="primary-button" type="button" onClick={applyCorrection} disabled={!preview || loading || applying}>
          {applying ? (es ? "Guardando" : "处理中") : (es ? "Confirmar y guardar" : "确认并保存")}
        </button>
      </div>
    </Modal>
  );
}

function HistoricalCurrencyRestoreDialog({ batch, onClose, onApplied }: { batch: Batch; onClose: () => void; onApplied: (count: number) => Promise<void> | void }) {
  const [preview, setPreview] = useState<HistoricalCurrencyRestorePreview | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.previewHistoricalCurrencyRestore(batch.id)
      .then((result) => {
        setPreview(result);
        setSelected(new Set(result.rows.filter((row) => row.status === "recoverable").map((row) => row.request_id)));
      })
      .catch((err) => setError((err as Error).message));
  }, [batch.id]);

  async function applyRestore() {
    if (!selected.size || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await api.applyHistoricalCurrencyRestore(batch.id, {
        request_ids: Array.from(selected),
        reason,
        expected_batch_version: batch.version,
      });
      await onApplied(result.count);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="历史币种恢复" onClose={busy ? () => undefined : onClose} className="historical-currency-modal">
      <p className="muted-text">只恢复能够从可靠钉钉来源同时确认原币、原币金额和人民币基准金额的记录。</p>
      {preview && <div className="history-currency-summary">可恢复 {preview.summary.recoverable || 0} · 无法判断 {preview.summary.undetermined || 0} · 金额异常 {preview.summary.amount_error || 0}</div>}
      {error && <p className="error-text" role="alert">{error}</p>}
      <div className="history-currency-list">
        {!preview && !error && <div className="editor-info-banner">正在分析历史记录…</div>}
        {preview?.rows.map((row) => (
          <label className={row.status === "recoverable" ? "history-currency-row" : "history-currency-row disabled"} key={row.request_id}>
            <input
              type="checkbox"
              disabled={row.status !== "recoverable" || busy}
              checked={selected.has(row.request_id)}
              onChange={(event) => {
                const next = new Set(selected);
                event.target.checked ? next.add(row.request_id) : next.delete(row.request_id);
                setSelected(next);
              }}
            />
            <span><strong>{row.dingding_id || `请款 ${row.request_id}`}</strong><small>{row.source_sheet || "未分 Sheet"} · {row.applicant || "申请人未知"}</small></span>
            <span>{row.status === "recoverable" ? `${formatMoney(Number(row.base_amount_cny || 0))} → ${formatMoney(Number(row.source_amount || 0), row.source_currency)}` : (row.reasons || []).join("；")}</span>
          </label>
        ))}
      </div>
      {batch.status === "archived" && <input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="归档更正原因（必填）" disabled={busy} />}
      <div className="modal-actions">
        <button className="ghost-button" type="button" onClick={onClose} disabled={busy}>取消</button>
        <button className="primary-button" type="button" onClick={applyRestore} disabled={!selected.size || busy || (batch.status === "archived" && !reason.trim())}>{busy ? "恢复中" : `恢复选中 ${selected.size} 条`}</button>
      </div>
    </Modal>
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
  const [rolloverError, setRolloverError] = useState("");
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
    setRolloverError("");
    setCopyingMode(copyMode);
    try {
      const sourceBatch = batches.find((batch) => batch.id === Number(sourceBatchId));
      if (!sourceBatch) throw new Error("来源批次不存在，请刷新后重试");
      const res = await api.rolloverBatch(Number(sourceBatchId), {
        name,
        start_date: startDate,
        end_date: endDate,
        copy_mode: copyMode,
        expected_batch_version: sourceBatch.version,
      });
      setName("");
      setStartDate("");
      setEndDate("");
      setNameTouched(false);
      onCreated(res.batch);
      setMessage(copyMode === "all" ? `已生成本周草稿，复制全部 ${res.copied_count} 条记录` : `已生成本周草稿，复制 ${res.copied_count} 条未完成记录`);
    } catch (error) {
      const detail = (error as Error).message || "生成本周批次失败";
      setRolloverError(detail);
      setMessage(detail);
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
      {rolloverError && <p className="error-text" role="alert">{rolloverError}</p>}
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

function MobileRequestCardList({
  rows,
  user,
  headerLanguage,
  attachmentCounts,
  onOpen,
}: {
  rows: GridRow[];
  user: User;
  headerLanguage: GridHeaderLanguage;
  attachmentCounts: Record<number, number>;
  onOpen: (request: PaymentRequest, tab: RequestEditorTab) => void;
}) {
  const isSpanish = headerLanguage === "es";
  const labels = isSpanish ? {
    empty: "No hay solicitudes que coincidan con los filtros.",
    company: "Empresa a pagar",
    applicant: "Solicitante",
    payable: "Monto a pagar",
    paid: "Monto pagado",
    pending: "Monto pendiente",
    neededDate: "Fecha requerida",
    managerApproval: "Aprobación del director general",
    pendingApproval: "Pendiente",
    attachments: "Adjuntos",
    payments: "Pagos",
    details: "Ver detalles",
    approve: "Ver / Aprobar",
    unsaved: "Solicitud sin guardar",
  } : {
    empty: "没有符合当前筛选条件的请款。",
    company: "应付款公司",
    applicant: "申请人",
    payable: "应付金额",
    paid: "已支付",
    pending: "待付款",
    neededDate: "需求付款日期",
    managerApproval: "总经理审批",
    pendingApproval: "待审批",
    attachments: "附件",
    payments: "付款",
    details: "查看详情",
    approve: "查看 / 审批",
    unsaved: "尚未保存的请款",
  };

  if (rows.length === 0) return <div className="mobile-request-empty">{labels.empty}</div>;

  return (
    <div className="mobile-request-list" aria-label={isSpanish ? "Lista de solicitudes" : "请款卡片列表"}>
      {rows.map((row) => {
        const source = row.raw_extra?.external_source;
        const applicant = requestApplicantName(row) || (isSpanish ? "Sin completar" : "未填写");
        const attachmentCount = row.id ? Number(attachmentCounts[row.id] || 0) : 0;
        const canApprove = user.role === "general_manager" && Boolean(row.id) && !requestDingTalkTerminated(row);
        return (
          <article className="mobile-request-card" key={row.__localId}>
            <header className="mobile-request-card-head">
              <StatusPill value={String(row.finance_review || "未付款")} />
              {source ? <ExternalApprovalBadge source={source} snapshot /> : <span className="external-status-empty">—</span>}
            </header>
            <div className="mobile-request-card-title">
              <strong>{row.summary || labels.unsaved}</strong>
              {row.dingding_id && <small className="mono">{row.dingding_id}</small>}
            </div>
            <dl className="mobile-request-context">
              <div><dt>{labels.company}</dt><dd>{row.source_sheet || "—"}</dd></div>
              <div><dt>{labels.applicant}</dt><dd>{applicant}</dd></div>
            </dl>
            <div className="mobile-request-amounts">
              <div><span>{labels.payable}</span><strong>{formatMoney(Number(row.amount || 0), row.currency)}</strong></div>
              <div><span>{labels.paid}</span><strong>{formatMoney(Number(row.paid_amount || 0), row.currency)}</strong></div>
              <div className="pending"><span>{labels.pending}</span><strong>{formatMoney(Number(row.pending_amount || 0), row.currency)}</strong></div>
            </div>
            <dl className="mobile-request-meta">
              <div><dt>{labels.neededDate}</dt><dd>{row.needed_payment_date || "—"}</dd></div>
              <div><dt>{labels.managerApproval}</dt><dd>{row.general_manager_approval || labels.pendingApproval}</dd></div>
            </dl>
            <footer className="mobile-request-card-actions">
              <div className="mobile-request-counts">
                <span>{labels.payments} {Number(row.payment_count || 0)}</span>
                <span>{labels.attachments} {attachmentCount}</span>
              </div>
              {row.id && (
                <button
                  className={canApprove ? "primary-button" : "ghost-button"}
                  type="button"
                  onClick={() => onOpen(row as PaymentRequest, canApprove ? "approval" : "request")}
                >
                  {canApprove ? labels.approve : labels.details}
                </button>
              )}
            </footer>
          </article>
        );
      })}
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
  onSave,
  defaultSourceSheet,
  wrapText,
  headerLanguage,
  canEditField,
  onCurrencyChange,
  columns,
  attachmentCounts,
}: {
  rows: GridRow[];
  onRowsChange: (rows: GridRow[]) => void;
  dirtyCells: Set<string>;
  setDirtyCells: (cells: Set<string>) => void;
  deletedLocalIds: Set<string>;
  selectedRows: number[];
  setSelectedRows: (ids: number[]) => void;
  onEdit: (request: PaymentRequest, tab?: RequestEditorTab) => void;
  readOnly: boolean;
  onSave: () => void;
  defaultSourceSheet: string;
  wrapText: boolean;
  headerLanguage: GridHeaderLanguage;
  canEditField: (field: keyof PaymentRequest) => boolean;
  onCurrencyChange: (request: Partial<PaymentRequest>, target: string) => void;
  columns: GridColumn[];
  attachmentCounts: Record<number, number>;
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
  }, [rows.length, columns]);

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
    const boundedCol = Math.max(0, Math.min(colIndex, columns.length - 1));
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
          const column = columns[activeCell.col + colOffset];
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
              <th className="payment-detail-col fixed-grid-column">{headerLanguage === "es" ? "Estado de pago" : "付款状态"}</th>
              {columns.map((column) => (
                <th key={column.key} style={{ width: column.width, minWidth: column.width }}>
                  {headerLanguage === "es" ? column.labelEs : column.labelZh}
                </th>
              ))}
              <th className="attachment-col fixed-grid-column">{headerLanguage === "es" ? "Adjuntos" : "附件"}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={row.__localId} className={deletedLocalIds.has(row.__localId) ? "row-deleted" : ""} onDoubleClick={() => row.id && onEdit(row as PaymentRequest)}>
                <td className="checkbox-col">
                  {row.id && <input type="checkbox" checked={selectedRows.includes(row.id)} onChange={() => toggle(row.id!)} />}
                </td>
                <td className="payment-detail-col fixed-grid-column">
                  <button
                    className={`payment-detail-chip${Number(row.payment_count || 0) > 0 ? " has-payments" : ""}`}
                    type="button"
                    disabled={!row.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (row.id) onEdit(row as PaymentRequest, "payments");
                    }}
                  >
                    {Number(row.payment_count || 0) > 0
                      ? (headerLanguage === "es" ? `${Number(row.payment_count || 0)} pago(s)` : `付款 ${Number(row.payment_count || 0)} 笔`)
                      : (headerLanguage === "es" ? "No pagado" : "未付款")}
                  </button>
                </td>
	                {columns.map((column, colIndex) => {
	                  const dirty = dirtyCells.has(`${row.__localId}:${column.key}`);
	                  const cellValue = cellDisplayValue(row, column);
	                  const selectOptions = selectOptionsForField(column.key, cellValue);
	                  const shouldWrap = wrapText && wrappableColumnKeys.has(column.key) && column.type !== "number" && column.type !== "date";
	                  const terminatedManagerField = requestDingTalkTerminated(row) && generalManagerControlledFields.has(column.key);
	                  const cellReadOnly = readOnly || row.__deleted || !canEditField(column.key) || terminatedManagerField
	                    || (column.key === "currency" && Boolean(row.__isNew))
	                    || (column.key === "amount" && currencyCode(row.currency) !== "CNY");
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
                            onChange={(event) => {
                              if (column.key === "currency" && row.id) {
                                onCurrencyChange(row, event.target.value);
                                return;
                              }
                              updateCell(rowIndex, column, event.target.value);
                            }}
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
                <td className="attachment-col fixed-grid-column">
                  <button
                    className={`attachment-chip${row.id && Number(attachmentCounts[row.id] || 0) > 0 ? " has-attachments" : ""}`}
                    type="button"
                    disabled={!row.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      if (row.id) onEdit(row as PaymentRequest, "attachments");
                    }}
                  >
                    <Paperclip size={14} />
                    {row.id && Number(attachmentCounts[row.id] || 0) > 0
                      ? (headerLanguage === "es" ? `${Number(attachmentCounts[row.id] || 0)} archivo(s)` : `附件 ${Number(attachmentCounts[row.id] || 0)}`)
                      : (headerLanguage === "es" ? "Subir" : "上传")}
                  </button>
                </td>
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
  onReloadRequest,
  onDraftChange,
  onDirtyChange,
  onAttachmentsChanged,
  onPaymentsChanged,
  canEditAttachments,
  canEditField,
  onCurrencyChange,
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
  onReloadRequest: (requestId: number) => Promise<PaymentRequest | undefined>;
  onDraftChange?: (request: Partial<PaymentRequest> | null) => void;
  onDirtyChange?: (dirty: boolean) => void;
  onAttachmentsChanged?: () => Promise<void> | void;
  onPaymentsChanged?: (request: PaymentRequest) => Promise<void> | void;
  canEditAttachments: boolean;
  canEditField: (field: keyof PaymentRequest) => boolean;
  onCurrencyChange: (request: Partial<PaymentRequest>, target: string) => void;
}) {
  const { language } = useLanguage();
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
  const [saveConflict, setSaveConflict] = useState(false);
  const [previewImages, setPreviewImages] = useState<{ images: AttachmentLink[]; index: number } | null>(null);
  const [foreignAmountCorrection, setForeignAmountCorrection] = useState<number | null>(null);
  const fields: Array<keyof PaymentRequest> = [
    "dingding_id",
    "applicant",
    "payment_account",
    "expense_type",
    "summary",
    "amount",
    "currency",
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
    setSaveConflict(false);
    setAttachments([]);
    setWorkflow(null);
    setWorkflowError("");
    setAttachmentForm({ label: "", url_path: "" });
    setImageLabel("");
    setImageFile(null);
    setAttachmentMode(null);
    setSaveError("");
    setPreviewImages(null);
    setForeignAmountCorrection(null);
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
      if (event.key === "Escape" && !previewImages && foreignAmountCorrection === null && !confirmationOpen) onCancel();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [confirmationOpen, foreignAmountCorrection, onCancel, previewImages]);

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
    const fieldEditable = canEditField(field)
      && !(requestDingTalkTerminated(form) && generalManagerControlledFields.has(field))
      && !(field === "amount" && currencyCode(form.currency) !== "CNY" && !["finance", "general_manager", "admin"].includes(user.role));
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
            <strong>{moneyFields.has(field) ? formatMoney(Number(value || 0), form.currency) : String(value || "未填写")}</strong>
          )}
        </div>
      );
    }
    return (
      <label className={className} key={field}>
        {label}
        {selectOptionsForField(field, String(value || "")) ? (
          <select
            value={String(value || "")}
            disabled={field === "currency" && !form.id}
            title={field === "currency" && !form.id ? "请先保存请款，再通过汇率确认切换币种" : undefined}
            onChange={(event) => {
              if (field === "currency" && form.id) {
                onCurrencyChange(form, event.target.value);
                return;
              }
              setForm(withPaymentAmountChange(form, field, event.target.value));
            }}
          >
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
    const amountChanged = Boolean(form.id)
      && currencyCode(form.currency) !== "CNY"
      && Math.abs(Number(form.amount || 0) - Number(request.amount || 0)) > 0.000001;
    if (amountChanged) {
      if (Number(form.amount || 0) <= 0) {
        setSaveError("应付金额必须大于 0");
        return;
      }
      if (Number(form.amount || 0) + 0.000001 < Number(form.paid_amount || 0)) {
        setSaveError(`应付金额不能低于累计已支付金额 ${formatMoney(Number(form.paid_amount || 0), form.currency)}`);
        return;
      }
      setSaveError("");
      setForeignAmountCorrection(Number(form.amount));
      return;
    }
    setSaving(true);
    setSaveError("");
    setSaveConflict(false);
    try {
      const savedRequest = await onSave(form);
      if (savedRequest) setForm(savedRequest);
    } catch (err) {
      setSaveConflict(isApiError(err, "VERSION_CONFLICT"));
      setSaveError(writeErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  function discardRequestChanges() {
    setForm(request);
    setSaveError("");
    setSaveConflict(false);
  }

  async function copyConflictDraft() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(withoutDerivedPaymentFields(form), null, 2));
      setSaveError("当前修改已复制，可刷新最新数据后重新填写");
    } catch {
      setSaveError("浏览器未允许复制，请先不要关闭抽屉，手工保留当前修改");
    }
  }

  async function refreshAfterConflict() {
    if (!form.id || saving) return;
    setSaving(true);
    try {
      const latest = await onReloadRequest(form.id);
      if (latest) {
        setForm(latest);
        setSaveConflict(false);
        setSaveError("");
      }
    } catch (err) {
      setSaveError(writeErrorMessage(err));
    } finally {
      setSaving(false);
    }
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
          <div><span>应付金额</span><strong>{formatMoney(payableAmount, form.currency)}</strong></div>
          <div><span>累计已付</span><strong>{formatMoney(paidAmount, form.currency)}</strong></div>
          <div><span>待付款</span><strong>{formatMoney(pendingAmount, form.currency)}</strong></div>
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
                {renderField("currency")}
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
              onRefreshRequest={async () => {
                const latest = await onReloadRequest(form.id!);
                if (latest) setForm(latest);
                return latest;
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
        {saveConflict && (
          <div className="editor-conflict-actions">
            <button className="ghost-button" type="button" onClick={copyConflictDraft} disabled={saving}>复制当前修改</button>
            <button className="ghost-button" type="button" onClick={refreshAfterConflict} disabled={saving}>刷新后重新编辑</button>
          </div>
        )}
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
      {foreignAmountCorrection !== null && request.id && (
        <ForeignAmountCorrectionDialog
          batch={batch}
          request={form as PaymentRequest}
          amount={foreignAmountCorrection}
          reason={reason}
          language={language}
          onClose={() => setForeignAmountCorrection(null)}
          onConflict={async () => {
            const result = await api.requests(batch.id, { dingtalk_lifecycle: "all" });
            const latest = result.requests.find((item) => item.id === request.id);
            if (latest) setForm(latest);
            return latest;
          }}
          onApplied={async (updatedRequest) => {
            setForeignAmountCorrection(null);
            const correctedForm: Partial<PaymentRequest> = {
              ...form,
              amount: updatedRequest.amount,
              paid_amount: updatedRequest.paid_amount,
              pending_amount: updatedRequest.pending_amount,
              finance_review: updatedRequest.finance_review,
              base_amount_cny: updatedRequest.base_amount_cny,
              fx_rate_cny_per_unit: updatedRequest.fx_rate_cny_per_unit,
              fx_rate_date: updatedRequest.fx_rate_date,
              fx_rate_actual_date: updatedRequest.fx_rate_actual_date,
              version: updatedRequest.version,
              updated_at: updatedRequest.updated_at,
            };
            setSaving(true);
            setSaveError("");
            try {
              const savedRequest = await onSave(correctedForm);
              if (savedRequest) setForm(savedRequest);
            } catch (err) {
              setForm(correctedForm);
              setSaveError(`应付金额已更正，但其他字段保存失败：${(err as Error).message}`);
            } finally {
              setSaving(false);
            }
          }}
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
  onRefreshRequest,
}: {
  batch: Batch;
  request: PaymentRequest;
  reason: string;
  canManage: boolean;
  onRequestChanged: (request: PaymentRequest) => Promise<void> | void;
  onRefreshRequest?: () => Promise<PaymentRequest | undefined>;
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
    loadPayments().catch((err) => setError(writeErrorMessage(err)));
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
      setError(`本次金额不能超过 ${formatMoney(maxPayable, request.currency)}`);
      return;
    }
    if (batch.status === "archived" && !reason.trim()) {
      setError("归档后更正付款必须填写原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const payload = {
        ...paymentForm,
        reason,
        expected_request_version: request.version,
      };
      const result = editingPaymentId
        ? await api.updatePayment(batch.id, request.id, editingPaymentId, {
            ...payload,
            expected_payment_version: Number(editingPayment?.version || 1),
          })
        : await api.createPayment(batch.id, request.id, payload);
      resetPaymentForm();
      await loadPayments();
      await onRequestChanged(result.request);
    } catch (err) {
      if (isApiError(err, "VERSION_CONFLICT")) {
        await onRefreshRequest?.();
        await loadPayments();
        setError(`${writeErrorMessage(err)}；已刷新最新金额，当前付款表单已保留，请重新确认`);
      } else {
        setError(writeErrorMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function removePayment(payment: PaymentRecord) {
    if (!canManage || payment.inherited || !window.confirm(`确定删除这笔 ${formatMoney(payment.amount, request.currency)} 的付款记录吗？`)) return;
    if (batch.status === "archived" && !reason.trim()) {
      setError("归档后删除付款必须填写原因");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.deletePayment(
        batch.id,
        request.id,
        payment.id,
        request.version,
        payment.version,
        reason,
      );
      if (editingPaymentId === payment.id) resetPaymentForm();
      await loadPayments();
      await onRequestChanged(result.request);
    } catch (err) {
      if (isApiError(err, "VERSION_CONFLICT")) {
        await onRefreshRequest?.();
        await loadPayments();
        setError(`${writeErrorMessage(err)}；已刷新付款明细，请重新确认`);
      } else {
        setError(writeErrorMessage(err));
      }
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
      setError(writeErrorMessage(err));
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
      setError(writeErrorMessage(err));
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
        <div><span>应付金额</span><strong>{formatMoney(summary.amount || 0, request.currency)}</strong></div>
        <div><span>累计已付</span><strong>{formatMoney(summary.paid_amount || 0, request.currency)}</strong></div>
        <div><span>待付款</span><strong>{formatMoney(summary.pending_amount || 0, request.currency)}</strong></div>
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
                <strong>第 {index + 1} 笔 · {formatMoney(payment.amount, request.currency)}</strong>
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
            本次金额（最大 {formatMoney(maxPayable, request.currency)}）
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
  onOpenBatch,
  reloadBatches,
  setMessage,
}: {
  user: User;
  batches: Batch[];
  selectedBatch: Batch | null;
  setSelectedBatchId: (id: number) => void;
  onOpenBatch: (id: number) => void;
  reloadBatches: () => Promise<void>;
  setMessage: (message: string) => void;
}) {
  const isSmallScreen = useSmallScreen();
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [mobileLogBatch, setMobileLogBatch] = useState<Batch | null>(null);

  async function archive() {
    if (!selectedBatch) return;
    await api.archive(selectedBatch.id, selectedBatch.version);
    await reloadBatches();
    setMessage("批次已归档");
  }

  async function loadLogs(batch: Batch, openMobile = false) {
    const res = await api.audit(batch.id);
    setLogs(res.logs);
    setSelectedBatchId(batch.id);
    if (openMobile) setMobileLogBatch(batch);
  }

  async function deleteDraft(batch: Batch) {
    if (batch.status !== "draft") return;
    if (!window.confirm(`确定删除草稿批次“${batch.name}”吗？删除后该批次下的请款、付款明细和附件凭证也会删除。`)) return;
    await api.deleteBatch(batch.id, batch.version);
    if (selectedBatch?.id === batch.id) setLogs([]);
    await reloadBatches();
    setMessage("草稿批次已删除");
  }

  return (
    <div className={user.role === "business" ? "" : "two-column"}>
      <section className="content-panel">
        {isSmallScreen ? (
          <div className="mobile-archive-list" aria-label="历史批次">
            {batches.map((batch) => (
              <article className="mobile-archive-card" key={batch.id}>
                <header>
                  <div><strong>{batch.name}</strong><span>{formatDateRange(batch.start_date, batch.end_date)}</span></div>
                  <StatusPill value={batch.status === "archived" ? "已归档" : "草稿"} />
                </header>
                <div className="mobile-archive-metrics">
                  <div><span>记录</span><strong>{batch.request_count || 0} 条</strong></div>
                  <div><span>应付</span><strong>{formatMoney(batch.total_amount || 0)}</strong></div>
                  <div><span>已付</span><strong>{formatMoney(batch.total_paid_amount || 0)}</strong></div>
                  <div><span>待付</span><strong>{formatMoney(batch.total_pending_amount || 0)}</strong></div>
                </div>
                <footer>
                  <button className="primary-button" type="button" onClick={() => onOpenBatch(batch.id)}>查看批次</button>
                  <button className="ghost-button" type="button" onClick={() => window.open(`/api/batches/${batch.id}/export.xlsx`, "_blank")}>
                    <Download size={15} />导出
                  </button>
                  {user.role !== "business" && (
                    <button className="ghost-button" type="button" onClick={() => void loadLogs(batch, true)}>
                      <History size={15} />日志
                    </button>
                  )}
                </footer>
              </article>
            ))}
          </div>
        ) : (
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
                    {user.role !== "business" && (
                      <button className="ghost-button" onClick={(event) => {
                        event.stopPropagation();
                        void loadLogs(batch);
                      }}>
                        <History size={15} />
                        日志
                      </button>
                    )}
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
        )}
      </section>
      {user.role !== "business" && !isSmallScreen && (
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
      )}
      {mobileLogBatch && (
        <Modal title={`${mobileLogBatch.name} · 操作日志`} onClose={() => setMobileLogBatch(null)} className="mobile-audit-modal">
          <div className="audit-list mobile-audit-list">
            {logs.length === 0 && <div className="mobile-request-empty">该批次暂无操作日志。</div>}
            {logs.map((log) => (
              <div key={log.id} className="audit-item">
                <strong>{auditActionLabel(log.action)}</strong>
                <span>{log.actor_name || "系统"} · {log.created_at}</span>
                {auditDetail(log) && <p>{auditDetail(log)}</p>}
                {log.reason && <p>{log.reason}</p>}
              </div>
            ))}
          </div>
        </Modal>
      )}
    </div>
  );
}

function auditActionLabel(action: string) {
  const labels: Record<string, string> = {
    "external_expenses.metadata_sync": "同步钉钉流程",
    "external_expenses.sync_timing": "钉钉同步耗时",
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

function SheetPermissionPicker({
  value,
  options,
  onChange,
  disabled = false,
}: {
  value: string[];
  options: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [popoverPosition, setPopoverPosition] = useState({ top: 0, left: 0, width: 320, maxHeight: 320 });
  const allOptions = Array.from(new Set([...options, ...value])).sort((left, right) => left.localeCompare(right, "zh-CN"));

  function updatePopoverPosition() {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const viewportPadding = 12;
    const width = Math.min(420, Math.max(280, window.innerWidth - viewportPadding * 2));
    const left = Math.min(
      Math.max(viewportPadding, rect.left),
      Math.max(viewportPadding, window.innerWidth - width - viewportPadding),
    );
    const spaceBelow = window.innerHeight - rect.bottom - viewportPadding;
    const spaceAbove = rect.top - viewportPadding;
    const openAbove = spaceBelow < 180 && spaceAbove > spaceBelow;
    const maxHeight = Math.max(140, Math.min(320, openAbove ? spaceAbove - 6 : spaceBelow - 6));
    const top = openAbove
      ? Math.max(viewportPadding, rect.top - maxHeight - 4)
      : rect.bottom + 4;
    setPopoverPosition({ top, left, width, maxHeight });
  }

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  useEffect(() => {
    if (!open) return;
    updatePopoverPosition();
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("resize", updatePopoverPosition);
    window.addEventListener("scroll", updatePopoverPosition, true);
    return () => {
      window.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("resize", updatePopoverPosition);
      window.removeEventListener("scroll", updatePopoverPosition, true);
    };
  }, [open]);

  return (
    <div className="sheet-permission-picker">
      <button
        ref={triggerRef}
        className="sheet-permission-trigger"
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        {disabled ? "角色可查看全部 Sheet" : `已授权 ${value.length} 个 Sheet`}
      </button>
      {!disabled && open && createPortal(
        <div
          ref={popoverRef}
          className="sheet-permission-options"
          role="dialog"
          aria-label="选择可访问的 Sheet"
          style={popoverPosition}
        >
          {allOptions.length === 0 && <span className="muted-text">暂无可授权的 Sheet</span>}
          {allOptions.map((sheetName) => (
            <label key={sheetName}>
              <input
                type="checkbox"
                checked={value.includes(sheetName)}
                onChange={(event) => {
                  const next = event.target.checked
                    ? [...value, sheetName]
                    : value.filter((item) => item !== sheetName);
                  onChange(Array.from(new Set(next)));
                }}
              />
              <span title={sheetName}>{sheetName}</span>
            </label>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}

function AdminView({ setMessage }: { setMessage: (message: string) => void }) {
  type UserForm = {
    username: string;
    password: string;
    role: UserRole;
    display_name: string;
    active: boolean;
    sheet_permissions: string[];
  };
  type UserDraft = {
    display_name: string;
    role: UserRole;
    active: boolean;
    password: string;
    sheet_permissions: string[];
  };
  const [users, setUsers] = useState<User[]>([]);
  const [availableSheets, setAvailableSheets] = useState<string[]>([]);
  const emptyUserForm = (): UserForm => ({
    username: "",
    password: "Yuewei123",
    role: "business",
    display_name: "",
    active: true,
    sheet_permissions: [],
  });
  const [userForm, setUserForm] = useState<UserForm>(emptyUserForm);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [creatingUser, setCreatingUser] = useState(false);
  const [createUserError, setCreateUserError] = useState("");
  const [userDrafts, setUserDrafts] = useState<Record<number, UserDraft>>({});
  const [userQuery, setUserQuery] = useState("");
  const visibleUsers = useMemo(() => {
    const query = userQuery.trim().toLowerCase();
    if (!query) return users;
    return users.filter((item) => {
      const roleLabel = roleLabels[item.role] || item.role;
      return [item.username, item.display_name, item.role, roleLabel, ...(item.sheet_permissions || [])]
        .some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [users, userQuery]);

  async function load() {
    const userRes = await api.users();
    setUsers(userRes.users);
    setAvailableSheets(userRes.available_sheets);
    setUserDrafts(Object.fromEntries(userRes.users.map((item) => [item.id, {
      display_name: item.display_name,
      role: item.role,
      active: item.active,
      password: "",
      sheet_permissions: item.sheet_permissions || [],
    }])));
  }

  useEffect(() => {
    load().catch((err) => setMessage((err as Error).message));
  }, []);

  function openCreateUserDialog() {
    setUserForm(emptyUserForm());
    setCreateUserError("");
    setCreateDialogOpen(true);
  }

  function closeCreateUserDialog() {
    if (creatingUser) return;
    setCreateDialogOpen(false);
    setCreateUserError("");
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    const username = userForm.username.trim();
    const displayName = userForm.display_name.trim();
    if (!username) {
      setCreateUserError("请输入账号");
      return;
    }
    if (!displayName) {
      setCreateUserError("请输入姓名");
      return;
    }
    if (userForm.password.length < 6) {
      setCreateUserError("初始密码至少需要 6 位");
      return;
    }
    setCreatingUser(true);
    setCreateUserError("");
    try {
      await api.createUser({
        ...userForm,
        username,
        display_name: displayName,
        sheet_permissions: userForm.role === "business" ? userForm.sheet_permissions : [],
      });
      await load();
      setCreateDialogOpen(false);
      setUserForm(emptyUserForm());
      setMessage("用户已创建");
    } catch (err) {
      setCreateUserError((err as Error).message);
    } finally {
      setCreatingUser(false);
    }
  }

  function updateUserDraft(id: number, patch: Partial<UserDraft>) {
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
      sheet_permissions: draft.sheet_permissions,
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
        <div className="section-title-row admin-users-header">
          <div>
            <div className="section-title">用户</div>
            <small>共 {users.length} 个用户，在列表中维护已有账号</small>
          </div>
          <button className="primary-button" type="button" onClick={openCreateUserDialog}>
            <Plus size={16} />新增用户
          </button>
        </div>
        <div className="admin-user-tools">
          <div className="search-box">
            <Search size={16} />
            <input value={userQuery} onChange={(event) => setUserQuery(event.target.value)} placeholder="搜索账号、姓名、角色" />
          </div>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>Sheet 权限</th><th>状态</th><th>修改密码</th><th>操作</th></tr></thead>
            <tbody>
              {visibleUsers.map((item) => {
                const draft = userDrafts[item.id] || {
                  display_name: item.display_name,
                  role: item.role,
                  active: item.active,
                  password: "",
                  sheet_permissions: item.sheet_permissions || [],
                };
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
                      <SheetPermissionPicker
                        value={draft.sheet_permissions}
                        options={availableSheets}
                        disabled={draft.role !== "business"}
                        onChange={(sheet_permissions) => updateUserDraft(item.id, { sheet_permissions })}
                      />
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
      {createDialogOpen && (
        <Modal title="新增用户" onClose={closeCreateUserDialog} className="create-user-modal">
          <form className="create-user-form" onSubmit={createUser}>
            <p className="form-hint span-2">创建登录账号并设置角色。业务人员只能查看已授权的 Sheet。</p>
            <label>
              账号
              <input
                autoFocus
                autoComplete="off"
                placeholder="请输入登录账号"
                value={userForm.username}
                onChange={(event) => setUserForm({ ...userForm, username: event.target.value })}
                disabled={creatingUser}
              />
            </label>
            <label>
              姓名
              <input
                autoComplete="off"
                placeholder="请输入用户姓名"
                value={userForm.display_name}
                onChange={(event) => setUserForm({ ...userForm, display_name: event.target.value })}
                disabled={creatingUser}
              />
            </label>
            <label>
              初始密码
              <input
                type="password"
                autoComplete="new-password"
                placeholder="至少 6 位"
                value={userForm.password}
                onChange={(event) => setUserForm({ ...userForm, password: event.target.value })}
                disabled={creatingUser}
              />
              <small>默认密码为 Yuewei123，用户登录后可以自行修改。</small>
            </label>
            <label>
              角色
              <select
                value={userForm.role}
                onChange={(event) => {
                  const role = event.target.value as UserRole;
                  setUserForm({
                    ...userForm,
                    role,
                    sheet_permissions: role === "business" ? userForm.sheet_permissions : [],
                  });
                }}
                disabled={creatingUser}
              >
                {Object.entries(roleLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="span-2">
              Sheet 权限
              <SheetPermissionPicker
                value={userForm.sheet_permissions}
                options={availableSheets}
                disabled={userForm.role !== "business"}
                onChange={(sheet_permissions) => setUserForm({ ...userForm, sheet_permissions })}
              />
              <small>
                {userForm.role === "business"
                  ? "未选择时，该用户登录后看不到任何 Sheet。"
                  : "该角色按照系统权限查看全部 Sheet，无需单独授权。"}
              </small>
            </label>
            <label className="inline-check create-user-active span-2">
              <input
                type="checkbox"
                checked={userForm.active}
                onChange={(event) => setUserForm({ ...userForm, active: event.target.checked })}
                disabled={creatingUser}
              />
              创建后立即启用账号
            </label>
            {createUserError && <p className="error-text span-2" role="alert">{createUserError}</p>}
            <div className="create-user-actions span-2">
              <button className="ghost-button" type="button" onClick={closeCreateUserDialog} disabled={creatingUser}>取消</button>
              <button className="primary-button" type="submit" disabled={creatingUser}>
                <Plus size={16} />
                {creatingUser ? "创建中" : "创建用户"}
              </button>
            </div>
          </form>
        </Modal>
      )}
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

function getSheetTabs(
  rows: GridRow[],
  sheetOrder: string[] = [],
  deletedSheetNames: Set<string> = new Set(),
): SheetTab[] {
  const counts = new Map<string, { active: number; deleted: number }>();
  sheetOrder.forEach((sheetName) => {
    const normalized = normalizeSheetName(sheetName);
    if (normalized !== ALL_SHEET && !counts.has(normalized)) {
      counts.set(normalized, { active: 0, deleted: 0 });
    }
  });
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
  const orderIndex = new Map(sheetOrder.map((name, index) => [normalizeSheetName(name), index]));
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
      pendingDelete: deletedSheetNames.has(sheetName),
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
  if (!normalized) return "未分 Sheet";
  const legacyMouldMatch = normalized.match(/^(赣瑞模具|志威模具)\s*(?:[（(]\s*7\s*月\s*(?:前|后)\s*[）)]|7\s*月\s*(?:前|后))$/);
  return legacyMouldMatch?.[1] || normalized;
}

function selectOptionsForField(field: keyof PaymentRequest, currentValue: string) {
  const baseOptions = selectOptionsByField[field];
  if (!baseOptions) return null;
  const options = field === "currency" ? [...baseOptions] : ["", ...baseOptions];
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
  filters: {
    q: string;
    payment_account: string;
    invoice_status: string;
    pending_amount_min: string;
    pending_amount_max: string;
    finance_review: string;
    general_manager_approval: string;
    execution_region: string;
  },
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
  if (filters.execution_region && requestExecutionRegion(row) !== filters.execution_region) return false;
  const pendingAmount = requestAmountCny(row, "pending_amount");
  const pendingAmountMin = optionalNumber(filters.pending_amount_min);
  const pendingAmountMax = optionalNumber(filters.pending_amount_max);
  if (pendingAmountMin !== undefined && pendingAmount < pendingAmountMin) return false;
  if (pendingAmountMax !== undefined && pendingAmount > pendingAmountMax) return false;
  if (filters.finance_review.trim() && String(row.finance_review || "") !== filters.finance_review.trim()) return false;
  if (filters.general_manager_approval === GENERAL_MANAGER_EMPTY_FILTER) {
    if (String(row.general_manager_approval || "").trim()) return false;
    const externalSource = row.raw_extra?.external_source;
    const externalStatus = String(externalSource?.approval_status || "").trim().toUpperCase();
    const externalResult = String(externalSource?.approval_result || "").trim().toLowerCase();
    const fullyPaid = String(row.finance_review || "") === "已付款" || (Number(row.amount || 0) > 0 && pendingAmount <= 0);
    if (fullyPaid || externalStatus === "TERMINATED" || externalResult === "refuse") return false;
  }
  if (
    filters.general_manager_approval.trim()
    && filters.general_manager_approval !== GENERAL_MANAGER_EMPTY_FILTER
    && String(row.general_manager_approval || "") !== filters.general_manager_approval.trim()
  ) return false;
  return true;
}

function requestExecutionRegion(row: Partial<PaymentRequest>): "china" | "mexico" | "unknown" {
  const external = row.raw_extra?.external_source;
  const region = String(external?.execution_region || "").trim().toLowerCase();
  if (region.includes("中国") || region.includes("china")) return "china";
  if (region.includes("墨西哥") || region.includes("mexico") || region.includes("méxico")) return "mexico";
  if (String(row.currency || external?.source_currency || "").trim().toUpperCase() === "MXN") return "mexico";
  return "unknown";
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
    if (key === "id" || key === "version" || key === "updated_at" || calculatedRequestFields.has(key as keyof PaymentRequest)) return;
    output[key as keyof PaymentRequest] = value as never;
  });
  return output;
}

function writeErrorMessage(error: unknown): string {
  if (isApiError(error, "VERSION_CONFLICT")) {
    const target = error.payload.entity_type === "payment_request"
      ? `请款 ${error.payload.entity_id || ""}`
      : error.payload.entity_type === "payment_record"
        ? `付款记录 ${error.payload.entity_id || ""}`
        : error.payload.entity_type === "request_batch"
          ? `批次 ${error.payload.entity_id || ""}`
          : "数据";
    return `${target}已被其他操作修改（当前版本 ${error.payload.current_version ?? "未知"}），请刷新后重新确认`;
  }
  if (isApiError(error, "BATCH_OPERATION_IN_PROGRESS")) {
    const operation = String(error.payload.operation_type || "后台任务");
    return `当前批次正在执行 ${operation}，请等待完成后再操作`;
  }
  if (isApiError(error, "DATABASE_BUSY")) return "数据库正忙，请稍后重试";
  return (error as Error)?.message || "操作失败";
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

function currencyCode(value?: string): CurrencyCode {
  const normalized = String(value || "CNY").trim().toUpperCase();
  return normalized === "USD" || normalized === "MXN" ? normalized : "CNY";
}

function currencyLabel(value?: string) {
  return currentLanguage() === "es"
    ? { CNY: "CNY Yuan chino", USD: "USD Dólar estadounidense", MXN: "MXN Peso mexicano" }[currencyCode(value)]
    : { CNY: "CNY 人民币", USD: "USD 美元", MXN: "MXN 墨西哥比索" }[currencyCode(value)];
}

function formatMoney(value: number, currency?: string) {
  return new Intl.NumberFormat(currentLanguage() === "es" ? "es-MX" : "zh-CN", { style: "currency", currency: currencyCode(currency), maximumFractionDigits: 2 }).format(value || 0);
}

function requestFxRate(request: Partial<PaymentRequest>) {
  if (currencyCode(request.currency) === "CNY") return 1;
  const rate = Number(request.fx_rate_cny_per_unit || 0);
  return rate > 0 ? rate : 1;
}

function requestAmountCny(request: Partial<PaymentRequest>, field: "amount" | "paid_amount" | "pending_amount") {
  if (field === "amount" && request.base_amount_cny !== undefined && request.base_amount_cny !== null) {
    return Number(request.base_amount_cny) || 0;
  }
  return roundMoney((Number(request[field]) || 0) * requestFxRate(request));
}

function currencySubtotals(rows: Array<Partial<PaymentRequest>>) {
  const totals = new Map<string, { currency: string; amount: number; paid_amount: number; pending_amount: number }>();
  rows.forEach((row) => {
    const currency = currencyCode(row.currency);
    const current = totals.get(currency) || { currency, amount: 0, paid_amount: 0, pending_amount: 0 };
    current.amount = roundMoney(current.amount + (Number(row.amount) || 0));
    current.paid_amount = roundMoney(current.paid_amount + (Number(row.paid_amount) || 0));
    current.pending_amount = roundMoney(current.pending_amount + (Number(row.pending_amount) || 0));
    totals.set(currency, current);
  });
  return ["CNY", "USD", "MXN"].map((code) => totals.get(code)).filter(Boolean) as Array<{ currency: string; amount: number; paid_amount: number; pending_amount: number }>;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(currentLanguage() === "es" ? "es-MX" : "zh-CN", { hour12: false });
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
  const es = currentLanguage() === "es";
  if (start && end) return es ? `${start} a ${end}` : `${start} 至 ${end}`;
  if (start) return es ? `Desde ${start}` : `${start} 起`;
  if (end) return es ? `Hasta ${end}` : `截至 ${end}`;
  return es ? "Sin configurar" : "未设置";
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
