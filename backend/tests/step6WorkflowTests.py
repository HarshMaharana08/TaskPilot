"""Step 6 additions; intentionally separate from the unchanged 89-test runner.

Run: python -B -m unittest discover -s backend/tests -p step6WorkflowTests.py -v
Every application connection and PDF write targets disposable resources.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import importlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.tests import testInvoiceExtraction as extractionTests
from backend.tests import test_invoice_uniqueness_compatibility as compatibilityTests


class BackendWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compatibilityTests.InvoiceCompatibilityTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        compatibilityTests.InvoiceCompatibilityTests.tearDownClass.__func__(cls)

    def setUp(self):
        compatibilityTests.InvoiceCompatibilityTests.setUp(self)
        self.invoiceDirectory = Path(self.temporaryDirectory) / self.id().split(".")[-1]
        self.invoiceDirectory.mkdir()
        self.uploadModule = importlib.import_module("backend.app.invoiceUploadService")
        self.testStack.enter_context(patch.object(self.toolsModule, "INVOICE_DIR", self.invoiceDirectory))
        self.testStack.enter_context(patch.object(self.uploadModule, "INVOICE_DIR", self.invoiceDirectory))
        self.client = self.testStack.enter_context(TestClient(self.mainModule.app, raise_server_exceptions=False))

    def pdfBytes(self, vendor="StepSix Services", number="SIX-001", invoiceDate="2026-11-05", dueDate="2026-12-05"):
        return extractionTests.makePdf([extractionTests.invoiceText(vendor="Supplier: " + vendor, number="Invoice ID: " + number, invoiceDate="Invoice Date: " + invoiceDate, dueDate="Due Date: " + dueDate)])

    def uploadPdf(self, content=None, filename="invoice.pdf"):
        return self.client.post("/invoices/upload", files={"uploadedFile": (filename, content or self.pdfBytes(), "application/pdf")})

    def runVendorTask(self, vendor="StepSix Services", mode="local"):
        return self.client.post("/tasks", json={"task": f"Process the latest {vendor} invoice", "mode": mode})

    def counts(self):
        with closing(sqlite3.connect(self.databasePath)) as connection:
            return connection.execute("SELECT (SELECT COUNT(*) FROM invoices), (SELECT COUNT(*) FROM task_runs)").fetchone()

    def testUnknownVendorUploadCreateRetryVerifyRepeatAndHistory(self):
        self.assertEqual(self.uploadPdf().status_code, 201)
        self.assertEqual(self.counts(), (9, 4))
        self.toolsModule.CREATE_FAILURE_SIMULATION = True
        first = self.runVendorTask()
        self.assertEqual(first.status_code, 200)
        result = first.json()
        self.assertEqual(result["status"], "completed", result)
        steps = {step["action"]: step["result"] for step in result["steps"]}
        self.assertFalse(steps["search_invoice"]["found"])
        self.assertFalse(steps["create_invoice_attempt_1"]["success"])
        self.assertTrue(steps["create_invoice_attempt_2"]["success"])
        self.assertTrue(steps["verify_invoice"]["verified"])
        repeated = self.runVendorTask().json()
        self.assertEqual(repeated["status"], "completed", repeated)
        self.assertTrue(any(step["action"] == "create_invoice_skipped" for step in repeated["steps"]))
        self.assertEqual(self.counts(), (10, 6))
        history = self.client.get("/tasks/history").json()
        saved = {row["id"]: row for row in history}
        for taskResult in (result, repeated):
            row = saved[taskResult["task_run_id"]]
            self.assertEqual(row["status"], "completed")
            self.assertEqual(row["result"], taskResult)

    def testLatestUsesDateDespiteReverseFilenamesAndUploadOrder(self):
        for number, dateValue, storedId in (("NEWEST-SIX", "2026-11-05", "0" * 32), ("MIDDLE-SIX", "2026-10-05", "7" * 32), ("OLDEST-SIX", "2026-09-05", "f" * 32)):
            with patch.object(self.uploadModule, "uuid4", return_value=SimpleNamespace(hex=storedId)):
                response = self.uploadPdf(self.pdfBytes(number=number, invoiceDate=dateValue), filename=number + ".pdf")
            self.assertEqual(response.status_code, 201)
        result = self.runVendorTask().json()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["invoice"]["invoice_number"], "NEWEST-SIX")
        selected = next(step["result"] for step in result["steps"] if step["action"] == "select_latest_vendor_invoice")
        self.assertEqual(selected["filename"], "0" * 32 + ".pdf")
        self.assertEqual(self.counts()[0], 10)

    def testDifferentVendorsShareInvoiceNumberAcrossFullWorkflows(self):
        invoiceIds = []
        for vendor in ("Aster Services", "Beacon Services"):
            self.assertEqual(self.uploadPdf(self.pdfBytes(vendor=vendor, number="SHARED-SIX")).status_code, 201)
            result = self.runVendorTask(vendor).json()
            self.assertEqual(result["status"], "completed", result)
            search = self.client.get("/tools/search/SHARED-SIX", params={"vendor": "  " + vendor.upper() + "  "}).json()
            self.assertTrue(search["found"])
            self.assertEqual(search["invoice"]["vendor"], vendor)
            invoiceIds.append(search["invoice"]["id"])
        self.assertEqual(len(set(invoiceIds)), 2)
        self.assertEqual(self.counts()[0], 11)
        self.assertTrue(self.client.get("/tools/search/SHARED-SIX").json()["ambiguous"])

    def testConcurrentUploadsAreUniqueAndDoNotCreateRecords(self):
        payload = self.pdfBytes()
        with ThreadPoolExecutor(max_workers=4) as executor:
            responses = list(executor.map(lambda index: self.uploadPdf(payload, f"concurrent-{index}.pdf"), range(4)))
        self.assertTrue(all(response.status_code == 201 for response in responses))
        filenames = [response.json()["filename"] for response in responses]
        self.assertEqual(len(set(filenames)), 4)
        self.assertTrue(all(UUID(Path(filename).stem).version == 4 for filename in filenames))
        self.assertEqual(len(self.client.get("/tools/invoices").json()), 4)
        self.assertTrue(all(self.toolsModule.read_invoice(filename)["extraction"]["valid"] for filename in filenames))
        self.assertEqual(self.counts(), (9, 4))

    def testConcurrentTasksCreateOneInvoiceAndIndependentHistoryRows(self):
        self.assertEqual(self.uploadPdf().status_code, 201)
        with ThreadPoolExecutor(max_workers=3) as executor:
            responses = list(executor.map(lambda index: self.runVendorTask(), range(3)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        results = [response.json() for response in responses]
        self.assertTrue(all(result["status"] == "completed" for result in results), results)
        self.assertEqual(len({result["task_run_id"] for result in results}), 3)
        self.assertEqual(self.counts(), (10, 7))
        with closing(sqlite3.connect(self.databasePath)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def testInvalidUploadsAndStoredInvalidInvoicesNeverCreateRecords(self):
        for changes in ({"vendor": None}, {"number": None}, {"invoiceDate": "Invoice Date: 2026-02-30"}, {"dueDate": None}, {"amount": "Grand Total: -1"}, {"number": "Invoice Number: FIRST\nInvoice Number: SECOND"}, {"amount": "Grand Total: INR 1\nTotal Amount: INR 2"}):
            with self.subTest(changes=changes):
                content = extractionTests.makePdf([extractionTests.invoiceText(vendor="Vendor: StepSix Services", **changes)]) if "vendor" not in changes else extractionTests.makePdf([extractionTests.invoiceText(**changes)])
                self.assertEqual(self.uploadPdf(content).status_code, 422)
                (self.invoiceDirectory / "invalid.pdf").write_bytes(content)
                with patch.object(self.agentModule, "create_invoice", side_effect=AssertionError("Invalid data must not reach creation")):
                    result = self.runVendorTask().json()
                self.assertIn(result["status"], {"failed", "needs_clarification"}, result)
                self.assertEqual(self.counts()[0], 9)
                (self.invoiceDirectory / "invalid.pdf").unlink()

    def testTaskFailureAndClarificationAreStoredWithEvidence(self):
        self.assertEqual(self.uploadPdf().status_code, 201)
        with patch.object(self.mainModule.TaskAgent, "run", return_value={"status": "failed", "reason": "Temporary creation failure.", "steps": []}):
            failed = self.runVendorTask().json()
        clarification = self.runVendorTask("Unlisted Vendor").json()
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(clarification["status"], "needs_clarification")
        saved = {row["id"]: row for row in self.client.get("/tasks/history").json()}
        for result in (failed, clarification):
            self.assertEqual(saved[result["task_run_id"]]["result"], result)
        self.assertEqual(self.counts(), (9, 6))

    def testDatabaseLockRollsBackCreationAndRecoversAfterRelease(self):
        shortEngine = create_engine(f"sqlite:///{self.databasePath}", connect_args={"timeout": 0.02})
        self.addCleanup(shortEngine.dispose)
        lock = sqlite3.connect(self.databasePath)
        try:
            lock.execute("BEGIN IMMEDIATE")
            with patch.object(self.toolsModule, "SessionLocal", sessionmaker(bind=shortEngine)):
                result = self.toolsModule.create_invoice("Lock Services", "LOCK-SIX", "2026-11-05", "2026-12-05", 100)
            self.assertFalse(result["success"], result)
            self.assertIn("locked", result["error"])
        finally:
            lock.rollback()
            lock.close()
        self.assertTrue(self.toolsModule.create_invoice("Lock Services", "LOCK-SIX", "2026-11-05", "2026-12-05", 100)["success"])
        self.assertEqual(self.counts()[0], 10)

    def testLockedTaskDatabaseReturnsRetryableJsonAndNoPartialHistory(self):
        shortEngine = create_engine(f"sqlite:///{self.databasePath}", connect_args={"check_same_thread": False, "timeout": 0.02})
        self.addCleanup(shortEngine.dispose)
        lock = sqlite3.connect(self.databasePath)
        try:
            lock.execute("BEGIN IMMEDIATE")
            with patch.object(self.mainModule, "SessionLocal", sessionmaker(bind=shortEngine)):
                response = self.runVendorTask()
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn("retry", response.json()["detail"].lower())
        finally:
            lock.rollback()
            lock.close()
        self.assertEqual(self.counts(), (9, 4))
        self.assertEqual(self.uploadPdf().status_code, 201)
        self.assertEqual(self.runVendorTask().json()["status"], "completed")

    def testUnavailableDatabaseReturnsFailureWithoutDuplicateRecovery(self):
        badEngine = create_engine(f"sqlite:///{self.invoiceDirectory / 'absent' / 'database.db'}")
        self.addCleanup(badEngine.dispose)
        with patch.object(self.toolsModule, "SessionLocal", sessionmaker(bind=badEngine)):
            result = self.toolsModule.create_invoice("Acme Corporation", "INV-105", "2026-10-01", "2026-11-01", 84500)
        self.assertFalse(result["success"], result)
        self.assertIn("unable to open", result["error"])
        self.assertEqual(self.counts(), (9, 4))

    def testMissingAndMalformedApiInputsReturnValidationErrors(self):
        for payload in ({}, {"task": 123}, {"task": ""}, {"task": "   "}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/tasks", json=payload).status_code, 422)
        self.assertEqual(self.client.post("/tasks", json={"task": "Test", "mode": "unsupported"}).status_code, 400)
        self.assertEqual(self.client.post("/invoices/upload", files={"file": ("test.pdf", self.pdfBytes(), "application/pdf")}).status_code, 422)
        self.assertEqual(self.client.get("/tools/verify/INV-105").status_code, 422)
        self.assertEqual(self.counts(), (9, 4))

    def testConfiguredFrontendCorsPreflightAndUploadRemainCompatible(self):
        # Browser suites use an isolated same-origin proxy; verify the actual
        # production/dev cross-origin contract separately through FastAPI.
        for origin in ("http://127.0.0.1:5173", "http://localhost:5173"):
            response = self.client.options("/invoices/upload", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
        response = self.client.post("/invoices/upload", files={"uploadedFile": ("cors.pdf", self.pdfBytes(), "application/pdf")}, headers={"Origin": "http://127.0.0.1:5173"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")
        rejected = self.client.options("/invoices/upload", headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn("access-control-allow-origin", rejected.headers)
        self.assertEqual(self.counts(), (9, 4))

    def testDirectInvoiceApiRejectsIncompleteInvalidAndNonFiniteData(self):
        validData = dict(vendor="API Validation Services", invoice_number="API-SIX", invoice_date="2026-11-05", due_date="2026-12-05", amount=100)
        badInputs = [{}, {"vendor": "Only a vendor"}]
        badInputs += [dict(validData, **changes) for changes in ({"vendor": "  "}, {"vendor": 123}, {"invoice_number": ""}, {"invoice_date": "2026-02-30"}, {"due_date": "2020-01-01"}, {"amount": -1}, {"amount": 0}, {"amount": True}, {"amount": "oops"}, {"amount": 10 ** 400})]
        for payload in badInputs:
            with self.subTest(payload=payload):
                response = self.client.post("/tools/invoices", json=payload)
                self.assertEqual(response.status_code, 422, response.text)
        for value in ("NaN", "Infinity"):
            payload = json.dumps(validData).replace('"amount": 100', '"amount": ' + value)
            self.assertEqual(self.client.post("/tools/invoices", content=payload, headers={"Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.counts(), (9, 4))


class OfflineProtocolTests(unittest.TestCase):
    setUp = extractionTests.OfflineGeminiTests.setUp
    toolResponse = extractionTests.OfflineGeminiTests.toolResponse

    def apiClient(self):
        database = importlib.import_module("backend.app.database")
        with patch.object(database, "engine", self.engine), patch.object(database, "SessionLocal", self.toolsModule.SessionLocal):
            application = importlib.import_module("backend.app.main")
        self.testStack.enter_context(patch.object(application, "SessionLocal", self.toolsModule.SessionLocal))
        uploadModule = importlib.import_module("backend.app.invoiceUploadService")
        self.testStack.enter_context(patch.object(uploadModule, "INVOICE_DIR", self.directory))
        return self.testStack.enter_context(TestClient(application.app))

    def testGeminiModeCompletesThroughActualApiAndPersistsHistoryOffline(self):
        client = self.apiClient()
        response = client.post("/invoices/upload", files={"uploadedFile": ("offline.pdf", extractionTests.makePdf([extractionTests.invoiceText()]), "application/pdf")})
        self.assertEqual(response.status_code, 201)
        self.fakeClient.models.generate_content.side_effect = [
            self.toolResponse("read_invoice", {"filename": response.json()["filename"]}),
            self.toolResponse("create_invoice", self.arguments), self.toolResponse("create_invoice", self.arguments),
            self.toolResponse("verify_invoice", dict(invoice_number="NOVA-26/001_A", expected_vendor="Nova Services", expected_invoice_date="2026-11-05", expected_due_date="2026-12-05", expected_amount=75000)),
        ]
        result = client.post("/tasks", json={"task": "Process the latest Nova invoice", "mode": "gemini"}).json()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["execution_mode"], "gemini")
        row = next(row for row in client.get("/tasks/history").json() if row["id"] == result["task_run_id"])
        self.assertEqual(row["result"], result)

    def testAutoQuotaFallbackUsesLocalAndPersistsResult(self):
        self.assertAutoFallback("429 RESOURCE_EXHAUSTED", expectedDelay=40)

    def testAutoUnavailableFallbackUsesLocalAndPersistsResult(self):
        self.assertAutoFallback("UNAVAILABLE", expectedDelay=2)

    def assertAutoFallback(self, errorMessage, expectedDelay):
        client = self.apiClient()
        self.assertEqual(client.post("/invoices/upload", files={"uploadedFile": ("offline.pdf", extractionTests.makePdf([extractionTests.invoiceText()]), "application/pdf")}).status_code, 201)
        self.fakeClient.models.generate_content.side_effect = RuntimeError(errorMessage)
        with patch.object(self.geminiModule.time, "sleep") as sleepMock:
            result = client.post("/tasks", json={"task": "Process the latest Nova invoice", "mode": "auto"}).json()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["execution_mode"], "local_fallback")
        self.assertEqual(self.fakeClient.models.generate_content.call_count, 3)
        self.assertEqual([call.args[0] for call in sleepMock.call_args_list], [expectedDelay, expectedDelay])
        row = next(row for row in client.get("/tasks/history").json() if row["id"] == result["task_run_id"])
        self.assertEqual(row["execution_mode"], "local_fallback")

    def testAutoDoesNotMaskPermissionErrorsWithFallback(self):
        client = self.apiClient()
        self.fakeClient.models.generate_content.side_effect = RuntimeError("PERMISSION_DENIED")
        with patch.object(self.geminiModule.time, "sleep"):
            result = client.post("/tasks", json={"task": "Process Nova invoice", "mode": "auto"}).json()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["execution_mode"], "gemini")
        self.assertNotIn("fallback_reason", result)

    def testMissingGeminiResponseReturnsStructuredFailure(self):
        self.fakeClient.models.generate_content.return_value = None
        result = self.geminiModule.GeminiTaskAgent().run("Process Nova invoice")
        self.assertEqual(result["status"], "failed")
        self.assertIn("response", result.get("reason", "").lower())

    def testMalformedGeminiCallArgumentsReturnStructuredFailure(self):
        self.fakeClient.models.generate_content.return_value = self.toolResponse("read_invoice", "not a mapping")
        result = self.geminiModule.GeminiTaskAgent().run("Process Nova invoice")
        self.assertEqual(result["status"], "failed")

    def testMalformedGeminiCandidatesReturnStructuredFailure(self):
        response = self.toolResponse("read_invoice", {"filename": "test.pdf"})
        response.candidates = []
        self.fakeClient.models.generate_content.return_value = response
        result = self.geminiModule.GeminiTaskAgent().run("Process Nova invoice")
        self.assertEqual(result["status"], "failed")

    def testUnexpectedToolResultIsRejectedInsteadOfEscaping(self):
        with patch.object(self.geminiModule, "execute_tool", return_value=None):
            result = self.geminiModule.GeminiTaskAgent().executeValidatedTool("read_invoice", {"filename": "test.pdf"})
        self.assertIsInstance(result, dict)
        self.assertFalse(result["success"])

    def testUnknownToolCannotBeReportedAsSuccessfulTask(self):
        self.fakeClient.models.generate_content.side_effect = [self.toolResponse("unknown_tool", {}), SimpleNamespace(function_calls=[], text="Done")]
        result = self.geminiModule.GeminiTaskAgent().run("Process Nova invoice")
        self.assertNotEqual(result["status"], "completed")


if __name__ == "__main__":
    unittest.main()
