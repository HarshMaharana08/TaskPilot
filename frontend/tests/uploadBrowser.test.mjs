import assert from "node:assert/strict";
import { after, before, beforeEach, test } from "node:test";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { mkdtemp, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, extname, join, resolve, sep } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { launchBrowser, waitUntil } from "./browserDriver.mjs";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = resolve(frontendRoot, "..");
let temporaryRoot, frontendServer, frontendUrl, browser, backendProcess, backendInfo, protectedHashes;
let scenario, realBackend = false;
const runtimeErrors = [];
const storedName = "56df990d5da04fada39969d955fbc6a0.pdf";
const mockedResult = { status: "completed", message: "Invoice processed and verified.", execution_mode: "local", task_run_id: 7, invoice: { vendor: "Browser Upload Services", invoice_number: "MOCK-001", amount: 1250.5, due_date: "2026-12-05" }, steps: [{ action: "read_invoice", result: { success: true } }, { action: "verify_invoice", result: { verified: true } }] };

async function collectProtectedHashes() {
  const paths = [];
  async function walk(directory, accept) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory() && entry.name !== "__pycache__") await walk(path, accept);
      else if (entry.isFile() && accept(path)) paths.push(path);
    }
  }
  await walk(join(projectRoot, "backend"), (path) => path.endsWith(".py"));
  await walk(join(projectRoot, "data"), () => true);
  for (const entry of await readdir(projectRoot)) if (entry.endsWith(".pdf")) paths.push(join(projectRoot, entry));
  return Object.fromEntries(await Promise.all(paths.map(async (path) => [path, createHash("sha256").update(await readFile(path)).digest("hex")])));
}

function resetScenario() {
  scenario = { invoices: [], requests: [], history: [], uploadStatus: 201, uploadDetail: null, badUpload: false, badList: false, offline: false, listFailure: false, pause: null, taskPause: null };
}

