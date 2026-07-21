export type User = {
  id: number;
  username: string;
  display_name: string;
  role: "business" | "finance" | "general_manager" | "admin";
  active: boolean;
};

export type Batch = {
  id: number;
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
  archived_at?: string;
};

export type PaymentRequest = {
  id: number;
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
  application_date?: string;
  source_created_at?: string;
  source_updated_at?: string;
  source_currency?: string;
  source_amount?: number;
};

export type ExternalExpenseSourceType = "operation" | "purchase";
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
  approval_status: "COMPLETED" | "RUNNING" | string;
  approval_result?: string;
  summary: string;
  amount?: number;
  beneficiary: string;
  needed_payment_date?: string;
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

export type ExternalMetadataSyncResult = {
  status: string;
  batch_id: number;
  unique_approval_nos: number;
  matched: number;
  unmatched: number;
  conflicts: number;
  updated_requests: number;
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
  request_id: number;
  copied_from_payment_id?: number;
  root_payment_id?: number;
  amount: number;
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

export type PaymentRecordPayload = {
  amount: number;
  payment_date: string;
  payer?: string;
  payment_account?: string;
  bank_reference?: string;
  remark?: string;
  reason?: string;
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
  attachment_type?: "link" | "image";
  file_path?: string;
  original_filename?: string;
  mime_type?: string;
  file_size?: number;
  file_url?: string;
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
};
export type UserUpdatePayload = Partial<Pick<User, "role" | "display_name" | "active">> & { password?: string };
export type ChangePasswordPayload = {
  current_password: string;
  new_password: string;
  confirm_password: string;
};
export type RolloverPayload = Pick<Batch, "name"> & {
  start_date?: string;
  end_date?: string;
  copy_mode?: RolloverCopyMode;
};

async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...options,
    credentials: "include",
    headers: options.body instanceof FormData ? options.headers : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || "请求失败");
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
  batches: () => request<{ batches: Batch[] }>("/api/batches"),
  createBatch: (payload: Partial<Batch>) => request<{ batch: Batch }>("/api/batches", { method: "POST", body: JSON.stringify(payload) }),
  deleteBatch: (id: number) => request<{ status: string }>(`/api/batches/${id}`, { method: "DELETE" }),
  rolloverBatch: (sourceBatchId: number, payload: RolloverPayload) =>
    request<{ batch: Batch; copied_count: number; copy_mode: RolloverCopyMode; operation_id: string }>(`/api/batches/${sourceBatchId}/rollover`, { method: "POST", body: JSON.stringify(payload) }),
  batch: (id: number) => request<{ batch: Batch; stats: Array<Record<string, unknown>> }>(`/api/batches/${id}`),
  updateSheetOrder: (id: number, sheetOrder: string[]) =>
    request<{ batch: Batch }>(`/api/batches/${id}/sheet-order`, {
      method: "PUT",
      body: JSON.stringify({ sheet_order: sheetOrder }),
    }),
  archive: (id: number) => request<{ batch: Batch }>(`/api/batches/${id}/archive`, { method: "POST" }),
  unarchive: (id: number) => request<{ batch: Batch }>(`/api/batches/${id}/unarchive`, { method: "POST" }),
  setBatchBaseline: (id: number) => request<{ snapshot: BatchSnapshot }>(`/api/batches/${id}/snapshots/baseline`, { method: "POST" }),
  restoreBatchBaseline: (id: number) =>
    request<{
      status: string;
      snapshot_id: number;
      pre_restore_snapshot_id: number;
      before: { requests: number; attachments: number; payments: number; payment_vouchers: number; amount: number };
      after: { requests: number; attachments: number; payments: number; payment_vouchers: number; amount: number };
    }>(`/api/batches/${id}/restore-baseline`, { method: "POST" }),
  requests: (batchId: number, params: Record<string, string>) => {
    const query = new URLSearchParams(params);
    return request<{ requests: PaymentRequest[]; totals: { count: number; amount: number; paid_amount: number; pending_amount: number } }>(`/api/batches/${batchId}/requests?${query}`);
  },
  createRequest: (batchId: number, payload: Partial<PaymentRequest>) =>
    request<{ request: PaymentRequest }>(`/api/batches/${batchId}/requests`, { method: "POST", body: JSON.stringify(payload) }),
  updateRequest: (batchId: number, requestId: number, payload: Partial<PaymentRequest> & { reason?: string }) =>
    request<{ request: PaymentRequest }>(`/api/batches/${batchId}/requests/${requestId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  bulkSaveRequests: (
    batchId: number,
    payload: {
      creates: Array<Partial<PaymentRequest>>;
      updates: Array<Partial<PaymentRequest> & { id: number }>;
      deletes: number[];
      reason?: string;
    },
  ) =>
    request<{ operation_id: string; counts: { created: number; updated: number; deleted: number } }>(`/api/batches/${batchId}/requests/bulk`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteRequest: (batchId: number, requestId: number, reason = "") =>
    request<{ status: string }>(`/api/batches/${batchId}/requests/${requestId}?reason=${encodeURIComponent(reason)}`, { method: "DELETE" }),
  payments: (batchId: number, requestId: number) =>
    request<{ payments: PaymentRecord[]; summary: PaymentSummary }>(`/api/batches/${batchId}/requests/${requestId}/payments`),
  createPayment: (batchId: number, requestId: number, payload: PaymentRecordPayload) =>
    request<{ payment: PaymentRecord; request: PaymentRequest }>(`/api/batches/${batchId}/requests/${requestId}/payments`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updatePayment: (batchId: number, requestId: number, paymentId: number, payload: Partial<PaymentRecordPayload>) =>
    request<{ payment: PaymentRecord; request: PaymentRequest }>(`/api/batches/${batchId}/requests/${requestId}/payments/${paymentId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deletePayment: (batchId: number, requestId: number, paymentId: number, reason = "") =>
    request<{ status: string; request: PaymentRequest }>(
      `/api/batches/${batchId}/requests/${requestId}/payments/${paymentId}?reason=${encodeURIComponent(reason)}`,
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
  syncExternalExpenseMetadata: (batchId: number) =>
    request<ExternalMetadataSyncResult>(`/api/batches/${batchId}/external-expenses/sync-metadata`, {
      method: "POST",
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
  users: () => request<{ users: User[] }>("/api/admin/users"),
  createUser: (payload: UserPayload) =>
    request<{ user: User }>("/api/admin/users", { method: "POST", body: JSON.stringify(payload) }),
  updateUser: (id: number, payload: UserUpdatePayload) =>
    request<{ user: User }>(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  resetUserPassword: (id: number) => request<{ user: User; password: string }>(`/api/admin/users/${id}/reset-password`, { method: "POST" }),
  deleteUser: (id: number) => request<{ status: string }>(`/api/admin/users/${id}`, { method: "DELETE" }),
};
