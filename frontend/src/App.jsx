import { useState } from "react";
import "./App.css";

const API_BASE_URL = "http://127.0.0.1:8000";

function formatAction(action) {
  const labels = {
    list_invoices: "Scanned available invoices",
    select_latest_vendor_invoice: "Selected the latest invoice",
    read_invoice: "Read invoice PDF",
    extract_invoice_data: "Extracted invoice details",
    search_invoice: "Checked company records",
    create_invoice_attempt_1: "Attempted to create invoice",
    retry_decision: "Decided to retry",
    create_invoice_attempt_2: "Retried invoice creation",
    create_invoice_skipped: "Skipped duplicate creation",
    verify_invoice: "Verified invoice in company records",
  };

  return labels[action] || action.replaceAll("_", " ");
}

function getStepStatus(step) {
  const result = step.result || {};

  if (step.action === "retry_decision") {
    return "retry";
  }

  if (
    result.verified === true ||
    result.success === true ||
    result.found === true
  ) {
    return "success";
  }

  if (
    result.verified === false ||
    result.success === false ||
    result.found === false
  ) {
    return "failed";
  }

  return "info";
}

function getStepSummary(step) {
  const result = step.result || {};

  switch (step.action) {
    case "list_invoices": {
      const count =
        result.count ??
        result.invoices?.length ??
        result.files?.length;

      return count != null
        ? `${count} invoice records found`
        : "Invoice listing retrieved";
    }

    case "select_latest_vendor_invoice":
      return [
        result.vendor || "Vendor",
        result.filename || "Invoice selected",
      ].join(" · ");

    case "read_invoice":
      return result.success
        ? "Invoice PDF read successfully"
        : result.error || "Invoice PDF read";

    case "extract_invoice_data":
      return [
        result.invoice_number,
        result.amount != null
          ? `₹${Number(result.amount).toLocaleString("en-IN")}`
          : null,
        result.due_date ? `Due ${result.due_date}` : null,
      ]
        .filter(Boolean)
        .join(" · ") || "Invoice data extracted";

    case "search_invoice":
      return result.found
        ? "Invoice already exists in company records"
        : "Invoice not found; creation required";

    case "create_invoice_attempt_1":
    case "create_invoice_attempt_2":
      return result.success
        ? "Invoice entry saved successfully"
        : result.error || "Creation attempt failed";

    case "retry_decision":
      return result.reason || "Retrying after a failed attempt";

    case "create_invoice_skipped":
      return result.reason || "Invoice already exists";

    case "verify_invoice":
      return result.verified
        ? "Invoice details match company records"
        : result.error || "Verification result recorded";

    default:
      return result.message || result.error || "Action recorded";
  }
}

function formatDate(value) {
  if (!value) return "Time unavailable";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "Time unavailable";
  }

  return date.toLocaleString();
}