async function startBackend() {
  backendProcess = spawn(join(projectRoot, ".venv/Scripts/python.exe"), ["-B", join(frontendRoot, "tests/isolatedBackend.py")], { cwd: projectRoot, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let errorOutput = "";
  backendProcess.stderr.on("data", (chunk) => { errorOutput += chunk; });
  backendInfo = await new Promise((resolveReady, reject) => {
    const timeout = setTimeout(() => reject(new Error("Isolated backend startup timed out: " + errorOutput)), 45000);
    const lines = createInterface({ input: backendProcess.stdout });
    lines.on("line", (line) => {
      if (line.startsWith("STEP5_BACKEND ")) { clearTimeout(timeout); resolveReady(JSON.parse(line.slice(14))); }
    });
    backendProcess.once("error", (error) => { clearTimeout(timeout); reject(error); });
    backendProcess.once("exit", (code) => { if (!backendInfo) { clearTimeout(timeout); reject(new Error(`Backend exited ${code}: ${errorOutput}`)); } });
  });
  await waitUntil(async () => { try { return (await fetch(backendInfo.baseUrl + "/health")).ok; } catch { return false; } }, "isolated backend health");
}

async function handleRequest(request, response) {
  const requestUrl = new URL(request.url, "http://localhost");
  if (requestUrl.pathname.startsWith("/api/")) {
    const scenarioState = scenario;
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    const path = requestUrl.pathname.slice(4);
    if (realBackend) {
      const headers = { ...request.headers };
      delete headers.host;
      const result = await fetch(backendInfo.baseUrl + path, { method: request.method, headers, body: body.length ? body : undefined });
      response.writeHead(result.status, { "content-type": result.headers.get("content-type") || "application/json" });
      response.end(Buffer.from(await result.arrayBuffer()));
      return;
    }
    scenarioState.requests.push({ path, method: request.method, contentType: request.headers["content-type"], body });
    if (scenarioState.offline) { response.destroy(); return; }
    let data, status = 200;
    if (path === "/invoices/upload") {
      if (scenarioState.pause) await scenarioState.pause;
      status = scenarioState.uploadStatus;
      if (status !== 201) data = { detail: scenarioState.uploadDetail };
      else if (scenarioState.badUpload) data = { unexpected: true };
      else {
        data = { success: true, original_filename: "browser-invoice.pdf", filename: storedName, size_bytes: 1000, page_count: 1, message: "Invoice PDF uploaded successfully." };
        scenarioState.invoices = [{ filename: storedName, path: "private backend path" }];
      }
    } else if (path === "/tools/invoices") {
      if (scenarioState.listFailure) { status = 503; data = { detail: "Invoice listing temporarily unavailable." }; }
      else data = scenarioState.badList ? { unexpected: [] } : scenarioState.invoices;
    } else if (path === "/tasks") {
      if (scenarioState.taskPause) await scenarioState.taskPause;
      data = { ...mockedResult, execution_mode: JSON.parse(body).mode };
      scenarioState.history = [{ id: 7, task: JSON.parse(body).task, status: "completed", execution_mode: data.execution_mode, created_at: "2026-11-12T10:00:00Z", result: data }];
    } else if (path === "/tasks/history") data = scenarioState.history;
    else { status = 404; data = {}; }
    response.writeHead(status, { "content-type": "application/json" });
    response.end(JSON.stringify(data));
    return;
  }
  let path = resolve(temporaryRoot, "dist", "." + decodeURIComponent(requestUrl.pathname === "/" ? "/index.html" : requestUrl.pathname));
  const staticRoot = resolve(temporaryRoot, "dist") + sep;
  if (!path.startsWith(staticRoot)) { response.writeHead(403).end(); return; }
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png" };
  try { const content = await readFile(path); response.writeHead(200, { "content-type": types[extname(path)] || "application/octet-stream" }); response.end(content); }
  catch { response.writeHead(404).end(); }
}

async function waitFor(expression, message = expression) { await waitUntil(() => browser.evaluate(expression), message); }
async function selectFixture(path = backendInfo.fixturePath) { await browser.selectFile(path); await waitFor("document.querySelector('[data-testid=selected-filename]') !== null"); }
async function clickUpload() { await browser.evaluate("document.querySelector('#upload-invoice-button').click()"); }
async function waitForSuccess() { await waitFor("document.querySelector('.upload-success')?.textContent.includes('uploaded successfully') && document.querySelector('#upload-invoice-button').textContent === 'Upload PDF'", "upload confirmation"); }
async function setTask(mode = "local", task = "Process the latest Browser Upload Services invoice") {
  await browser.evaluate(`(() => { const input = document.querySelector('#task'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, ${JSON.stringify(task)}); input.dispatchEvent(new Event('input', {bubbles:true})); const mode = document.querySelector('#mode'); mode.value = ${JSON.stringify(mode)}; mode.dispatchEvent(new Event('change', {bubbles:true})); })()`);
}

before(async () => {
  protectedHashes = await collectProtectedHashes();
  temporaryRoot = await mkdtemp(join(tmpdir(), "taskpilot-step5-browser-"));
  resetScenario();
  await startBackend();
  frontendServer = createServer((request, response) => { handleRequest(request, response).catch((error) => { response.writeHead(500).end(JSON.stringify({ detail: error.message })); }); });
  await new Promise((resolveReady) => frontendServer.listen(0, "127.0.0.1", resolveReady));
  frontendUrl = `http://127.0.0.1:${frontendServer.address().port}`;
  await build({ root: frontendRoot, logLevel: "warn", define: { "import.meta.env.VITE_API_BASE_URL": JSON.stringify(frontendUrl + "/api") }, build: { outDir: join(temporaryRoot, "dist"), emptyOutDir: true } });
  browser = await launchBrowser(join(temporaryRoot, "browser-profile"));
  browser.on("Runtime.exceptionThrown", (details) => runtimeErrors.push(details));
  console.log("Real headless browser:", browser.executable);
}, { timeout: 120000 });

beforeEach(async () => {
  realBackend = false;
  resetScenario();
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('#invoice-pdf') !== null");
  await browser.evaluate("sessionStorage.clear()");
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('#invoice-pdf') && !document.querySelector('.available-invoices [role=status]')");
});

after(async () => {
  if (browser) await browser.close();
  if (frontendServer) await new Promise((done) => { frontendServer.closeAllConnections(); frontendServer.close(done); });
  if (backendInfo) {
    try { await fetch(backendInfo.baseUrl + "/__test__/shutdown", { method: "POST" }); } catch { /* Startup may have failed. */ }
  }
  if (backendProcess?.exitCode === null) {
    try { await waitUntil(() => backendProcess.exitCode !== null, "test backend shutdown", 10000); }
    catch { backendProcess.kill(); }
  }
  if (protectedHashes) assert.deepEqual(await collectProtectedHashes(), protectedHashes, "Real backend/database/PDF/migration/backup files changed");
  assert.equal(runtimeErrors.length, 0, "Uncaught browser runtime errors: " + JSON.stringify(runtimeErrors));
  if (temporaryRoot) {
    assert.equal(dirname(resolve(temporaryRoot)), resolve(tmpdir()));
    assert.ok(basename(temporaryRoot).startsWith("taskpilot-step5-browser-"));
    await rm(temporaryRoot, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 });
  }
}, { timeout: 30000 });

test("browser: selects one PDF and displays its original name and exact size", async () => {
  await selectFixture();
  assert.equal(await browser.evaluate("document.querySelector('[data-testid=selected-filename]').textContent"), "browser-invoice.pdf");
  assert.ok((await browser.evaluate("document.querySelector('[data-testid=selected-filesize]').textContent")).includes((await stat(backendInfo.fixturePath)).size.toLocaleString("en-US")));
  assert.equal(await browser.evaluate("document.querySelector('#invoice-pdf').multiple"), false);
});

for (const [label, filename, size, error] of [["invalid extension", "invalid.txt", 10, "extension"], ["empty PDF", "empty.pdf", 0, "empty"], ["oversized PDF", "oversized.pdf", 10 * 1024 * 1024 + 1, "10 MiB"]]) {
  test(`browser: rejects ${label} without uploading`, async () => {
    const path = join(temporaryRoot, filename);
    await writeFile(path, Buffer.alloc(size));
    await selectFixture(path);
    await waitFor(`document.querySelector('.upload-error')?.textContent.includes(${JSON.stringify(error)})`);
    assert.equal(await browser.evaluate("document.querySelector('#upload-invoice-button').disabled"), true);
    assert.equal(scenario.requests.filter((request) => request.path === "/invoices/upload").length, 0);
  });
}

test("browser: sends real multipart uploadedFile and refreshes the list with the original name", async () => {
  await selectFixture(); await clickUpload();
  await waitFor("document.querySelector('.invoice-file-list')?.textContent.includes('browser-invoice.pdf')");
  const request = scenario.requests.find((request) => request.path === "/invoices/upload");
  assert.match(request.contentType, /^multipart\/form-data; boundary=/);
  assert.ok(request.body.includes(Buffer.from('name="uploadedFile"')));
  assert.ok(!request.body.includes(Buffer.from('name="file"')));
  assert.equal(await browser.evaluate("document.querySelector('.invoice-file-list li').dataset.storedFilename"), storedName);
  assert.equal(scenario.requests.filter((request) => request.path === "/tasks").length, 0);
  await waitFor("document.querySelector('.upload-success') !== null");
});

test("browser: shows uploading state and blocks duplicate form submissions", async () => {
  let release;
  scenario.pause = new Promise((resolvePause) => { release = resolvePause; });
  try {
    await selectFixture(); await clickUpload();
    await waitFor("document.querySelector('#upload-invoice-button').textContent === 'Uploading...'");
    assert.equal(await browser.evaluate("document.querySelector('#invoice-pdf').disabled"), true);
    await browser.evaluate("document.querySelector('.invoice-upload-panel form').requestSubmit(); document.querySelector('.invoice-upload-panel form').requestSubmit()");
    assert.equal(scenario.requests.filter((request) => request.path === "/invoices/upload").length, 1);
  } finally { release(); }
  await waitForSuccess();
});

for (const [label, detail] of [["corrupted PDF", "PDF structure is invalid or damaged."], ["password-protected PDF", "Password-protected PDFs are not supported."], ["textless PDF", "PDF has no readable text. Image-only PDFs require OCR, which is unsupported."]]) {
  test(`browser: displays the backend error for ${label} and permits another selection`, async () => {
    scenario.uploadStatus = 400; scenario.uploadDetail = detail;
    await selectFixture(); await clickUpload();
    await waitFor(`document.querySelector('.upload-error')?.textContent === ${JSON.stringify(detail)}`);
    assert.equal(await browser.evaluate("document.querySelector('#invoice-pdf').disabled"), false);
    const path = join(temporaryRoot, "another.pdf");
    await writeFile(path, await readFile(backendInfo.fixturePath));
    await browser.selectFile(path);
    await waitFor("document.querySelector('[data-testid=selected-filename]').textContent === 'another.pdf'");
  });
}

test("browser: network failure is readable and retryable", async () => {
  scenario.offline = true;
  await selectFixture(); await clickUpload();
  await waitFor("document.querySelector('.upload-error')?.textContent.includes('Cannot reach TaskPilot')");
  scenario.offline = false;
  await clickUpload(); await waitForSuccess();
});

test("browser: unexpected upload response is an error rather than false success", async () => {
  scenario.badUpload = true;
  await selectFixture(); await clickUpload();
  await waitFor("document.querySelector('.upload-error')?.textContent.includes('unexpected upload response')");
  assert.equal(await browser.evaluate("document.querySelector('.upload-success') === null"), true);
});

test("browser: invoice-list failure after success keeps upload confirmation and allows refresh", async () => {
  scenario.listFailure = true;
  await selectFixture(); await clickUpload(); await waitForSuccess();
  await waitFor("document.querySelector('.available-invoices .upload-error') !== null");
  scenario.listFailure = false;
  await browser.evaluate("document.querySelector('.available-invoices button').click()");
  await waitFor("document.querySelector('.invoice-file-list')?.textContent.includes('browser-invoice.pdf')");
});

test("browser: unexpected invoice lists show a recoverable error", async () => {
  scenario.badList = true;
  await browser.evaluate("document.querySelector('.available-invoices button').click()");
  await waitFor("document.querySelector('.available-invoices .upload-error')?.textContent.includes('unexpected invoice list')");
});

test("browser: can upload another PDF after success", async () => {
  await selectFixture(); await clickUpload(); await waitForSuccess();
  assert.equal(await browser.evaluate("document.querySelector('#invoice-pdf').value"), "");
  const path = join(temporaryRoot, "second-invoice.pdf");
  await writeFile(path, await readFile(backendInfo.fixturePath));
  await selectFixture(path); await clickUpload(); await waitForSuccess();
  assert.equal(scenario.requests.filter((request) => request.path === "/invoices/upload").length, 2);
});

test("browser: original upload names survive reload in the current session", async () => {
  await selectFixture(); await clickUpload(); await waitForSuccess();
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('.invoice-file-list')?.textContent.includes('browser-invoice.pdf')");
});

test("browser: upload continues across history navigation and returns its result", async () => {
  let release;
  scenario.pause = new Promise((resolvePause) => { release = resolvePause; });
  try {
    await selectFixture(); await clickUpload();
    await waitFor("document.querySelector('#upload-invoice-button').textContent === 'Uploading...'");
    await browser.evaluate("[...document.querySelectorAll('.nav-item')].find(button => button.textContent.includes('Execution History')).click()");
    await waitFor("document.querySelector('.history-panel') !== null");
  } finally { release(); }
  await waitForSuccess();
  await browser.evaluate("[...document.querySelectorAll('.nav-item')].find(button => button.textContent.includes('Task Runner')).click()");
  await waitFor("document.querySelector('.invoice-file-list')?.textContent.includes('browser-invoice.pdf')");
  assert.equal(await browser.evaluate("document.querySelector('.invoice-upload-panel').parentElement.hidden"), false);
});

test("browser: Local, Auto and Gemini selections still submit and render results, steps and history", async () => {
  await selectFixture(); await clickUpload(); await waitForSuccess();
  for (const mode of ["local", "auto", "gemini"]) {
    await setTask(mode);
    await browser.evaluate("document.querySelector('#task').closest('form').requestSubmit()");
    await waitFor("document.querySelector('.results h2')?.textContent === 'Task completed'");
    await waitFor("!document.querySelector('#task').closest('form').querySelector('button[type=submit]').disabled");
    const request = scenario.requests.filter((request) => request.path === "/tasks").at(-1);
    assert.equal(JSON.parse(request.body).mode, mode);
    assert.ok((await browser.evaluate("document.querySelector('.timeline').textContent")).includes("Verified invoice"));
  }
  await browser.evaluate("[...document.querySelectorAll('.nav-item')].find(button => button.textContent.includes('Execution History')).click()");
  await waitFor("document.querySelector('.history-item')?.textContent.includes('Process the latest Browser Upload Services invoice')");
  await browser.evaluate("document.querySelector('.history-item').click()");
  await waitFor("document.querySelector('.results h2')?.textContent === 'Task completed'");
});

test("browser: desktop and mobile upload/list/history navigation stay usable", async () => {
  const outputDirectory = join(frontendRoot, "test-results");
  await mkdir(outputDirectory, { recursive: true });
  for (const [name, width, height] of [["desktop", 1440, 1100], ["mobile", 390, 844]]) {
    await browser.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 650 });
    assert.equal(await browser.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), true, `${name} horizontal overflow`);
    assert.ok((await browser.evaluate("document.querySelector('#invoice-pdf').getBoundingClientRect().width")) > 100);
    assert.equal(await browser.evaluate("[...document.querySelectorAll('.nav-item')].every(button => getComputedStyle(button).display !== 'none')"), true);
    const screenshot = await browser.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(join(outputDirectory, `step5-${name}.png`), Buffer.from(screenshot.data, "base64"));
  }
  await browser.send("Emulation.clearDeviceMetricsOverride");
});

