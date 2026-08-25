import { translateKnownError } from "./i18n";

export type MexicoAccessScope = "all" | "participant" | "none";

export type User = {
  id: number;
  username: string;
  display_name: string;
  role: "business" | "finance" | "general_manager" | "admin";
  active: boolean;
  sheet_permissions: string[];
  mexico_access_scope: MexicoAccessScope;
  mexico_identity_name: string | null;
};

export type MexicoTrackingView = "pending" | "history" | "review";
export type MexicoWarningLevel = "normal" | "yellow" | "red";

export type MexicoTrackingReminder = {
  zh: string;
  es: string;
};

export type MexicoCurrentTask = {
  id?: number;
  task_key: string;
  task_id?: string | null;
  activity_id?: string | null;
  node_name: string;
  approver_id?: string | null;
  approver_name: string;
  entered_at?: string | null;
};

export type MexicoAttachmentStatus = {
  total: number;
  ready: number;
  queued: number;
  downloading: number;
  failed: number;
  complete: boolean;
};

export type MexicoTrackingItem = {
  id: number;
  approval_no: string;
  source_type: string;
  resolved_region: "china" | "mexico" | "review";
  region_resolution_source?: string | null;
  region_review_status: "resolved" | "pending";
  region_conflict_reason?: string | null;
  request_date?: string | null;
  applicant_name?: string | null;
  applicant_department?: string | null;
  company_name?: string | null;
  source_sheet?: string | null;
  summary?: string | null;
  amount?: number | null;
  currency?: string | null;
  workflow_status?: string | null;
  workflow_result?: string | null;
  current_node_name?: string | null;
  current_approver_name?: string | null;
  current_tasks: MexicoCurrentTask[];
  current_approvers: string[];
  current_nodes: string[];
  current_node_entered_at?: string | null;
  workflow_url?: string | null;
  last_state_synced_at?: string | null;
  last_attachment_synced_at?: string | null;
  last_synced_at?: string | null;
  version: number;
  updated_at?: string | null;
  age_days: number;
  warning_level: MexicoWarningLevel;
  reminder?: MexicoTrackingReminder | null;
};

