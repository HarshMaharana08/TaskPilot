"""Public synthetic fixtures; never read or seed the developer's active database."""

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import tempfile

import pymupdf


@dataclass(frozen=True)
class TestDataset:
    databasePath: Path
    invoiceDirectory: Path


datasetDirectory = None
cachedDataset = None


def createTestDataset(databasePath: Path, invoiceDirectory: Path | None = None):
    """Create only a new explicitly supplied file; refuse to overwrite anything."""
    databasePath.parent.mkdir(parents=True, exist_ok=True)
    with databasePath.open("xb"):
        pass
    connection = sqlite3.connect(databasePath)
    try:
        connection.executescript("""
            CREATE TABLE invoices (
                id INTEGER NOT NULL PRIMARY KEY, vendor VARCHAR NOT NULL,
                invoice_number VARCHAR NOT NULL, invoice_date DATE NOT NULL,
                due_date DATE NOT NULL, amount FLOAT NOT NULL, status VARCHAR NOT NULL
            );
            CREATE INDEX ix_invoices_id ON invoices (id);
            CREATE TABLE task_runs (
                id INTEGER NOT NULL PRIMARY KEY, task TEXT NOT NULL,
                status VARCHAR NOT NULL, execution_mode VARCHAR,
                result_json TEXT, created_at DATETIME NOT NULL
            );
            CREATE INDEX ix_task_runs_id ON task_runs (id);
        """)
        connection.execute("CREATE UNIQUE INDEX ux_invoices_vendor_invoice_number_normalized ON invoices (lower(trim(vendor)), invoice_number)")
        invoiceRows = [
            (1, "Acme Corporation", "INV-103", "2026-08-01", "2026-09-01", 65000, "Paid"),
            (2, "Acme Corporation", "INV-104", "2026-09-01", "2026-10-01", 72500, "Pending"),
            (3, "Globex Corporation", "INV-201", "2026-09-15", "2026-10-15", 91000, "Pending"),
            (5, "Globex Corporation", "INV-202", "2026-10-01", "2026-11-01", 98000, "Pending"),
            (6, "Acme Corporation", "INV-105", "2026-10-01", "2026-11-01", 84500, "Pending"),
            (7, "MICROSOFT CORPORATION", "MS-2026-101", "2026-11-01", "2026-12-01", 125000, "Pending"),
            (8, "ORACLE CORPORATION", "OR-2026-501", "2026-11-05", "2026-12-05", 75000, "Pending"),
            (9, "Test Vendor Alpha", "SHARED-FIXTURE", "2026-11-01", "2026-12-01", 1000, "Pending"),
            (10, "Test Vendor Beta", "SHARED-FIXTURE", "2026-11-01", "2026-12-01", 1000, "Pending"),
        ]
        connection.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?)", invoiceRows)
        for runId in range(1, 5):
            task = f"Synthetic baseline task {runId}"
            result = dict(task=task, status="completed", message="Synthetic fixture result.", steps=[], execution_mode="local", task_run_id=runId)
            connection.execute("INSERT INTO task_runs VALUES (?, ?, ?, ?, ?, ?)", (runId, task, "completed", "local", json.dumps(result), "2026-11-12 00:00:00"))
        connection.commit()
    finally:
        connection.close()
    if invoiceDirectory is None:
        return
    invoiceDirectory.mkdir(parents=True, exist_ok=True)
    for row in invoiceRows[:7]:
        invoiceId, vendor, number, invoiceDate, dueDate, amount, status = row
        if vendor == "Acme Corporation":
            filename = "acme_invoice_" + number.removeprefix("INV-") + ".pdf"
        elif vendor == "Globex Corporation":
            filename = "globex_invoice_" + number.removeprefix("INV-") + ".pdf"
        else:
            filename = "microsoft_fixture.pdf" if number.startswith("MS-") else "829d64f86c1b4d87851c55350e84d24f.pdf"
        # U+00B7 deliberately preserves coverage of legacy unreadable glyphs.
        amountText = f"\u00b7{amount:,.0f}" if vendor in {"Acme Corporation", "Globex Corporation"} else f"INR {amount:,.2f}"
        text = f"{vendor.upper()}\nINVOICE\nInvoice Number: {number}\nInvoice Date: {invoiceDate}\nDue Date: {dueDate}\nTotal Amount: {amountText}"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_textbox(pymupdf.Rect(40, 40, 550, 800), text, fontsize=11)
            document.save(invoiceDirectory / filename)


def getTestDataset() -> TestDataset:
    global datasetDirectory, cachedDataset
    if cachedDataset is None:
        datasetDirectory = tempfile.TemporaryDirectory(prefix="taskpilot-synthetic-")
        root = Path(datasetDirectory.name)
        cachedDataset = TestDataset(root / "baseline.db", root / "invoices")
        createTestDataset(cachedDataset.databasePath, cachedDataset.invoiceDirectory)
    return cachedDataset