test("browser end-to-end: real FastAPI upload, listing, Local extraction/create/verify, and task history", async () => {
  realBackend = true;
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('#invoice-pdf') !== null");
  const beforeState = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  await selectFixture(); await clickUpload();
  await waitFor("document.querySelector('.invoice-file-list')?.textContent.includes('browser-invoice.pdf')");
  const afterUpload = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  assert.equal(afterUpload.invoice_count, beforeState.invoice_count, "Upload must not create a database invoice");
  await setTask("local");
  await browser.evaluate("document.querySelector('#task').closest('form').requestSubmit()");
  await waitFor("document.querySelector('.results h2')?.textContent === 'Task completed'");
  const timeline = await browser.evaluate("document.querySelector('.timeline').textContent");
  for (const expected of ["Extracted invoice details", "Checked company records", "Attempted to create invoice", "Retried invoice creation", "Verified invoice in company records"]) assert.ok(timeline.includes(expected), expected);
  assert.ok((await browser.evaluate("document.querySelector('.invoice-grid').textContent")).includes("BROWSER-STEP5-001"));
  const afterTask = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  assert.equal(afterTask.invoice_count, beforeState.invoice_count + 1);
  assert.equal(afterTask.test_invoice_count, 1);
  assert.equal(afterTask.history_count, beforeState.history_count + 1);
  assert.equal(afterTask.integrity, "ok");
  await waitFor("!document.querySelector('#task').closest('form').querySelector('button[type=submit]').disabled");
  await browser.evaluate("[...document.querySelectorAll('.nav-item')].find(button => button.textContent.includes('Execution History')).click()");
  await waitFor("[...document.querySelectorAll('.history-item')].some(item => item.textContent.includes('Browser Upload Services'))");
});

