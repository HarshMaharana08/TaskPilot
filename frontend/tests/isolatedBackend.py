"""Real FastAPI server for browser tests; all writes use disposable resources."""

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from unittest.mock import Mock, patch

import pymupdf
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import uvicorn


projectRoot = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(projectRoot))


def main():
    sourcePath = projectRoot / "data/taskpilot.db"
    originalHash = hashlib.sha256(sourcePath.read_bytes()).hexdigest() if sourcePath.exists() else None
    with ExitStack() as testStack:
        temporaryRoot = Path(testStack.enter_context(tempfile.TemporaryDirectory(prefix="taskpilot-step5-backend-")))
        databasePath = temporaryRoot / "browser.db"
        from backend.tests.syntheticFixtures import createTestDataset
        createTestDataset(databasePath)
        from backend.app import database

        def rejectRealConnection(*arguments, **keywords):
            raise AssertionError("Browser test attempted a real database connection")

        event.listen(database.engine, "do_connect", rejectRealConnection)
        testStack.callback(event.remove, database.engine, "do_connect", rejectRealConnection)
        engine = create_engine(f"sqlite:///{databasePath}", connect_args={"check_same_thread": False})
        testStack.callback(engine.dispose)
        sessionFactory = sessionmaker(bind=engine)
        testStack.enter_context(patch.object(database, "engine", engine))
        testStack.enter_context(patch.object(database, "SessionLocal", sessionFactory))
        testStack.enter_context(patch.dict("os.environ", {"GEMINI_API_KEY": "offline-browser-test"}))
        testStack.enter_context(patch("google.genai.Client", return_value=Mock()))
        from backend.app import main as application, tools, invoiceUploadService

        # Browser mode tests simulate quota failure without invoking any model.
        testStack.enter_context(patch.object(application.GeminiTaskAgent, "run", side_effect=lambda task: {
            "task": task, "status": "failed", "error": "429 RESOURCE_EXHAUSTED (offline test)",
            "reason": "Gemini is unavailable in this offline test.", "steps": [],
        }))
        invoiceDirectory = temporaryRoot / "invoices"
        invoiceDirectory.mkdir()
        testStack.enter_context(patch.object(tools, "INVOICE_DIR", invoiceDirectory))
        testStack.enter_context(patch.object(invoiceUploadService, "INVOICE_DIR", invoiceDirectory))
        fixturePath = temporaryRoot / "browser-invoice.pdf"
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_textbox(pymupdf.Rect(40, 40, 550, 800), "Supplier: Browser Upload Services\nInvoice No.: BROWSER-STEP5-001\nInvoice Date: 2026-11-05\nDue Date: 2026-12-05\nGrand Total: INR 1,250.50", fontsize=11)
            document.save(fixturePath)
        badFixturePaths = {}
        malformedPath = temporaryRoot / "malformed.pdf"
        malformedPath.write_bytes(b"%PDF-1.7\nbroken")
        badFixturePaths["malformed"] = str(malformedPath)
        with pymupdf.open() as document:
            document.new_page()
            blankPath = temporaryRoot / "textless.pdf"
            document.save(blankPath)
            badFixturePaths["textless"] = str(blankPath)
        with pymupdf.open(fixturePath) as document:
            passwordPath = temporaryRoot / "protected.pdf"
            document.save(passwordPath, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="test-password")
            badFixturePaths["protected"] = str(passwordPath)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        testStack.callback(listener.close)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(application.app, host="127.0.0.1", port=port, log_level="warning"))

        @application.app.get("/__test__/state")
        def inspectState():
            connection = sqlite3.connect(databasePath)
            try:
                invoiceRows = connection.execute("SELECT vendor, invoice_number FROM invoices ORDER BY id").fetchall()
                return {
                    "invoice_count": len(invoiceRows),
                    "history_count": connection.execute("SELECT COUNT(*) FROM task_runs").fetchone()[0],
                    "test_invoice_count": connection.execute("SELECT COUNT(*) FROM invoices WHERE invoice_number='BROWSER-STEP5-001'").fetchone()[0],
                    "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
                }
            finally:
                connection.close()

        @application.app.post("/__test__/shutdown")
        def shutdownServer():
            server.should_exit = True
            return {"success": True}

        print("STEP5_BACKEND " + json.dumps({"baseUrl": f"http://127.0.0.1:{port}", "fixturePath": str(fixturePath), "badFixturePaths": badFixturePaths}), flush=True)
        try:
            server.run(sockets=[listener])
        finally:
            assert (hashlib.sha256(sourcePath.read_bytes()).hexdigest() if sourcePath.exists() else None) == originalHash


if __name__ == "__main__":
    main()