function App() {
  const [task, setTask] = useState("");
  const [mode, setMode] = useState("local");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [history, setHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [activeView, setActiveView] = useState("runner");

  async function loadHistory() {
    setHistoryLoading(true);
    setHistoryError("");

    try {
      const response = await fetch(`${API_BASE_URL}/tasks/history`);

      if (!response.ok) {
        throw new Error("Could not load task history.");
      }

      const data = await response.json();
      setHistory(Array.isArray(data) ? data : []);
    } catch (err) {
      setHistoryError(
        err.message || "An error occurred while loading task history."
      );
    } finally {
      setHistoryLoading(false);
    }
  }

  async function runTask(event) {
    event.preventDefault();

    if (!task.trim() || loading) return;

    setLoading(true);
    setResult(null);
    setError("");
    setActiveView("runner");

    try {
      const response = await fetch(`${API_BASE_URL}/tasks`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          task: task.trim(),
          mode,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || "Unable to execute the task."
        );
      }

      setResult(data);

      // Refresh saved runs after the backend returns a result.
      await loadHistory();
    } catch (err) {
      setError(
        `${err.message || "Failed to fetch"}. Make sure the FastAPI backend is running.`
      );
    } finally {
      setLoading(false);
    }
  }

  function openHistoryItem(item) {
    setTask(item.task || "");
    setResult(
      item.result
        ? {
            ...item.result,
            task_run_id: item.id,
            execution_mode:
              item.execution_mode ||
              item.result.execution_mode,
          }
        : null
    );
    setMode(
      ["local", "auto", "gemini"].includes(item.execution_mode)
        ? item.execution_mode
        : "local"
    );
    setError("");
    setActiveView("runner");
  }

  function useSuggestedTask(vendor) {
    setTask(
      `Find the latest invoice from ${vendor} and enter it into the company system`
    );
    setResult(null);
    setError("");
    setActiveView("runner");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">T</div>
          <div>
            <h2>TaskPilot</h2>
            <p>Autonomous AI Worker</p>
          </div>
        </div>

        <div className="sidebar-label">WORKSPACE</div>

        <button
          type="button"
          className={`nav-item ${
            activeView === "runner" ? "active" : ""
          }`}
          onClick={() => setActiveView("runner")}
        >
          ✳ &nbsp; Task Runner
        </button>

        <button
          type="button"
          className={`nav-item ${
            activeView === "history" ? "active" : ""
          }`}
          onClick={() => {
            setActiveView("history");
            loadHistory();
          }}
        >
          ▤ &nbsp; Execution History
        </button>

        <div className="sidebar-bottom">
          <div className="status-dot" />
          <span>Local workspace</span>
          <span className="online">Online</span>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div>
            <span className="breadcrumb">Workspace / </span>
            <strong>
              {activeView === "runner"
                ? "Task Runner"
                : "Execution History"}
            </strong>
          </div>

          <div className="environment">
            <span className="status-dot" />
            Prototype environment
          </div>
        </header>

        {activeView === "runner" && (
          <>
            <section className="hero">
              <div className="eyebrow">YOUR AI WORKSPACE</div>
              <h1>What should I take care of?</h1>
              <p>
                Describe a task in plain English. TaskPilot will execute
                the workflow, record its actions, and verify the result.
              </p>
            </section>

            <section className="task-card">
              <div className="card-heading">
                <div>
                  <h3>Create a task</h3>
                  <p>
                    Tell your AI worker what needs to get done.
                  </p>
                </div>
                <span className="step-tag">NATURAL LANGUAGE</span>
              </div>

              <form onSubmit={runTask}>
                <label htmlFor="task">TASK INSTRUCTIONS</label>

                <textarea
                  id="task"
                  value={task}
                  onChange={(event) => setTask(event.target.value)}
                  placeholder="e.g. Find the latest invoice from Globex and enter it into the company system..."
                  rows={4}
                  required
                />

                <div className="form-footer">
                  <div className="mode-control">
                    <label htmlFor="mode">EXECUTION MODE</label>
                    <select
                      id="mode"
                      value={mode}
                      onChange={(event) =>
                        setMode(event.target.value)
                      }
                    >
                      <option value="local">
                        Local (no AI API call)
                      </option>
                      <option value="auto">
                        Auto (Gemini + fallback)
                      </option>
                      <option value="gemini">
                        Gemini only
                      </option>
                    </select>
                  </div>

                  <button
                    type="submit"
                    disabled={loading || !task.trim()}
                  >
                    {loading ? "Executing..." : "▶ Run task"}
                  </button>
                </div>
              </form>

              <div className="suggestions">
                <span>TRY A TASK</span>

                <button
                  type="button"
                  className="suggestion"
                  onClick={() => useSuggestedTask("Acme")}
                >
                  Process latest Acme invoice ↗
                </button>

                <button
                  type="button"
                  className="suggestion"
                  onClick={() => useSuggestedTask("Globex")}
                >
                  Process latest Globex invoice ↗
                </button>
              </div>
            </section>

            {loading && (
              <section className="result-card">
                <div className="loading-indicator" />
                <div>
                  <h3>Task in progress</h3>
                  <p>
                    Waiting for TaskPilot to return its execution
                    results...
                  </p>
                </div>
              </section>
            )}

            {error && (
              <section className="result-card error-card">
                <h3>Task could not be completed</h3>
                <p>{error}</p>
              </section>
            )}

            {result && (
              <section className="results">
                <div className="result-card">
                  <div className="result-title">
                    <div>
                      <div className="eyebrow">
                        EXECUTION RESULT
                      </div>
                      <h2>
                        {result.status === "completed"
                          ? "Task completed"
                          : result.status === "needs_clarification"
                            ? "More information needed"
                            : "Task failed"}
                      </h2>
                    </div>

                    <span
                      className={`status-badge ${
                        result.status === "completed"
                          ? "success"
                          : result.status === "needs_clarification"
                            ? "warning"
                            : "failure"
                      }`}
                    >
                      {result.status || "Unknown"}
                    </span>
                  </div>

                  <p className="result-message">
                    {result.message ||
                      result.error ||
                      "No result message was provided."}
                  </p>

                  {result.invoice && (
                    <div className="invoice-grid">
                      <div>
                        <span>VENDOR</span>
                        <strong>
                          {result.invoice.vendor || "—"}
                        </strong>
                      </div>

                      <div>
                        <span>INVOICE NUMBER</span>
                        <strong>
                          {result.invoice.invoice_number || "—"}
                        </strong>
                      </div>

                      <div>
                        <span>AMOUNT</span>
                        <strong>
                          {result.invoice.amount != null
                            ? `₹${Number(
                                result.invoice.amount
                              ).toLocaleString("en-IN")}`
                            : "—"}
                        </strong>
                      </div>

                      <div>
                        <span>DUE DATE</span>
                        <strong>
                          {result.invoice.due_date || "—"}
                        </strong>
                      </div>
                    </div>
                  )}

                  {result.execution_mode && (
                    <p className="execution-mode">
                      Execution mode:{" "}
                      <strong>{result.execution_mode}</strong>
                      {result.task_run_id != null && (
                        <>
                          {" · "}
                          Run ID: <strong>#{result.task_run_id}</strong>
                        </>
                      )}
                    </p>
                  )}
                </div>

                <div className="result-card">
                  <div className="card-heading">
                    <div>
                      <h3>Execution timeline</h3>
                      <p>
                        Actions and tool outputs returned by the
                        backend.
                      </p>
                    </div>

                    <span className="step-tag">
                      {(result.steps || []).length} STEPS
                    </span>
                  </div>

                  {result.steps?.length ? (
                    <div className="timeline">
                      {result.steps.map((step, index) => {
                        const status = getStepStatus(step);

                        return (
                          <div
                            className="timeline-item"
                            key={`${step.action}-${index}`}
                          >
                            <div
                              className={`timeline-marker marker-${status}`}
                            >
                              {status === "success"
                                ? "✓"
                                : status === "failed"
                                  ? "!"
                                  : status === "retry"
                                    ? "↻"
                                    : index + 1}
                            </div>

                            <div className="timeline-content">
                              <div className="step-heading">
                                <strong>
                                  {formatAction(step.action)}
                                </strong>

                                <span
                                  className={`step-status status-${status}`}
                                >
                                  {status === "success"
                                    ? "SUCCESS"
                                    : status === "failed"
                                      ? "FAILED"
                                      : status === "retry"
                                        ? "RETRY"
                                        : "RECORDED"}
                                </span>
                              </div>

                              <p className="step-summary">
                                {getStepSummary(step)}
                              </p>

                              <details className="step-details">
                                <summary>
                                  View technical details
                                </summary>
                                <pre>
                                  {JSON.stringify(
                                    step.result,
                                    null,
                                    2
                                  )}
                                </pre>
                              </details>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p>No execution steps were returned.</p>
                  )}
                </div>
              </section>
            )}
          </>
        )}

        {activeView === "history" && (
          <section className="history-panel">
            <div className="card-heading">
              <div>
                <div className="eyebrow">PAST EXECUTIONS</div>
                <h3>Execution History</h3>
                <p>
                  Previously executed tasks saved in your database.
                </p>
              </div>

              <button
                type="button"
                className="refresh-button"
                onClick={loadHistory}
                disabled={historyLoading}
              >
                {historyLoading ? "Refreshing..." : "↻ Refresh"}
              </button>
            </div>

            {historyError && (
              <p className="history-error">{historyError}</p>
            )}

            {historyLoading && (
              <p className="empty-history">
                Loading saved tasks...
              </p>
            )}

            {!historyLoading &&
              !historyError &&
              history.length === 0 && (
                <p className="empty-history">
                  No saved tasks yet. Run a task first.
                </p>
              )}

            {!historyLoading &&
              history.map((item) => (
                <button
                  type="button"
                  className="history-item"
                  key={item.id}
                  onClick={() => openHistoryItem(item)}
                >
                  <div className="history-item-top">
                    <strong>Run #{item.id}</strong>

                    <span
                      className={`status-badge ${
                        item.status === "completed"
                          ? "success"
                          : item.status === "needs_clarification"
                            ? "warning"
                            : "failure"
                      }`}
                    >
                      {item.status}
                    </span>
                  </div>

                  <p>{item.task}</p>

                  <div className="history-meta">
                    <span>
                      {item.execution_mode || "Unknown mode"}
                    </span>
                    <span>{formatDate(item.created_at)}</span>
                  </div>

                  <span className="history-open">
                    Open result ↗
                  </span>
                </button>
              ))}
          </section>
        )}

        <footer>
          TaskPilot · Autonomous workflows with observable execution
        </footer>
      </main>
    </div>
  );
}

export default App;