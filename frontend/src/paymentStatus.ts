import type { PaymentRequest } from "./api";

type RequestPayment = Partial<PaymentRequest>;

export function requestPaymentStatus(request: RequestPayment): string {
  if (requestIsPaymentRefused(request)) return "拒绝付款";
  return String(request.finance_review || request.payment_status || "未付款");
}

export function requestIsPaymentRefused(request: RequestPayment): boolean {
  return request.payment_status === "拒绝付款";
}

export function requestIsCompleted(request: RequestPayment): boolean {
  return ["已付款", "拒绝付款"].includes(requestPaymentStatus(request));
}

export function requestOutstandingAmount(request: RequestPayment): number {
  return requestIsPaymentRefused(request) ? 0 : Number(request.pending_amount || 0);
}

export function requestCanAddPayment(request: RequestPayment): boolean {
  return !requestIsPaymentRefused(request) && requestOutstandingAmount(request) > 0;
}

export function matchesPaymentStatusFilter(request: RequestPayment, filter: string): boolean {
  if (!filter.trim()) return true;
  return filter === "已完成" ? requestIsCompleted(request) : requestPaymentStatus(request) === filter.trim();
}

export function paymentStatusCounts(requests: RequestPayment[]) {
  const counts = { paid: 0, partial: 0, unpaid: 0, refused: 0, completed: 0, total: requests.length };
  for (const request of requests) {
    const status = requestPaymentStatus(request);
    if (status === "已付款") counts.paid += 1;
    if (status === "部分付款") counts.partial += 1;
    if (status === "未付款") counts.unpaid += 1;
    if (status === "拒绝付款") counts.refused += 1;
    if (requestIsCompleted(request)) counts.completed += 1;
  }
  return counts;
}
