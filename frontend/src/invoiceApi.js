export const apiBaseUrl = import.meta.env?.VITE_API_BASE_URL || "http://127.0.0.1:8000";
export const maxUploadBytes = 10 * 1024 * 1024;

export function validatePdfFile(file) {
  if (!file) return "Choose one PDF file to upload.";
  if (!/\.pdf$/i.test(file.name)) return "Choose a file with a .pdf extension.";
  if (!Number.isFinite(file.size) || file.size <= 0) return "The selected PDF is empty.";
  if (file.size > maxUploadBytes) return "The PDF must be 10 MiB or smaller.";
  return "";
}

export function formatFileSize(size) {
  const bytes = `${size.toLocaleString("en-US")} bytes`;
  if (size < 1024) return bytes;
  const unit = size >= 1024 * 1024 ? "MiB" : "KiB";
  const divisor = unit === "MiB" ? 1024 * 1024 : 1024;
  return `${(size / divisor).toFixed(2)} ${unit} (${bytes})`;
}

function safeMessage(value) {
  if (typeof value !== "string" || !value.trim() || value.length > 600) return "";
  if (/traceback|sqlalchemy|sqlite3|\bstack trace\b|[A-Z]:[\\/]|\/(?:home|usr|var|etc|tmp)\/|api[_-]?key|bearer\s/i.test(value)) return "";
  return value.trim();
}

export function getApiError(data, fallback) {
  const detail = data?.detail;
  if (typeof detail === "string") return safeMessage(detail) || fallback;
  if (Array.isArray(detail)) {
    return detail.slice(0, 5).map((item) => safeMessage(item?.msg)).filter(Boolean).join(" ") || fallback;
  }
  if (detail && typeof detail === "object") {
    const messages = [safeMessage(detail.message)];
    if (Array.isArray(detail.errors)) messages.push(...detail.errors.slice(0, 5).map(safeMessage));
    return messages.filter(Boolean).join(" ") || fallback;
  }
  return fallback;
}

export async function readApiJson(response, fallback) {
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(response.ok ? "TaskPilot returned an unexpected response. Please try again." : fallback);
  }
  if (!response.ok) throw new Error(getApiError(data, fallback));
  return data;
}

export async function requestApiJson(url, options = {}, config = {}) {
  const {
    fetchRequest = fetch, timeoutMs = 60000,
    fallback = "TaskPilot could not complete the request.",
    networkMessage = "Cannot reach TaskPilot. Check that the backend is running and try again.",
    timeoutMessage = "The request timed out. Please try again.",
  } = config;
  const controller = new AbortController();
  let timer;
  const request = (async () => {
    let response;
    try {
      response = await fetchRequest(url, { ...options, signal: controller.signal });
    } catch {
      throw new Error(controller.signal.aborted ? timeoutMessage : networkMessage);
    }
    return readApiJson(response, fallback);
  })();
  const deadline = new Promise((resolveDeadline, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error(timeoutMessage));
    }, timeoutMs);
  });
  try {
    // The deadline includes JSON body consumption, not just response headers.
    return await Promise.race([request, deadline]);
  } finally {
    clearTimeout(timer);
  }
}

export function isStoredPdfName(filename) {
  return typeof filename === "string" && filename.length > 4 && /\.pdf$/i.test(filename)
    && !/[\\/:]/.test(filename) && ![...filename].some((character) => character.charCodeAt(0) < 32);
}

export async function uploadInvoicePdf(file, baseUrl = apiBaseUrl, fetchRequest = fetch, timeoutMs = 60000) {
  const validationError = validatePdfFile(file);
  if (validationError) throw new Error(validationError);
  const formData = new FormData();
  formData.append("uploadedFile", file);
  const data = await requestApiJson(`${baseUrl}/invoices/upload`, { method: "POST", body: formData }, {
    fetchRequest, timeoutMs, fallback: "The PDF could not be uploaded. Please try again.",
    timeoutMessage: "Upload timed out. Refresh the invoice list before retrying; the PDF may already have been stored.",
  });
  if (data?.success !== true || !isStoredPdfName(data.filename) || !Number.isFinite(data.size_bytes) || data.size_bytes <= 0 || !Number.isInteger(data.page_count) || data.page_count < 1) {
    throw new Error("TaskPilot returned an unexpected upload response. Refresh the invoice list before trying again.");
  }
  return data;
}

export async function fetchInvoiceList(baseUrl = apiBaseUrl, fetchRequest = fetch, timeoutMs = 60000) {
  const data = await requestApiJson(`${baseUrl}/tools/invoices`, {}, {
    fetchRequest, timeoutMs, fallback: "The available invoices could not be loaded.",
    networkMessage: "Cannot load invoices. Check that the backend is running and retry.",
    timeoutMessage: "Loading invoices timed out. Please retry.",
  });
  if (!Array.isArray(data) || data.some((item) => !isStoredPdfName(item?.filename))) {
    throw new Error("TaskPilot returned an unexpected invoice list. Please retry.");
  }
  return data;
}

export function isTaskResult(data) {
  if (!data || typeof data !== "object" || !["completed", "failed", "needs_clarification"].includes(data.status)) return false;
  const optionalStrings = (object, fields) => fields.every((field) => object[field] == null || typeof object[field] === "string");
  if (!optionalStrings(data, ["message", "summary", "reason", "error", "execution_mode"])) return false;
  if (!Array.isArray(data.steps) || !data.steps.every((step) => step && typeof step.action === "string"
    && (step.result == null || (typeof step.result === "object" && optionalStrings(step.result, ["message", "error", "reason"]))))) return false;
  return !data.invoice || (typeof data.invoice === "object" && !Array.isArray(data.invoice)
    && optionalStrings(data.invoice, ["vendor", "invoice_number", "due_date"])
    && (data.invoice.amount == null || (typeof data.invoice.amount === "number" && Number.isFinite(data.invoice.amount))));
}

export function isHistoryList(data) {
  return Array.isArray(data) && data.every((item) => item && Number.isInteger(item.id)
    && typeof item.task === "string" && typeof item.status === "string"
    && (item.execution_mode == null || typeof item.execution_mode === "string")
    && (!item.result || isTaskResult(item.result)));
}
