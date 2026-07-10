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
  request_count?: number;
  total_amount?: number;
  archived_at?: string;
};

export type PaymentRequest = {
  id: number;
  batch_id: number;
  copied_from_request_id?: number;
  dingding_id?: string;
  payment_account?: string;
  expense_type?: string;
  summary?: string;
  style_name?: string;
  amount?: number;
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
export type UserPayload = {
  username: string;
  password: string;
  role: UserRole;
  display_name: string;
  active: boolean;
};
export type UserUpdatePayload = Partial<Pick<User, "role" | "display_name" | "active">> & { password?: string };
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
  batches: () => request<{ batches: Batch[] }>("/api/batches"),
  createBatch: (payload: Partial<Batch>) => request<{ batch: Batch }>("/api/batches", { method: "POST", body: JSON.stringify(payload) }),
  deleteBatch: (id: number) => request<{ status: string }>(`/api/batches/${id}`, { method: "DELETE" }),
  rolloverBatch: (sourceBatchId: number, payload: RolloverPayload) =>
    request<{ batch: Batch; copied_count: number; copy_mode: RolloverCopyMode; operation_id: string }>(`/api/batches/${sourceBatchId}/rollover`, { method: "POST", body: JSON.stringify(payload) }),
  batch: (id: number) => request<{ batch: Batch; stats: Array<Record<string, unknown>> }>(`/api/batches/${id}`),
  archive: (id: number) => request<{ batch: Batch }>(`/api/batches/${id}/archive`, { method: "POST" }),
  unarchive: (id: number) => request<{ batch: Batch }>(`/api/batches/${id}/unarchive`, { method: "POST" }),
  requests: (batchId: number, params: Record<string, string>) => {
    const query = new URLSearchParams(params);
    return request<{ requests: PaymentRequest[]; totals: { count: number; amount: number } }>(`/api/batches/${batchId}/requests?${query}`);
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
  rollbackLatestImport: (batchId: number) =>
    request<{ status: string; job_id: number; deleted_requests: number; deleted_attachments: number; removed_files: number }>(
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