export type MexicoTrackingEvent = {
  id: number;
  event_key: string;
  event_time?: string | null;
  sequence_index?: number | null;
  node_name?: string | null;
  event_type?: string | null;
  result?: string | null;
  operator_name?: string | null;
  remark?: string | null;
  is_current?: number | boolean | null;
  images?: Array<Record<string, unknown>>;
  attachments?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

export type MexicoTrackingAttachment = {
  id: number;
  event_key?: string | null;
  source_file_id?: string | null;
  file_name: string;
  mime_type?: string | null;
  size_bytes?: number | null;
  status: string;
  attempts: number;
  last_error?: string | null;
  content_url?: string | null;
};

export type MexicoTrackingLinkedRequest = {
  id: number;
  batch_id: number;
  batch_name: string;
  dingding_id?: string | null;
  source_sheet?: string | null;
  summary?: string | null;
  payment_status?: string | null;
  amount?: number | null;
  paid_amount?: number | null;
  pending_amount?: number | null;
  currency?: string | null;
  is_primary?: number | boolean;
};

export type MexicoTrackingDetail = MexicoTrackingItem & {
  events: MexicoTrackingEvent[];
  attachments: MexicoTrackingAttachment[];
  linked_requests: MexicoTrackingLinkedRequest[];
  attachment_status: MexicoAttachmentStatus;
};

export type MexicoTrackingSummary = {
  pending: number;
  history: number;
  review: number;
  normal: number;
  yellow: number;
  red: number;
};

export type MexicoApproverStat = {
  approver_name: string;
  pending: number;
  overdue: number;
  severe: number;
};

export type MexicoTrackingFilterOptions = {
  companies: string[];
  sheets: string[];
  source_types: string[];
  applicants: string[];
  approvers: string[];
  nodes: string[];
};

export type MexicoTrackingSettings = {
  yellow_days: number;
  red_days: number;
  cache_stale_seconds: number;
  china_region_isolation_enabled: boolean;
};

export type MexicoSyncRun = {
  id: string;
  kind: string;
  trigger_type: "manual" | "automatic";
  status: "queued" | "running" | "completed" | "failed" | "interrupted";
  phase: string;
  processed_count: number;
  total_count: number;
  attachment_processed_count: number;
  attachment_total_count: number;
  state_committed_at?: string | null;
  error_message?: string | null;
  stage_timings?: Record<string, unknown>;
  result?: Record<string, unknown>;
  fresh?: boolean;
  created_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
};

export type MexicoTrackingListParams = {
  view?: MexicoTrackingView;
  page?: number;
  page_size?: number;
  keyword?: string;
  company?: string;
  source_type?: string;
  applicant?: string;
  approver?: string;
  node?: string;
  warning?: MexicoWarningLevel | "";
  request_date_from?: string;
  request_date_to?: string;
};

export type Batch = {
  id: number;
  version: number;
  parent_batch_id?: number;
  name: string;
  start_date?: string;
  end_date?: string;
  status: "draft" | "archived";
  source_file?: string;
  sheet_order?: string[];
  request_count?: number;
  total_amount?: number;
  total_paid_amount?: number;
  total_pending_amount?: number;
  currency_totals?: CurrencySubtotal[];
  archived_at?: string;
};

export type DailyPayableAmounts = {
  due_today: number;
  paid_today: number;
  end_pending: number;
  overdue_pending: number;
};

export type DailyPayableCurrencyTotal = DailyPayableAmounts & {
  currency: string;
};

export type DailyPayableDetail = {
  logical_request_id: number;
  source_request_id?: number;
  source_batch_id?: number;
  dingding_id?: string;
  source_sheet?: string;
  applicant?: string;
  summary?: string;
  needed_payment_date: string;
  amount: number;
  paid_amount: number;
  paid_today: number;
  pending_amount: number;
  currency: string;
  base_amount_cny: number;
  base_paid_amount_cny: number;
  base_pending_amount_cny: number;
  approval_status?: string;
  approval_result?: string;
  is_due_today: boolean;
  is_overdue: boolean;
};

export type DailyPayableSnapshot = {
  date: string;
  history_start_date: string;
  totals_cny: DailyPayableAmounts;
  currency_totals: DailyPayableCurrencyTotal[];
  counts: {
    due_today: number;
    end_pending: number;
    overdue_pending: number;
  };
  currency?: string | null;
  items?: DailyPayableDetail[];
};

export type DailyPayableTrend = {
  start: string;
  end: string;
  history_start_date: string;
  points: Array<Omit<DailyPayableSnapshot, "history_start_date" | "items" | "currency">>;
};

export type PaymentRequest = {
  id: number;
  version: number;
  batch_id: number;
  copied_from_request_id?: number;
  dingding_id?: string;
  applicant?: string | null;
  payment_account?: string;
  expense_type?: string;
  summary?: string;
  style_name?: string;
  amount?: number;
  paid_amount?: number;
  pending_amount?: number;
  currency?: string;
  base_amount_cny?: number;
  fx_rate_cny_per_unit?: number;
  fx_rate_date?: string;
  fx_rate_actual_date?: string;
  project?: string;
  bu?: string;
  payee_account?: string;
  payee_name?: string;
  bank_name?: string;
  invoice_status?: string;
  needed_payment_date?: string;
  owner_confirmation?: string;
  finance_review?: string;
  finance_manager_approval?: string;
  general_manager_approval?: string;
  general_manager_approval_date?: string;
  general_manager_opinion?: string;
  actual_payment_date?: string;
  remark?: string;
  payment_status?: string;
  overdue_status?: string;
  payer?: string;
  source_sheet?: string;
  source_row?: number;
  payment_count?: number;
  updated_at?: string;
  raw_extra?: {
    external_source?: ExternalSourceSnapshot;
    [key: string]: unknown;
  };
};

export type ExternalSourceSnapshot = {
  system?: string;
  table?: string;
  record_id?: string;
  source_type?: string;
  source_label?: string;
  source_id?: string;
  approval_no?: string;
  approval_status?: "COMPLETED" | "RUNNING" | string;
  approval_result?: string;
  lookup_status?: "matched" | "unmatched" | "conflict";
  metadata_synced_at?: string;
  applicant_id?: string;
  applicant?: string;
  applicant_name_source?: "ding_user_snapshot" | "approval_title" | "unresolved" | string;
  applicant_department?: string;
  applicant_department_level2?: string;
  sheet_assignment_source?: string;
  application_date?: string;
  source_created_at?: string;
  source_updated_at?: string;
  source_currency?: string;
  source_currency_raw?: string;
  currency_source?: string;
  execution_region?: string;
  source_amount?: number;
  base_currency_amount?: number;
};

export type ExternalExpenseSourceType = "operation" | "purchase" | "monthly";
export type ExternalExpenseResultFilter = "matched" | "importable" | "duplicates" | "warnings" | "invalid";

export type ExternalExpensePreviewRow = {
  source_type: ExternalExpenseSourceType;
  source_label: string;
  source_id: string;
  application_date?: string;
  approval_no: string;
  applicant_id: string;
  applicant: string;
  applicant_department?: string;
  original_applicant_department?: string;
  approval_status: "COMPLETED" | "RUNNING" | string;
  approval_result?: string;
  summary: string;
  amount?: number;
  currency?: string;
  base_amount_cny?: number;
  beneficiary: string;
  needed_payment_date?: string;
  related_approval_nos?: string[];
  warnings: string[];
  errors: string[];
  source_conflict: boolean;
  duplicate?: {
    request_id: number;
    batch_id: number;
    batch_name: string;
    source_sheet?: string;
  } | null;
  importable: boolean;
};

export type ExternalExpensePreview = {
  rows: ExternalExpensePreviewRow[];
  all_rows: ExternalExpensePreviewRow[];
  applicant_options: Array<{ id: string; name: string; department?: string; count: number }>;
  summary: {
    matched: number;
    importable: number;
    duplicates: number;
    warnings: number;
    invalid: number;
  };
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
};

export type ExternalExpensePreviewFilter = {
  batch_id: number;
  date_from: string;
  date_to: string;
  source_types: ExternalExpenseSourceType[];
  approval_no: string;
  applicant_ids: string[];
  result_filter: ExternalExpenseResultFilter;
  page: number;
  page_size: number;
};

export type ExternalExpenseImportResult = {
  status: string;
  job_id?: number;
  batch_id: number;
  imported_rows: number;
  duplicate_rows: number;
  invalid_rows: number;
  warnings: number;
  duplicates: unknown[];
  errors: Array<{ source_type: string; source_id: string; messages: string[] }>;
};

export type EmployeeDepartmentImportResult = {
  status: string;
  batch_id: number;
  mapping_rows: number;
  employee_rows: number;
  skipped_employee_no_name: number;
  skipped_employee_no_department: number;
  ambiguous_employee_names: string[];
  departments: string[];
  matched_requests: number;
  moved_requests: number;
  unchanged_requests: number;
  missing_applicant: number;
  unmatched_applicant: number;
  ambiguous_applicant: number;
  removed_empty_sheets: string[];
  sheet_order: string[];
  permissions_unchanged: boolean;
};

export type ExternalMetadataSyncResult = {
  status: string;
  batch_id: number;
  unique_approval_nos: number;
  matched: number;
  unmatched: number;
  conflicts: number;
  updated_requests: number;
  workflow_events: number;
  payment_candidates: number;
  auto_payments: number;
  review_required: number;
  already_applied: number;
  skipped: number;
  auto_payment_mode: "off" | "preview" | "apply" | string;
  attachment_downloaded: number;
  attachment_synced: number;
  attachment_existing: number;
  attachment_failed: number;
  attachment_errors: Array<{
    approval_no?: string;
    attachment_id?: string;
    file_name?: string;
    message: string;
  }>;
  timings?: Record<string, number>;
};

export type BatchOperation = {
  id: string;
  batch_id: number;
  operation_type: string;
  status: "running" | "succeeded" | "failed" | "interrupted" | string;
  stage: string;
  progress_current: number;
  progress_total: number;
  progress_message?: string;
  started_at: string;
  heartbeat_at: string;
  finished_at?: string;
  failure_reason?: string;
  blocks_writes: boolean;
  timings: Record<string, number>;
  partial_result: Partial<ExternalMetadataSyncResult> & { status_committed?: boolean };
  result: Partial<ExternalMetadataSyncResult>;
};

export type ExternalMetadataSyncTaskResult =
  | ExternalMetadataSyncResult
  | { status: "running"; reused: boolean; operation: BatchOperation };

export type DingtalkWorkflowEvent = {
  id: number;
  event_key: string;
  process_instance_id?: string;
  activity_id?: string;
  event_type?: string;
  stage_name?: string;
  result?: string;
  operator_id?: string;
  operator_name?: string;
  event_time?: string;
  comment?: string;
  images: unknown[];
  attachments: unknown[];
  trusted_finance: boolean;
  classification:
    | "ignored"
    | "preview_candidate"
    | "review_required"
    | "applied"
    | "already_applied"
    | "source_missing"
    | string;
  classification_reason?: string;
  payment_record_id?: number;
  payment_amount?: number;
  payment_date?: string;
  active: boolean;
  current: boolean;
  synced_at?: string;
};

export type DingtalkWorkflow = {
  request_id: number;
  approval_no?: string;
  lookup_status?: "matched" | "unmatched" | "conflict" | string;
  approval_status?: string;
  last_synced_at?: string;
  events: DingtalkWorkflowEvent[];
  summary: {
    total: number;
    active: number;
    applied: number;
    review_required: number;
  };
};

export type WeeklyMergeCandidate = {
  id: number;
  dingding_id?: string;
  applicant?: string;
  source_sheet?: string;
  amount?: number;
  summary?: string;
};

export type WeeklyMergeRow = {
  row_id: string;
  action: "create" | "update" | "unchanged" | "conflict" | "skip";
  request_id?: number;
  incoming_request_id?: number;
  dingding_id?: string;
  applicant?: string;
  source_sheet?: string;
  amount?: number;
  old_paid_amount: number;
  new_paid_amount: number;
  changes: Array<{ field: string; label: string; old?: unknown; new?: unknown }>;
  payment_change: boolean;
  attachment_change: boolean;
  payment_changes: Array<{
    key: string;
    action: string;
    payment_id?: number;
    old_amount?: number;
    new_amount?: number;
    delta?: number;
  }>;
  payment_date_keys: string[];
  candidates: WeeklyMergeCandidate[];
  errors: string[];
  warnings: string[];
};

export type WeeklyMergePreview = {
  job_id: number;
  batch_id: number;
  format_version: number;
  summary: {
    create: number;
    update: number;
    payment: number;
    unchanged: number;
    conflict: number;
    warning: number;
  };
  rows: WeeklyMergeRow[];
  sheet_order: { old: string[]; new: string[]; changed: boolean };
  can_apply: boolean;
  expires_at: string;
};

export type WeeklyMergeResolution = {
  row_id: string;
  action: "update" | "create" | "skip";
  request_id?: number;
};

export type WeeklyMergeApplyResult = {
  status: string;
  job_id: number;
  batch_id: number;
  operation_id: string;
  summary: WeeklyMergePreview["summary"];
  sheet_order: string[];
};

export type PaymentVoucher = {
  id: number;
  payment_id: number;
  label?: string;
  original_filename?: string;
  mime_type?: string;
  file_size?: number;
  file_url: string;
  voucher_type: "image" | "pdf";
  created_at: string;
};

export type PaymentRecord = {
  id: number;
  version: number;
  request_id: number;
  copied_from_payment_id?: number;
  root_payment_id?: number;
  amount: number;
  base_amount_cny?: number;
  fx_rate_cny_per_unit?: number;
  fx_rate_date?: string;
  fx_rate_actual_date?: string;
  payment_date?: string;
  payer?: string;
  payment_account?: string;
  bank_reference?: string;
  remark?: string;
  source_type: string;
  creator_name?: string;
  created_at: string;
  updated_at: string;
  inherited: boolean;
  vouchers: PaymentVoucher[];
};

export type CurrencyCode = "CNY" | "USD" | "MXN";
export type CurrencySubtotal = {
  currency: CurrencyCode | string;
  amount: number;
  paid_amount: number;
  pending_amount: number;
  amount_cny: number;
  paid_amount_cny: number;
  pending_amount_cny: number;
};

export type CurrencyConversionPreview = {
  request_id: number;
  request_version?: number;
  mode: "convert" | "correct";
  source_currency: CurrencyCode;
  target_currency: CurrencyCode;
  requested_rate_date: string;
  actual_rate_date: string;
  used_previous_rate: boolean;
  source_rate?: number;
  target_rate: number;
  before_base_amount_cny: number;
  before: { amount: number; paid_amount: number; pending_amount: number };
  after: { amount: number; paid_amount: number; pending_amount: number };
  base_amount_cny: number;
  payment_count: number;
  request_updated_at?: string;
};

export type ForeignAmountCorrectionPreview = {
  request_id: number;
  request_version?: number;
  currency: CurrencyCode;
  requested_rate_date: string;
  actual_rate_date: string;
  used_previous_rate: boolean;
  rate: number;
  before_base_amount_cny: number;
  base_amount_cny: number;
  payment_count: number;
  before: { amount: number; paid_amount: number; pending_amount: number };
  after: { amount: number; paid_amount: number; pending_amount: number };
};

export type RequestGridPreference = {
  version: number;
  order: string[];
  hidden: string[];
};

export type HistoricalCurrencyRestoreRow = {
  request_id: number;
  dingding_id?: string;
  applicant?: string;
  source_sheet?: string;
  current_currency: string;
  source_currency?: CurrencyCode;
  source_currency_raw?: string;
  currency_source?: string;
  execution_region?: string;
  source_amount?: number;
  base_amount_cny?: number;
  payment_count: number;
  status: "recoverable" | "undetermined" | "amount_error" | "already_restored" | string;
  reasons?: string[];
};

export type HistoricalCurrencyRestorePreview = {
  rows: HistoricalCurrencyRestoreRow[];
  summary: Record<string, number>;
};

export type PaymentRecordPayload = {
  amount: number;
  payment_date: string;
  payer?: string;
  payment_account?: string;
  bank_reference?: string;
  remark?: string;
  reason?: string;
  expected_request_version?: number;
  expected_payment_version?: number;
};

export type PaymentSummary = {
  amount?: number;
  paid_amount: number;
  pending_amount?: number;
  finance_review: string;
  payment_count: number;
  actual_payment_date?: string;
  payer?: string;
};

export type Dictionary = {
  id: number;
  kind: string;
  value: string;
  active: number | boolean;
};

export type AuditLog = {
  id: number;
  action: string;
  actor_name?: string;
  reason?: string;
  created_at: string;
  old_value?: unknown;
  new_value?: unknown;
};

export type AttachmentLink = {
  id: number;
  request_id: number;
  label?: string;
  url_path: string;
  attachment_type?: "link" | "image" | "file";
  file_path?: string;
  original_filename?: string;
  mime_type?: string;
  file_size?: number;
  file_url?: string;
  source_system?: string;
  source_attachment_id?: string;
  created_at: string;
};

export type RolloverCopyMode = "unfinished" | "all";
export type UserRole = User["role"];
export type BatchSnapshot = {
  id: number;
  batch_id: number;
  snapshot_type: "baseline" | "pre_restore";
  created_by?: number;
  created_at: string;
  request_count: number;
  attachment_count: number;
  payment_count: number;
  payment_voucher_count: number;
};
export type UserPayload = {
  username: string;
  password: string;
  role: UserRole;
  display_name: string;
  active: boolean;
  sheet_permissions: string[];
  mexico_access_scope: MexicoAccessScope;
  mexico_identity_name: string | null;
};
export type UserUpdatePayload = Partial<Pick<User,
  "role" | "display_name" | "active" | "sheet_permissions" |
  "mexico_access_scope" | "mexico_identity_name"
>> & { password?: string };
export type ChangePasswordPayload = {
  current_password: string;
  new_password: string;
  confirm_password: string;
};
export type RolloverPayload = Pick<Batch, "name"> & {
  start_date?: string;
  end_date?: string;
  copy_mode?: RolloverCopyMode;
  expected_batch_version: number;
};

export type ApiErrorPayload = {
  code?: string;
  message?: string;
  entity_type?: string;
  entity_id?: number;
  current_version?: number;
  operation_type?: string;
  [key: string]: unknown;
};

export class ApiError extends Error {
  status: number;
  code?: string;
  payload: ApiErrorPayload;

  constructor(status: number, payload: ApiErrorPayload, fallback: string) {
    const rawMessage = payload.message || fallback;
    super(translateKnownError(rawMessage));
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code;
    this.payload = payload;
  }
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (!code || error.code === code);
}

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...options,
    credentials: "include",
    headers: options.body instanceof FormData ? options.headers : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = data?.detail;
    const payload: ApiErrorPayload =
      detail && typeof detail === "object"
        ? detail
        : { message: typeof detail === "string" ? detail : "请求失败" };
    throw new ApiError(response.status, payload, "请求失败");
  }
  return data as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<{ user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<{ status: string }>("/api/auth/logout", { method: "POST" }),
  me: () => request<{ user: User }>("/api/me"),
  changePassword: (payload: ChangePasswordPayload) =>
    request<{ status: string; signed_out_sessions: number }>("/api/auth/change-password", { method: "POST", body: JSON.stringify(payload) }),
  dailyPayablesSummary: (selectedDate: string) =>
    request<DailyPayableSnapshot>(`/api/daily-payables/summary?date=${encodeURIComponent(selectedDate)}`),
  dailyPayablesDetails: (selectedDate: string, currency = "") => {
    const query = new URLSearchParams({ date: selectedDate });
    if (currency) query.set("currency", currency);
    return request<DailyPayableSnapshot>(`/api/daily-payables/details?${query}`);
  },
  dailyPayablesTrend: (start: string, end: string) => {
    const query = new URLSearchParams({ start, end });
    return request<DailyPayableTrend>(`/api/daily-payables/trend?${query}`);
  },
  batches: () => request<{ batches: Batch[] }>("/api/batches"),
  createBatch: (payload: Partial<Batch>) => request<{ batch: Batch }>("/api/batches", { method: "POST", body: JSON.stringify(payload) }),
  deleteBatch: (id: number, expectedBatchVersion: number) =>
    request<{ status: string }>(`/api/batches/${id}?expected_batch_version=${expectedBatchVersion}`, { method: "DELETE" }),
  rolloverBatch: (sourceBatchId: number, payload: RolloverPayload) =>
    request<{ batch: Batch; copied_count: number; copy_mode: RolloverCopyMode; operation_id: string }>(`/api/batches/${sourceBatchId}/rollover`, { method: "POST", body: JSON.stringify(payload) }),
  batch: (id: number) => request<{ batch: Batch; stats: Array<Record<string, unknown>> }>(`/api/batches/${id}`),
  updateSheetOrder: (id: number, sheetOrder: string[], expectedBatchVersion: number) =>
    request<{ batch: Batch }>(`/api/batches/${id}/sheet-order`, {
      method: "PUT",
      body: JSON.stringify({ sheet_order: sheetOrder, expected_batch_version: expectedBatchVersion }),
    }),
  archive: (id: number, expectedBatchVersion: number) => request<{ batch: Batch }>(`/api/batches/${id}/archive?expected_batch_version=${expectedBatchVersion}`, { method: "POST" }),
  unarchive: (id: number, expectedBatchVersion: number) => request<{ batch: Batch }>(`/api/batches/${id}/unarchive?expected_batch_version=${expectedBatchVersion}`, { method: "POST" }),
  setBatchBaseline: (id: number, expectedBatchVersion: number) => request<{ snapshot: BatchSnapshot }>(`/api/batches/${id}/snapshots/baseline?expected_batch_version=${expectedBatchVersion}`, { method: "POST" }),
  restoreBatchBaseline: (id: number, expectedBatchVersion: number) =>
    request<{
      status: string;
      snapshot_id: number;
      pre_restore_snapshot_id: number;
      before: { requests: number; attachments: number; payments: number; payment_vouchers: number; amount: number };
      after: { requests: number; attachments: number; payments: number; payment_vouchers: number; amount: number };
    }>(`/api/batches/${id}/restore-baseline?expected_batch_version=${expectedBatchVersion}`, { method: "POST" }),
  requests: (batchId: number, params: Record<string, string>) => {
    const query = new URLSearchParams(params);
    return request<{ requests: PaymentRequest[]; totals: { count: number; amount: number; paid_amount: number; pending_amount: number } }>(`/api/batches/${batchId}/requests?${query}`);
  },
  requestGridPreference: () =>
    request<{ preference: RequestGridPreference }>("/api/me/preferences/request-grid"),
  updateRequestGridPreference: (payload: Pick<RequestGridPreference, "order" | "hidden">) =>
    request<{ preference: RequestGridPreference }>("/api/me/preferences/request-grid", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  createRequest: (batchId: number, payload: Partial<PaymentRequest>) =>
    request<{ request: PaymentRequest }>(`/api/batches/${batchId}/requests`, { method: "POST", body: JSON.stringify(payload) }),
  updateRequest: (batchId: number, requestId: number, payload: Partial<PaymentRequest> & { expected_version: number; reason?: string }) =>
    request<{ request: PaymentRequest }>(`/api/batches/${batchId}/requests/${requestId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  previewCurrencyConversion: (batchId: number, requestId: number, payload: { target_currency: CurrencyCode; rate_date: string; mode?: "convert" | "correct"; reason?: string; expected_version: number; expected_updated_at?: string }) =>
    request<{ preview: CurrencyConversionPreview }>(`/api/batches/${batchId}/requests/${requestId}/currency-conversion/preview`, { method: "POST", body: JSON.stringify(payload) }),
  applyCurrencyConversion: (batchId: number, requestId: number, payload: { target_currency: CurrencyCode; rate_date: string; mode?: "convert" | "correct"; reason?: string; expected_version: number; expected_updated_at?: string }) =>
    request<{ status: string; request: PaymentRequest; preview: CurrencyConversionPreview }>(`/api/batches/${batchId}/requests/${requestId}/currency-conversion/apply`, { method: "POST", body: JSON.stringify(payload) }),
  previewForeignAmountCorrection: (batchId: number, requestId: number, payload: { amount: number; rate_date: string; reason?: string; expected_version: number; expected_updated_at?: string }) =>
    request<{ preview: ForeignAmountCorrectionPreview }>(`/api/batches/${batchId}/requests/${requestId}/amount-correction/preview`, { method: "POST", body: JSON.stringify(payload) }),
  applyForeignAmountCorrection: (batchId: number, requestId: number, payload: { amount: number; rate_date: string; reason?: string; expected_version: number; expected_updated_at?: string }) =>
    request<{ status: string; request: PaymentRequest; preview: ForeignAmountCorrectionPreview }>(`/api/batches/${batchId}/requests/${requestId}/amount-correction/apply`, { method: "POST", body: JSON.stringify(payload) }),
  previewHistoricalCurrencyRestore: (batchId: number) =>
    request<HistoricalCurrencyRestorePreview>(`/api/batches/${batchId}/historical-currency-restore/preview`),
  applyHistoricalCurrencyRestore: (batchId: number, payload: { request_ids: number[]; reason?: string; expected_batch_version: number }) =>
    request<{ status: string; operation_id: string; restored_request_ids: number[]; count: number }>(`/api/batches/${batchId}/historical-currency-restore/apply`, { method: "POST", body: JSON.stringify(payload) }),
  bulkSaveRequests: (
    batchId: number,
    payload: {
      creates: Array<Partial<PaymentRequest>>;
      updates: Array<Partial<PaymentRequest> & { id: number; expected_version: number }>;
      deletes: Array<{ id: number; expected_version: number }>;
      reason?: string;
    },
  ) =>
    request<{ operation_id: string; counts: { created: number; updated: number; deleted: number } }>(`/api/batches/${batchId}/requests/bulk`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteRequest: (batchId: number, requestId: number, expectedVersion: number, reason = "") =>
    request<{ status: string }>(`/api/batches/${batchId}/requests/${requestId}?reason=${encodeURIComponent(reason)}&expected_version=${expectedVersion}`, { method: "DELETE" }),
  payments: (batchId: number, requestId: number) =>
    request<{ payments: PaymentRecord[]; summary: PaymentSummary }>(`/api/batches/${batchId}/requests/${requestId}/payments`),
  createPayment: (batchId: number, requestId: number, payload: PaymentRecordPayload & { expected_request_version: number }) =>
    request<{ payment: PaymentRecord; request: PaymentRequest }>(`/api/batches/${batchId}/requests/${requestId}/payments`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updatePayment: (batchId: number, requestId: number, paymentId: number, payload: Partial<PaymentRecordPayload> & { expected_request_version: number; expected_payment_version: number }) =>
    request<{ payment: PaymentRecord; request: PaymentRequest }>(`/api/batches/${batchId}/requests/${requestId}/payments/${paymentId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deletePayment: (batchId: number, requestId: number, paymentId: number, expectedRequestVersion: number, expectedPaymentVersion: number, reason = "") =>
    request<{ status: string; request: PaymentRequest }>(
      `/api/batches/${batchId}/requests/${requestId}/payments/${paymentId}?reason=${encodeURIComponent(reason)}&expected_request_version=${expectedRequestVersion}&expected_payment_version=${expectedPaymentVersion}`,
      { method: "DELETE" },
    ),
  uploadPaymentVoucher: (batchId: number, requestId: number, paymentId: number, file: File, label?: string, reason?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (label) body.append("label", label);
    if (reason) body.append("reason", reason);
    return request<{ voucher: PaymentVoucher }>(`/api/batches/${batchId}/requests/${requestId}/payments/${paymentId}/vouchers`, { method: "POST", body });
  },
  deletePaymentVoucher: (batchId: number, requestId: number, paymentId: number, voucherId: number, reason = "") =>
    request<{ status: string }>(
      `/api/batches/${batchId}/requests/${requestId}/payments/${paymentId}/vouchers/${voucherId}?reason=${encodeURIComponent(reason)}`,
      { method: "DELETE" },
    ),
  batchAttachments: (batchId: number) => request<{ attachments: AttachmentLink[] }>(`/api/batches/${batchId}/attachments`),
  attachments: (batchId: number, requestId: number) =>
    request<{ attachments: AttachmentLink[] }>(`/api/batches/${batchId}/requests/${requestId}/attachments`),
  createAttachment: (batchId: number, requestId: number, payload: { label?: string; url_path: string }) =>
    request<{ attachment: AttachmentLink }>(`/api/batches/${batchId}/requests/${requestId}/attachments`, { method: "POST", body: JSON.stringify(payload) }),
  uploadImageAttachment: (batchId: number, requestId: number, file: File, label?: string, reason?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (label) body.append("label", label);
    if (reason) body.append("reason", reason);
    return request<{ attachment: AttachmentLink }>(`/api/batches/${batchId}/requests/${requestId}/attachments/image`, { method: "POST", body });
  },
  deleteAttachment: (batchId: number, requestId: number, attachmentId: number, reason = "") =>
    request<{ status: string }>(`/api/batches/${batchId}/requests/${requestId}/attachments/${attachmentId}?reason=${encodeURIComponent(reason)}`, { method: "DELETE" }),
  attachmentFileUrl: (attachmentId: number) => `/api/attachments/${attachmentId}/file`,
  uploadWeekly: (file: File, batchId?: number) => {
    const body = new FormData();
    body.append("file", file);
    if (batchId) body.append("batch_id", String(batchId));
    return request<Record<string, unknown>>("/api/import/weekly-excel", { method: "POST", body });
  },
  previewWeeklyMerge: (file: File, batchId: number) => {
    const body = new FormData();
    body.append("file", file);
    body.append("batch_id", String(batchId));
    return request<WeeklyMergePreview>("/api/import/weekly-excel/merge-preview", { method: "POST", body });
  },
  applyWeeklyMerge: (
    jobId: number,
    payload: {
      resolutions: WeeklyMergeResolution[];
      payment_dates: Record<string, string>;
      reason?: string;
    },
  ) =>
    request<WeeklyMergeApplyResult>(`/api/import-jobs/${jobId}/apply-merge`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  uploadDingTalk: (file: File, batchId?: number, mapping?: Record<string, string>) => {
    const body = new FormData();
    body.append("file", file);
    if (batchId) body.append("batch_id", String(batchId));
    if (mapping) body.append("mapping_json", JSON.stringify(mapping));
    return request<Record<string, unknown>>("/api/import/dingtalk", { method: "POST", body });
  },
  previewExternalExpenses: (payload: ExternalExpensePreviewFilter) =>
    request<ExternalExpensePreview>("/api/external-expenses/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importExternalExpenses: (
    batchId: number,
    items: Array<{ source_type: ExternalExpenseSourceType; source_id: string }>,
  ) =>
    request<ExternalExpenseImportResult>(`/api/batches/${batchId}/imports/external-expenses`, {
      method: "POST",
      body: JSON.stringify({ items }),
    }),
  importEmployeeDepartments: (batchId: number, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<EmployeeDepartmentImportResult>(`/api/batches/${batchId}/employee-departments/import`, {
      method: "POST",
      body,
    });
  },
  syncExternalExpenseMetadata: (batchId: number, onlyIfStaleSeconds = 0, taskMode = true) =>
    request<ExternalMetadataSyncTaskResult>(`/api/batches/${batchId}/external-expenses/sync-metadata?only_if_stale_seconds=${onlyIfStaleSeconds}&task_mode=${taskMode ? "true" : "false"}`, {
      method: "POST",
    }),
  batchOperation: (operationId: string) =>
    request<{ operation: BatchOperation }>(`/api/batch-operations/${operationId}`),
  dingtalkWorkflow: (batchId: number, requestId: number) =>
    request<DingtalkWorkflow>(`/api/batches/${batchId}/requests/${requestId}/dingtalk-workflow`),
  mexicoTrackingList: (params: MexicoTrackingListParams = {}) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
    });
    return request<{ items: MexicoTrackingItem[]; total: number; page: number; page_size: number; pages: number }>(
      `/api/mexico-tracking?${query}`,
    );
  },
  mexicoTrackingDetail: (trackingId: number) =>
    request<{ item: MexicoTrackingDetail }>(`/api/mexico-tracking/${trackingId}`),
  syncMexicoTrackingAttachments: (trackingId: number) =>
    request<{
      run: MexicoSyncRun;
      reused: boolean;
      attachment_status: MexicoAttachmentStatus;
    }>(`/api/mexico-tracking/${trackingId}/attachments/sync`, {
      method: "POST",
    }),
  mexicoTrackingSummary: () =>
    request<{ summary: MexicoTrackingSummary }>("/api/mexico-tracking/summary"),
  mexicoTrackingApproverStats: () =>
    request<{ items: MexicoApproverStat[] }>("/api/mexico-tracking/approver-stats"),
  mexicoTrackingFilterOptions: () =>
    request<{ options: MexicoTrackingFilterOptions }>("/api/mexico-tracking/filter-options"),
  startMexicoTrackingSync: (onlyIfStaleSeconds = 300, triggerType: "manual" | "automatic" = "manual") =>
    request<{ status: string; reused: boolean; run: MexicoSyncRun }>(
      `/api/mexico-tracking/sync?only_if_stale_seconds=${onlyIfStaleSeconds}&trigger_type=${triggerType}`,
      { method: "POST" },
    ),
  mexicoTrackingSyncRun: (runId: string) =>
    request<{ run: MexicoSyncRun }>(`/api/mexico-tracking/sync-runs/${encodeURIComponent(runId)}`),
  mexicoTrackingSettings: () =>
    request<{ settings: MexicoTrackingSettings }>("/api/mexico-tracking/settings"),
  updateMexicoTrackingSettings: (payload: MexicoTrackingSettings) =>
    request<{ settings: MexicoTrackingSettings }>("/api/mexico-tracking/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  resolveMexicoTrackingRegion: (trackingId: number, region: "china" | "mexico", expectedVersion: number) =>
    request<{ item: MexicoTrackingItem }>(`/api/mexico-tracking/${trackingId}/resolve-region`, {
      method: "POST",
      body: JSON.stringify({ region, expected_version: expectedVersion }),
    }),
  rollbackLatestImport: (batchId: number) =>
    request<{
      status: string;
      job_id: number;
      deleted_requests: number;
      deleted_attachments: number;
      deleted_payments: number;
      deleted_payment_vouchers: number;
      removed_files: number;
      restored_requests?: number;
      restored_payments?: number;
    }>(
      `/api/batches/${batchId}/imports/latest/rollback`,
      { method: "POST" },
    ),
  audit: (batchId: number) => request<{ logs: AuditLog[] }>(`/api/batches/${batchId}/audit`),
  dictionaries: () => request<{ dictionaries: Dictionary[] }>("/api/admin/dictionaries"),
  createDictionary: (payload: Partial<Dictionary>) =>
    request<{ dictionary: Dictionary }>("/api/admin/dictionaries", { method: "POST", body: JSON.stringify(payload) }),
  updateDictionary: (id: number, payload: Partial<Dictionary>) =>
    request<{ dictionary: Dictionary }>(`/api/admin/dictionaries/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  users: () => request<{ users: User[]; available_sheets: string[] }>("/api/admin/users"),
  createUser: (payload: UserPayload) =>
    request<{ user: User }>("/api/admin/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: UserUpdatePayload) =>
    request<{ user: User }>(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  resetUserPassword: (id: number) => request<{ user: User; password: string }>(`/api/admin/users/${id}/reset-password`, { method: "POST" }),
  deleteUser: (id: number) => request<{ status: string }>(`/api/admin/users/${id}`, { method: "DELETE" }),
};