test("Step 6 browser: upload timeout clears loading and advises checking the list", async () => {
  let release;
  scenario.pause = new Promise((resolvePause) => { release = resolvePause; });
  await browser.evaluate("window.savedSetTimeout = window.setTimeout; window.setTimeout = (callback, duration, ...args) => savedSetTimeout(callback, duration === 60000 ? 100 : duration, ...args)");
  try {
    await selectFixture(); await clickUpload();
    await waitFor("document.querySelector('.upload-error')?.textContent.includes('Upload timed out')");
    assert.ok((await browser.evaluate("document.querySelector('.upload-error').textContent")).includes("Refresh the invoice list"));
    assert.equal(await browser.evaluate("document.querySelector('#invoice-pdf').disabled"), false);
    assert.equal(scenario.requests.filter((request) => request.path === "/invoices/upload").length, 1);
  } finally { release(); await browser.evaluate("window.setTimeout = window.savedSetTimeout"); }
});

test("Step 6 browser: task timeout clears loading and advises checking history", async () => {
  let release;
  scenario.taskPause = new Promise((resolvePause) => { release = resolvePause; });
  await browser.evaluate("window.savedSetTimeout = window.setTimeout; window.setTimeout = (callback, duration, ...args) => savedSetTimeout(callback, duration === 180000 ? 100 : duration, ...args)");
  try {
    await setTask();
    await browser.evaluate("document.querySelector('#task').closest('form').requestSubmit()");
    await waitFor("document.querySelector('.error-card')?.textContent.includes('Check execution history')");
    assert.equal(await browser.evaluate("document.querySelector('#task').closest('form').querySelector('button[type=submit]').disabled"), false);
  } finally { release(); await browser.evaluate("window.setTimeout = window.savedSetTimeout"); }
});

