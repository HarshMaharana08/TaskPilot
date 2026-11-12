import assert from "node:assert/strict";
import test from "node:test";
import {
  fetchInvoiceList, formatFileSize, getApiError, isHistoryList, isStoredPdfName,
  isTaskResult, maxUploadBytes, readApiJson, uploadInvoicePdf, validatePdfFile,
} from "../src/invoiceApi.js";

const pdfFile = () => new File(["%PDF-fixture"], "invoice.PDF", { type: "application/pdf" });
const uploadResult = { success: true, filename: "56df990d5da04fada39969d955fbc6a0.pdf", original_filename: "invoice.PDF", size_bytes: 12, page_count: 1 };
const jsonResponse = (data, status = 200) => new Response(JSON.stringify(data), { status });

test("PDF extension is case-insensitive", () => assert.equal(validatePdfFile(pdfFile()), ""));
test("a file is required", () => assert.match(validatePdfFile(null), /Choose one PDF/));
test("invalid extension is rejected", () => assert.match(validatePdfFile(new File(["text"], "invoice.txt")), /extension/));
test("empty PDF is rejected", () => assert.match(validatePdfFile(new File([], "empty.pdf")), /empty/));
test("file above 10 MiB is rejected", () => assert.match(validatePdfFile({ name: "large.pdf", size: maxUploadBytes + 1 }), /10 MiB/));
test("exactly 10 MiB is accepted", () => assert.equal(validatePdfFile({ name: "limit.pdf", size: maxUploadBytes }), ""));
test("file size uses exact bytes and readable binary units", () => {
  assert.equal(formatFileSize(1024), "1.00 KiB (1,024 bytes)");
  assert.equal(formatFileSize(1048576), "1.00 MiB (1,048,576 bytes)");
  assert.equal(formatFileSize(12), "12 bytes");
});
test("upload uses uploadedFile FormData without a Content-Type header", async () => {
  const file = pdfFile();
  let calls = 0;
  const result = await uploadInvoicePdf(file, "http://test-api", async (url, options) => {
    calls++;
    assert.equal(url, "http://test-api/invoices/upload");
    assert.equal(options.method, "POST");
    assert.ok(options.body instanceof FormData);
    assert.deepEqual([...options.body.keys()], ["uploadedFile"]);
    assert.equal(options.body.get("uploadedFile").name, file.name);
    assert.equal(options.headers, undefined);
    return jsonResponse(uploadResult, 201);
  });
  assert.equal(calls, 1);
  assert.deepEqual(result, uploadResult);
});
test("invalid files never send a request", async () => {
  await assert.rejects(uploadInvoicePdf(new File([], "empty.pdf"), "http://test-api", () => assert.fail("Unexpected request")), /empty/);
});
test("network failure has an understandable message", async () => {
  await assert.rejects(uploadInvoicePdf(pdfFile(), "http://test-api", async () => { throw new TypeError("Failed to fetch"); }), /Cannot reach TaskPilot/);
});
test("safe backend PDF errors are preserved", async () => {
  await assert.rejects(uploadInvoicePdf(pdfFile(), "http://test-api", async () => jsonResponse({ detail: "Password-protected PDFs are not supported." }, 400)), /Password-protected/);
});
test("structured extraction errors are readable", () => {
  assert.equal(getApiError({ detail: { message: "Invalid invoice.", errors: ["Missing required field: vendor."] } }, "Fallback"), "Invalid invoice. Missing required field: vendor.");
  assert.equal(getApiError({ detail: [{ msg: "Field required", input: "private input" }] }, "Fallback"), "Field required");
});
test("internal traces and paths are hidden", () => {
  assert.equal(getApiError({ detail: "Traceback C:\\Users\\private\\secret.py" }, "Safe fallback"), "Safe fallback");
});
test("unexpected upload responses are rejected", async () => {
  for (const data of [null, {}, { ...uploadResult, success: false }, { ...uploadResult, filename: "../secret.pdf" }, { ...uploadResult, page_count: 0 }]) {
    await assert.rejects(uploadInvoicePdf(pdfFile(), "http://test-api", async () => jsonResponse(data)), /unexpected upload/);
  }
});
test("non-JSON error pages do not leak HTML", async () => {
  await assert.rejects(readApiJson(new Response("<html>Private server error</html>", { status: 500 }), "Safe fallback"), /Safe fallback/);
});
test("non-JSON successful responses are rejected", async () => {
  await assert.rejects(readApiJson(new Response("not JSON"), "Fallback"), /unexpected response/);
});
test("invoice list uses actual array contract", async () => {
  const invoices = [{ filename: "legacy.pdf", path: "private backend path" }];
  assert.deepEqual(await fetchInvoiceList("http://test-api", async (url) => {
    assert.equal(url, "http://test-api/tools/invoices");
    return jsonResponse(invoices);
  }), invoices);
});
test("unexpected invoice lists are rejected", async () => {
  for (const data of [{ invoices: [] }, [null], [{ filename: "../secret.pdf" }]]) {
    await assert.rejects(fetchInvoiceList("http://test-api", async () => jsonResponse(data)), /unexpected invoice list/);
  }
});
test("invoice listing network failures remain recoverable", async () => {
  await assert.rejects(fetchInvoiceList("http://test-api", async () => { throw new Error("Offline"); }), /Check that the backend/);
});
test("stored filenames cannot contain path components", () => {
  assert.equal(isStoredPdfName("56df990d5da04fada39969d955fbc6a0.pdf"), true);
  for (const filename of ["../secret.pdf", "C:\\secret.pdf", "invoice.pdf:stream", null]) assert.equal(isStoredPdfName(filename), false);
});
test("task result and history contracts accept existing responses", () => {
  const result = { status: "completed", steps: [{ action: "list_invoices", result: [] }] };
  assert.equal(isTaskResult(result), true);
  assert.equal(isTaskResult({ status: "completed", steps: {} }), false);
  assert.equal(isHistoryList([{ id: 1, task: "Process an invoice", status: "completed", result }]), true);
  assert.equal(isHistoryList({ history: [] }), false);
});
test("unexpected task display values are rejected before React rendering", () => {
  const result = { status: "completed", steps: [] };
  assert.equal(isTaskResult({ ...result, message: { private: "details" } }), false);
  assert.equal(isTaskResult({ ...result, invoice: { vendor: {} } }), false);
  assert.equal(isTaskResult({ ...result, steps: [{ action: "read_invoice", result: { error: {} } }] }), false);
  assert.equal(isTaskResult(null), false);
});
test("unexpected history display values are rejected", () => {
  assert.equal(isHistoryList([{ id: 1, task: "Process invoice", status: "completed", execution_mode: {} }]), false);
});
