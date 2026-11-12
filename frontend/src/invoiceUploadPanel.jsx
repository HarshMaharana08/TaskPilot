import { useEffect, useRef, useState } from "react";
import { fetchInvoiceList, formatFileSize, uploadInvoicePdf, validatePdfFile } from "./invoiceApi.js";

const uploadNamesKey = "taskpilot.uploadNames";

function readUploadNames() {
  try {
    const names = JSON.parse(sessionStorage.getItem(uploadNamesKey) || "{}");
    return names && typeof names === "object" && !Array.isArray(names) ? names : {};
  } catch {
    return {};
  }
}

export default function InvoiceUploadPanel({ apiBaseUrl }) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [fileError, setFileError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [uploadSuccess, setUploadSuccess] = useState("");
  const [uploadPhase, setUploadPhase] = useState("");
  const [invoices, setInvoices] = useState([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState("");
  const [uploadNames, setUploadNames] = useState(readUploadNames);
  const fileInputRef = useRef(null);
  const uploadLockRef = useRef(false);
  const listRequestRef = useRef(0);
  const uploading = uploadPhase !== "";

  useEffect(() => {
    let cancelled = false;
    const requestId = ++listRequestRef.current;
    fetchInvoiceList(apiBaseUrl).then((data) => {
      if (!cancelled && requestId === listRequestRef.current) setInvoices(data);
    }).catch((error) => {
      if (!cancelled && requestId === listRequestRef.current) setListError(error.message);
    }).finally(() => {
      if (!cancelled && requestId === listRequestRef.current) setListLoading(false);
    });
    return () => { cancelled = true; };
  }, [apiBaseUrl]);

  async function refreshInvoices(expectedFilename) {
    const requestId = ++listRequestRef.current;
    setListLoading(true);
    setListError("");
    try {
      const data = await fetchInvoiceList(apiBaseUrl);
      if (requestId !== listRequestRef.current) return;
      setInvoices(data);
      if (expectedFilename && !data.some((invoice) => invoice.filename === expectedFilename)) {
        setListError("Your PDF was uploaded, but is not in the current list yet. Refresh the list before running a task.");
      }
    } catch (error) {
      if (requestId === listRequestRef.current) setListError(error.message);
    } finally {
      if (requestId === listRequestRef.current) setListLoading(false);
    }
  }

  function selectFile(event) {
    if (uploadLockRef.current) return;
    const file = event.target.files?.[0] || null;
    setSelectedFile(file);
    setFileError(event.target.files?.length > 1 ? "Choose one PDF at a time." : validatePdfFile(file));
    setUploadError("");
    setUploadSuccess("");
  }

  async function submitUpload(event) {
    event.preventDefault();
    if (uploadLockRef.current) return;
    const validationError = fileError || validatePdfFile(selectedFile);
    if (validationError) { setFileError(validationError); return; }
    uploadLockRef.current = true;
    setUploadPhase("uploading");
    setUploadError("");
    setUploadSuccess("");
    try {
      const data = await uploadInvoicePdf(selectedFile, apiBaseUrl);
      const originalName = typeof data.original_filename === "string" && data.original_filename ? data.original_filename : selectedFile.name;
      const names = { ...uploadNames, [data.filename]: originalName };
      setUploadNames(names);
      try { sessionStorage.setItem(uploadNamesKey, JSON.stringify(names)); } catch { /* In-memory names remain available. */ }
      setUploadSuccess(`${originalName} uploaded successfully. You can now run a task for its vendor.`);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setUploadPhase("refreshing");
      await refreshInvoices(data.filename);
    } catch (error) {
      setUploadError(error.message || "The PDF could not be uploaded. Please try again.");
    } finally {
      uploadLockRef.current = false;
      setUploadPhase("");
    }
  }

  return (
    <section className="task-card invoice-upload-panel" aria-labelledby="invoice-upload-heading">
      <div className="card-heading">
        <div>
          <h3 id="invoice-upload-heading">Invoice PDFs</h3>
          <p>Upload an invoice, then ask TaskPilot to process its vendor in the task below.</p>
        </div>
        <span className="step-tag">PDF · UP TO 10 MiB</span>
      </div>
      <form onSubmit={submitUpload} aria-busy={uploading}>
        <label htmlFor="invoice-pdf">SELECT ONE INVOICE PDF</label>
        <input ref={fileInputRef} id="invoice-pdf" type="file" accept=".pdf,application/pdf" onChange={selectFile} disabled={uploading} aria-describedby="invoice-file-help invoice-file-feedback" aria-invalid={Boolean(fileError)} />
        <p id="invoice-file-help" className="upload-help">Text-based PDFs only. Uploading stores the PDF; invoice entry happens when you run a task.</p>
        {selectedFile && <p className="selected-file"><strong data-testid="selected-filename">{selectedFile.name}</strong><span data-testid="selected-filesize">{formatFileSize(selectedFile.size)}</span></p>}
        <div id="invoice-file-feedback">
          {(fileError || uploadError) && <p className="upload-error" role="alert">{fileError || uploadError}</p>}
          {uploadSuccess && <p className="upload-success" role="status">{uploadSuccess}</p>}
        </div>
        <div className="form-footer upload-footer">
          <p className="upload-help">Vendor names are read from PDF contents, not filenames.</p>
          <button id="upload-invoice-button" type="submit" disabled={uploading || !selectedFile || Boolean(fileError)}>{uploadPhase === "refreshing" ? "Refreshing invoices..." : uploading ? "Uploading..." : "Upload PDF"}</button>
        </div>
      </form>
      <div className="available-invoices">
        <div className="invoice-list-heading">
          <h4>Available invoices <span>({invoices.length})</span></h4>
          <button type="button" className="refresh-button" onClick={() => refreshInvoices()} disabled={listLoading || uploading}>Refresh invoices</button>
        </div>
        {listLoading && <p className="upload-help" role="status">Loading invoices...</p>}
        {listError && <p className="upload-error" role="alert">{listError}</p>}
        {!listLoading && !listError && invoices.length === 0 && <p className="upload-help">No invoice PDFs yet. Upload one to get started.</p>}
        <ul className="invoice-file-list" aria-label="Available invoice PDFs">
          {invoices.map((invoice) => <li key={invoice.filename} data-stored-filename={invoice.filename}><span aria-hidden="true" className="pdf-label">PDF</span><strong>{typeof uploadNames[invoice.filename] === "string" ? uploadNames[invoice.filename] : typeof invoice.original_filename === "string" && invoice.original_filename ? invoice.original_filename : invoice.filename}</strong></li>)}
        </ul>
      </div>
    </section>
  );
}