test("Step 6 browser: repeated immediate task submits send exactly one request", async () => {
  let release;
  scenario.taskPause = new Promise((resolvePause) => { release = resolvePause; });
  try {
    await setTask();
    await browser.evaluate("const form = document.querySelector('#task').closest('form'); form.requestSubmit(); form.requestSubmit(); form.requestSubmit()");
    await waitUntil(() => scenario.requests.some((request) => request.path === "/tasks"), "task request");
    assert.equal(scenario.requests.filter((request) => request.path === "/tasks").length, 1);
  } finally { release(); }
  await waitFor("document.querySelector('.results h2')?.textContent === 'Task completed'");
});

test("Step 6 browser: malformed history is recoverable using Refresh", async () => {
  scenario.history = { unexpected: true };
  await browser.evaluate("[...document.querySelectorAll('.nav-item')].find(button => button.textContent.includes('Execution History')).click()");
  await waitFor("document.querySelector('.history-error')?.textContent.includes('unexpected task history')");
  scenario.history = [];
  await browser.evaluate("document.querySelector('.history-panel .refresh-button').click()");
  await waitFor("!document.querySelector('.history-error') && document.querySelector('.empty-history')?.textContent.includes('No saved tasks')");
});

test("Step 6 real browser/API: repeated Local tasks identify and verify the existing invoice", async () => {
  realBackend = true;
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('#invoice-pdf') !== null");
  await selectFixture(); await clickUpload(); await waitForSuccess();
  const seeded = await (await fetch(backendInfo.baseUrl + "/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task: "Process the latest Browser Upload Services invoice", mode: "local" }) })).json();
  assert.equal(seeded.status, "completed");
  const beforeState = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  for (let index = 0; index < 2; index++) {
    await setTask();
    await browser.evaluate("document.querySelector('#task').closest('form').requestSubmit()");
    await waitFor("document.querySelector('.results h2')?.textContent === 'Task completed'");
    assert.ok((await browser.evaluate("document.querySelector('.timeline').textContent")).includes("Skipped duplicate creation"));
    await waitFor("!document.querySelector('#task').closest('form').querySelector('button[type=submit]').disabled");
  }
  const afterState = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  assert.equal(afterState.invoice_count, beforeState.invoice_count);
  assert.equal(afterState.test_invoice_count, 1);
  assert.equal(afterState.history_count, beforeState.history_count + 2);
});

test("Step 6 real browser/API: Auto quota fallback and failed/clarification outcomes persist and display", async () => {
  realBackend = true;
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('#task') !== null");
  await selectFixture(); await clickUpload(); await waitForSuccess();
  const beforeState = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  for (const [mode, task, heading] of [["auto", "Process the latest Browser Upload Services invoice", "Task completed"], ["gemini", "Process the latest Browser Upload Services invoice", "Task failed"], ["local", "Process the latest Unlisted Company invoice", "More information needed"]]) {
    await setTask(mode, task);
    await browser.evaluate("document.querySelector('#task').closest('form').requestSubmit()");
    await waitFor(`document.querySelector('.results h2')?.textContent === ${JSON.stringify(heading)}`);
    if (mode === "auto") assert.ok((await browser.evaluate("document.querySelector('.execution-mode').textContent")).includes("local_fallback"));
    await waitFor("!document.querySelector('#task').closest('form').querySelector('button[type=submit]').disabled");
  }
  await browser.evaluate("[...document.querySelectorAll('.nav-item')].find(button => button.textContent.includes('Execution History')).click()");
  await waitFor("document.querySelectorAll('.history-item').length >= 3");
  const statuses = await browser.evaluate("[...document.querySelectorAll('.history-item .status-badge')].map(element => element.textContent)");
  for (const status of ["completed", "failed", "needs_clarification"]) assert.ok(statuses.includes(status), status);
  const afterState = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  assert.equal(afterState.history_count, beforeState.history_count + 3);
});

test("Step 6 real browser/API: malformed, protected and textless PDFs are rejected without database writes", async () => {
  realBackend = true;
  await browser.navigate(frontendUrl);
  await waitFor("document.querySelector('#invoice-pdf') !== null");
  const beforeState = await (await fetch(backendInfo.baseUrl + "/__test__/state")).json();
  for (const path of Object.values(backendInfo.badFixturePaths)) {
    await selectFixture(path); await clickUpload();
    await waitFor("document.querySelector('.upload-error') !== null");
    assert.equal(await browser.evaluate("document.querySelector('.upload-success') === null"), true);
    await waitFor("!document.querySelector('#invoice-pdf').disabled");
  }
  assert.deepEqual(await (await fetch(backendInfo.baseUrl + "/__test__/state")).json(), beforeState);
});
