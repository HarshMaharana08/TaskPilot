// Small Chrome DevTools driver using Node's built-in WebSocket; no packages.
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export async function waitUntil(check, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(30);
  }
  throw new Error(`Timed out: ${message}`);
}

export async function launchBrowser(profilePath) {
  const candidates = [process.env.TASKPILOT_BROWSER, "C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "/usr/bin/chromium", "/usr/bin/google-chrome"].filter(Boolean);
  const executable = candidates.find(existsSync);
  if (!executable) throw new Error("A local Chromium browser is required. No browser was downloaded.");
  const child = spawn(executable, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", `--user-data-dir=${profilePath}`, "about:blank"], { windowsHide: true, stdio: "ignore" });
  let startupError;
  child.on("error", (error) => { startupError = error; });
  try {
    let port;
    await waitUntil(async () => {
      if (startupError) throw startupError;
      if (child.exitCode !== null) throw new Error(`Browser exited: ${child.exitCode}`);
      try {
        port = (await readFile(join(profilePath, "DevToolsActivePort"), "utf8")).split(/\r?\n/)[0];
        return /^[0-9]+$/.test(port);
      } catch (error) {
        // Chrome briefly holds this file exclusively while writing it on Windows.
        if (["ENOENT", "EBUSY", "EPERM"].includes(error.code)) return false;
        throw error;
      }
    }, "browser startup", 30000);
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const target = targets.find((item) => item.type === "page");
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
    let nextId = 0;
    const pending = new Map();
    const listeners = new Map();
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (message.id) {
        const request = pending.get(message.id);
        if (!request) return;
        clearTimeout(request.timer);
        pending.delete(message.id);
        if (message.error) request.reject(new Error(message.error.message));
        else request.resolve(message.result);
      } else {
        for (const listener of listeners.get(message.method) || []) listener(message.params);
      }
    });
    socket.addEventListener("close", () => {
      for (const request of pending.values()) { clearTimeout(request.timer); request.reject(new Error("Browser connection closed")); }
      pending.clear();
    });
    const driver = {
      child, executable,
      send(method, params = {}) {
        return new Promise((resolve, reject) => {
          const id = ++nextId;
          const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 20000);
          pending.set(id, { resolve, reject, timer });
          socket.send(JSON.stringify({ id, method, params }));
        });
      },
      on(method, listener) {
        const callbacks = listeners.get(method) || [];
        callbacks.push(listener);
        listeners.set(method, callbacks);
      },
      async evaluate(expression) {
        const result = await driver.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
        if (result.exceptionDetails) throw new Error(result.exceptionDetails.text + ": " + JSON.stringify(result.exceptionDetails.exception));
        return result.result.value;
      },
      async navigate(url) {
        const loaded = new Promise((resolve) => {
          const callback = () => { listeners.set("Page.loadEventFired", (listeners.get("Page.loadEventFired") || []).filter((item) => item !== callback)); resolve(); };
          driver.on("Page.loadEventFired", callback);
        });
        await driver.send("Page.navigate", { url });
        let timer;
        try {
          await Promise.race([loaded, new Promise((resolveReady, reject) => { timer = setTimeout(() => reject(new Error("Page navigation timed out")), 20000); })]);
        } finally { clearTimeout(timer); }
      },
      async selectFile(path) {
        const { root } = await driver.send("DOM.getDocument");
        const { nodeId } = await driver.send("DOM.querySelector", { nodeId: root.nodeId, selector: "#invoice-pdf" });
        await driver.send("DOM.setFileInputFiles", { nodeId, files: [path] });
      },
      async close() {
        try { await driver.send("Browser.close"); } catch { /* Closing the socket is expected. */ }
        socket.close();
        if (child.exitCode === null) child.kill();
      },
    };
    await driver.send("Page.enable");
    await driver.send("Runtime.enable");
    await driver.send("Network.enable");
    // Functional browser tests must not depend on Google's font servers.
    await driver.send("Network.setBlockedURLs", { urls: ["https://fonts.googleapis.com/*", "https://fonts.gstatic.com/*"] });
    return driver;
  } catch (error) {
    if (child.exitCode === null) child.kill();
    throw error;
  }
}
