import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { fetchInvoiceList, uploadInvoicePdf } from "../src/invoiceApi.js";

const file = () => new File(["%PDF-test"], "test.pdf", { type: "application/pdf" });
const response = () => new Response(JSON.stringify({ success: true, filename: "56df990d5da04fada39969d955fbc6a0.pdf", size_bytes: 9, page_count: 1 }));
async function settles(promise) { return Promise.race([promise.then((value) => value, (error) => error), delay(250).then(() => "NO_DEADLINE")]); }

test("Step 6: a hung upload times out, aborts, and never retries automatically", async () => {
  let signal, calls = 0;
  const result = await settles(uploadInvoicePdf(file(), "http://test", async (url, options) => { signal = options.signal; calls++; return new Promise(() => {}); }, 20));
  assert.ok(result instanceof Error, "Upload must settle with a timeout error");
  assert.match(result.message, /timed out.*[Rr]efresh/);
  assert.equal(signal.aborted, true);
  assert.equal(calls, 1);
});

test("Step 6: upload deadline includes reading the response body", async () => {
  const result = await settles(uploadInvoicePdf(file(), "http://test", async () => ({ ok: true, json: () => new Promise(() => {}) }), 20));
  assert.ok(result instanceof Error, "An unfinished response body must time out");
  assert.match(result.message, /timed out/);
});

test("Step 6: invoice listing has a bounded deadline", async () => {
  const result = await settles(fetchInvoiceList("http://test", async () => new Promise(() => {}), 20));
  assert.ok(result instanceof Error, "Listing must settle with a timeout error");
  assert.match(result.message, /timed out/);
});

test("Step 6: an explicitly retried upload works after a timeout", async () => {
  let calls = 0;
  const fetchRequest = async () => { calls++; return calls === 1 ? new Promise(() => {}) : response(); };
  const first = await settles(uploadInvoicePdf(file(), "http://test", fetchRequest, 20));
  assert.ok(first instanceof Error);
  assert.equal((await uploadInvoicePdf(file(), "http://test", fetchRequest, 50)).success, true);
  assert.equal(calls, 2);
});

test("Step 6: a response within the deadline is not aborted", async () => {
  let signal;
  const result = await uploadInvoicePdf(file(), "http://test", async (url, options) => { signal = options.signal; await delay(5); return response(); }, 80);
  assert.equal(result.success, true);
  await delay(90);
  assert.equal(signal.aborted, false);
});

test("Step 6: server timeout messages remain readable", async () => {
  await assert.rejects(uploadInvoicePdf(file(), "http://test", async () => new Response(JSON.stringify({ detail: "The request timed out. Please retry." }), { status: 408 })), /timed out/);
});
