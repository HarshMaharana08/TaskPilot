"""Step 3D regressions. Run: python -B -m unittest discover -s backend/tests -v.

SQLite backup reads a generated synthetic baseline in mode=ro. All application connections
are redirected before importing main (including its create_all call). Each test
uses a fresh disposable copy. Gemini client construction and execution are
blocked. Hashes protect the real database, migration, backups, and untouched code.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import date
import hashlib
import importlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker
from backend.tests.syntheticFixtures import getTestDataset


projectRoot = Path(__file__).resolve().parents[2]
sourcePath = getTestDataset().databasePath
indexName = "ux_invoices_vendor_invoice_number_normalized"


def fileHash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def databaseSnapshot(connection):
    return (
        connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall(),
        connection.execute("SELECT * FROM invoices ORDER BY id").fetchall(),
        connection.execute("SELECT * FROM task_runs ORDER BY id").fetchall(),
    )


class InvoiceCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classStack = ExitStack()
        cls.addClassCleanup(cls.classStack.close)
        protectedPaths = [sourcePath]
        protectedPaths += list((projectRoot / "backend" / "migrations").glob("*.py"))
        protectedPaths += list((projectRoot / "data" / "backups").glob("*"))
        protectedPaths += [path for path in (projectRoot / "backend" / "app").glob("*.py") if path.name != "tools.py"]
        cls.protectedHashes = {path: fileHash(path) for path in protectedPaths if path.is_file()}
        cls.temporaryDirectory = cls.classStack.enter_context(tempfile.TemporaryDirectory(prefix="taskpilot-step3d-"))
        cls.baselinePath = Path(cls.temporaryDirectory) / "baseline.db"
        sourceConnection = sqlite3.connect(sourcePath.as_uri() + "?mode=ro", uri=True)
        copyConnection = sqlite3.connect(cls.baselinePath)
        try:
            sourceConnection.execute("PRAGMA query_only=ON")
            sourceConnection.execute("BEGIN")
            assert sourceConnection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            cls.sourceSnapshot = databaseSnapshot(sourceConnection)
            assert len(cls.sourceSnapshot[1]) == 9
            assert len(cls.sourceSnapshot[2]) == 4
            assert sourceConnection.execute("SELECT COUNT(DISTINCT lower(trim(vendor))) FROM invoices").fetchone()[0] == 6
            assert sourceConnection.execute("SELECT sql FROM sqlite_master WHERE name=?", (indexName,)).fetchone()[0] == (
                f"CREATE UNIQUE INDEX {indexName} ON invoices (lower(trim(vendor)), invoice_number)"
            )
            sourceConnection.backup(copyConnection)
        finally:
            sourceConnection.close()
            copyConnection.close()

        databaseModule = importlib.import_module("backend.app.database")
        originalEngine = databaseModule.engine

        def rejectRealConnection(*arguments, **keywords):
            raise AssertionError("Application attempted to connect to the real database")

        event.listen(originalEngine, "do_connect", rejectRealConnection)
        cls.classStack.callback(event.remove, originalEngine, "do_connect", rejectRealConnection)
        bootstrapEngine = create_engine(f"sqlite:///{cls.baselinePath}")
        cls.classStack.callback(bootstrapEngine.dispose)
        cls.classStack.enter_context(patch.object(databaseModule, "engine", bootstrapEngine))
        cls.classStack.enter_context(patch.object(databaseModule, "SessionLocal", sessionmaker(bind=bootstrapEngine)))
        cls.classStack.enter_context(patch.dict(os.environ, {"GEMINI_API_KEY": "offline-test-placeholder"}))
        cls.classStack.enter_context(patch("google.genai.Client", side_effect=AssertionError("Unexpected Gemini client creation")))
        # Import main's existing Gemini dependency without constructing a live client.
        with patch("google.genai.Client", return_value=Mock()):
            cls.mainModule = importlib.import_module("backend.app.main")
        cls.classStack.enter_context(patch.object(cls.mainModule.GeminiTaskAgent, "run", side_effect=AssertionError("Gemini execution forbidden")))
        cls.toolsModule = importlib.import_module("backend.app.tools")
        cls.classStack.enter_context(patch.object(cls.toolsModule, "INVOICE_DIR", getTestDataset().invoiceDirectory))
        cls.agentModule = importlib.import_module("backend.app.agent")
        cls.invoiceModel = importlib.import_module("backend.app.models").Invoice

    @classmethod
    def tearDownClass(cls):
        for path, originalHash in cls.protectedHashes.items():
            assert fileHash(path) == originalHash, f"Protected file changed: {path}"
        sourceConnection = sqlite3.connect(sourcePath.as_uri() + "?mode=ro", uri=True)
        try:
            sourceConnection.execute("PRAGMA query_only=ON")
            assert sourceConnection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert databaseSnapshot(sourceConnection) == cls.sourceSnapshot
        finally:
            sourceConnection.close()
        print("\nPASS: synthetic baseline SHA-256/schema/index/integrity/9 invoices/6 vendors/4 history rows unchanged")
        print("PASS: migration, backups, and all other application source hashes unchanged")

    def setUp(self):
        self.databasePath = Path(self.temporaryDirectory) / (self.id().split(".")[-1] + ".db")
        shutil.copyfile(self.baselinePath, self.databasePath)
        self.engine = create_engine(f"sqlite:///{self.databasePath}", connect_args={"check_same_thread": False, "timeout": 20})
        self.addCleanup(self.engine.dispose)
        self.sessionFactory = sessionmaker(bind=self.engine)
        self.testStack = ExitStack()
        self.addCleanup(self.testStack.close)
        self.testStack.enter_context(patch.object(self.toolsModule, "SessionLocal", self.sessionFactory))
        self.testStack.enter_context(patch.object(self.mainModule, "SessionLocal", self.sessionFactory))
        self.testStack.enter_context(patch.object(self.toolsModule, "CREATE_FAILURE_SIMULATION", False))

    def createInvoice(self, vendor="Step3D Vendor", invoiceNumber="STEP3D-001", **overrides):
        invoiceData = dict(vendor=vendor, invoice_number=invoiceNumber, invoice_date="2026-11-01", due_date="2026-12-01", amount=1200)
        invoiceData.update(overrides)
        return self.toolsModule.create_invoice(**invoiceData)

    def countInvoices(self):
        with self.sessionFactory() as session:
            return session.scalar(select(func.count()).select_from(self.invoiceModel))

    def testNewVendorInvoiceSucceeds(self):
        result = self.createInvoice()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["invoice"]["vendor"], "Step3D Vendor")
        self.assertEqual(self.countInvoices(), 10)

    def testDuplicateExactCaseAndPaddingReturnOriginal(self):
        original = self.createInvoice()
        for vendor in ("Step3D Vendor", "STEP3D VENDOR", "  Step3D Vendor  "):
            with self.subTest(vendor=vendor):
                duplicate = self.createInvoice(vendor)
                self.assertTrue(duplicate["success"], duplicate)
                self.assertTrue(duplicate["already_exists"])
                self.assertEqual(duplicate["invoice"]["id"], original["invoice"]["id"])
        self.assertEqual(self.countInvoices(), 10)

    def testDifferentVendorsShareNumberAndLegacySearchIsAmbiguous(self):
        first = self.createInvoice("Step3D Alpha")
        second = self.createInvoice("Step3D Beta")
        self.assertTrue(first["success"] and second["success"])
        self.assertNotEqual(first["invoice"]["id"], second["invoice"]["id"])
        for vendor, expected in (("Step3D Alpha", first), ("Step3D Beta", second)):
            self.assertEqual(self.toolsModule.search_invoice("STEP3D-001", vendor)["invoice"]["id"], expected["invoice"]["id"])
        self.assertTrue(self.toolsModule.search_invoice("STEP3D-001")["ambiguous"])

    def testExistingFourVendorSearchesAndVerification(self):
        cases = [
            ("INV-105", "Acme Corporation", "2026-10-01", "2026-11-01", 84500),
            ("INV-202", "Globex Corporation", "2026-10-01", "2026-11-01", 98000),
            ("MS-2026-101", "Microsoft Corporation", "2026-11-01", "2026-12-01", 125000),
            ("OR-2026-501", "Oracle Corporation", "2026-11-05", "2026-12-05", 75000),
        ]
        for number, vendor, invoiceDate, dueDate, amount in cases:
            with self.subTest(vendor=vendor):
                self.assertTrue(self.toolsModule.search_invoice(number, "  " + vendor.upper() + "  ")["found"])
                self.assertTrue(self.toolsModule.verify_invoice(number, vendor, invoiceDate, dueDate, amount)["verified"])

    def testWrongVendorAndMismatchedFieldsFailVerification(self):
        self.assertFalse(self.toolsModule.verify_invoice("INV-105", "Globex Corporation", "2026-10-01", "2026-11-01", 84500)["verified"])
        result = self.toolsModule.verify_invoice("INV-105", "Acme Corporation", "2020-01-01", "2020-02-01", 1)
        self.assertFalse(result["verified"])
        self.assertEqual({entry["field"] for entry in result["mismatches"]}, {"invoice_date", "due_date", "amount"})

    def testSessionRecoversAfterRealUniquenessViolation(self):
        with self.sessionFactory() as session:
            session.add(self.invoiceModel(vendor="  ACME CORPORATION ", invoice_number="INV-105", invoice_date=date(2026, 11, 1), due_date=date(2026, 12, 1), amount=10, status="Pending"))
            with self.assertRaises(IntegrityError):
                session.commit()
            session.rollback()
            self.assertEqual(session.scalar(select(func.count()).select_from(self.invoiceModel)), 9)
            session.add(self.invoiceModel(vendor="Recovered Session Vendor", invoice_number="RECOVERED-1", invoice_date=date(2026, 11, 1), due_date=date(2026, 12, 1), amount=10, status="Pending"))
            session.commit()
        self.assertEqual(self.countInvoices(), 10)

    def testGenuineQueryErrorIsNotDuplicateSuccess(self):
        brokenSession = Mock()
        brokenSession.query.side_effect = OperationalError("SELECT", {}, sqlite3.OperationalError("disk I/O error"))
        with patch.object(self.toolsModule, "SessionLocal", side_effect=[brokenSession, self.sessionFactory()]):
            result = self.createInvoice("Acme Corporation", "INV-105")
        self.assertFalse(result["success"], result)
        self.assertIn("disk I/O error", result["error"])
        brokenSession.rollback.assert_called_once()
        brokenSession.close.assert_called()

    def testOtherIntegrityErrorCannotRecoverAnExistingInvoice(self):
        brokenSession = Mock()
        brokenSession.query.side_effect = IntegrityError("INSERT", {}, sqlite3.IntegrityError("NOT NULL constraint failed: invoices.amount"))
        with patch.object(self.toolsModule, "SessionLocal", side_effect=[brokenSession, self.sessionFactory()]):
            result = self.createInvoice("Acme Corporation", "INV-105")
        self.assertFalse(result["success"], result)
        self.assertIn("NOT NULL", result["error"])
        brokenSession.rollback.assert_called_once()

    def testGenuineCommitErrorIsNotDuplicateSuccess(self):
        realSession = self.sessionFactory()
        self.addCleanup(realSession.close)

        def failCommit():
            # Another request can create the key while this request has an
            # unrelated failure. Existence alone must not conceal that error.
            with self.sessionFactory() as winnerSession:
                winnerSession.add(self.invoiceModel(vendor="Error Vendor", invoice_number="ERROR-001", invoice_date=date(2026, 11, 1), due_date=date(2026, 12, 1), amount=1200, status="Pending"))
                winnerSession.commit()
            raise OperationalError("INSERT", {}, sqlite3.OperationalError("disk I/O error"))

        with patch.object(realSession, "commit", side_effect=failCommit), patch.object(self.toolsModule, "SessionLocal", side_effect=[realSession, self.sessionFactory()]):
            result = self.createInvoice("Error Vendor", "ERROR-001")
        self.assertFalse(result["success"], result)
        self.assertIn("disk I/O error", result["error"])

    def testNonUniqueIntegrityErrorRemainsFailure(self):
        result = self.createInvoice(amount=None)
        self.assertFalse(result["success"], result)
        self.assertIn("NOT NULL", result["error"])
        self.assertEqual(self.countInvoices(), 9)
        self.assertTrue(self.createInvoice()["success"])

    def testPostCommitRefreshFailureRecoversCorrectInvoice(self):
        realSession = self.sessionFactory()
        self.addCleanup(realSession.close)
        with patch.object(realSession, "refresh", side_effect=RuntimeError("refresh interrupted")), patch.object(self.toolsModule, "SessionLocal", side_effect=[realSession, self.sessionFactory()]):
            result = self.createInvoice()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["recovered_after_error"])
        self.assertEqual(result["invoice"]["vendor"], "Step3D Vendor")
        self.assertEqual(self.countInvoices(), 10)

    def testUncertainDisconnectedCommitCanRecover(self):
        realSession = self.sessionFactory()
        self.addCleanup(realSession.close)
        actualCommit = realSession.commit

        def commitThenDisconnect():
            actualCommit()
            raise OperationalError("COMMIT", {}, sqlite3.OperationalError("connection lost"), connection_invalidated=True)

        with patch.object(realSession, "commit", side_effect=commitThenDisconnect), patch.object(self.toolsModule, "SessionLocal", side_effect=[realSession, self.sessionFactory()]):
            result = self.createInvoice()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["recovered_after_error"])
        self.assertEqual(self.countInvoices(), 10)

    def testDisconnectedCommitWithoutPersistedRowRemainsFailure(self):
        realSession = self.sessionFactory()
        self.addCleanup(realSession.close)
        error = OperationalError("COMMIT", {}, sqlite3.OperationalError("connection lost"), connection_invalidated=True)
        with patch.object(realSession, "commit", side_effect=error), patch.object(self.toolsModule, "SessionLocal", side_effect=[realSession, self.sessionFactory()]):
            result = self.createInvoice()
        self.assertFalse(result["success"], result)
        self.assertIn("connection lost", result["error"])
        self.assertEqual(self.countInvoices(), 9)

    def testUniqueRecoveryRollsBackBeforeLookupAndSelectsCorrectVendor(self):
        with self.sessionFactory() as session:
            sharedNumber = session.scalar(select(self.invoiceModel.invoice_number).where(self.invoiceModel.vendor == "Test Vendor Beta"))
        expectedInvoice = self.toolsModule.search_invoice(sharedNumber, "Test Vendor Beta")["invoice"]
        realSession = self.sessionFactory()
        self.addCleanup(realSession.close)
        recoverySession = self.sessionFactory()
        self.addCleanup(recoverySession.close)
        factoryCalls = []
        with patch.object(realSession, "rollback", wraps=realSession.rollback) as rollbackSpy:
            def trackedFactory():
                factoryCalls.append(1)
                if len(factoryCalls) == 1:
                    return realSession
                rollbackSpy.assert_called_once()
                self.assertFalse(realSession.in_transaction())
                return recoverySession

            # Force the stale pre-check that occurs when a competing request
            # inserts between the initial lookup and this request's flush.
            with patch.object(realSession, "query") as queryMock, patch.object(self.toolsModule, "SessionLocal", side_effect=trackedFactory):
                queryMock.return_value.filter.return_value.first.return_value = None
                result = self.createInvoice("  TEST VENDOR BETA  ", sharedNumber)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["recovered_after_error"])
        self.assertEqual(result["invoice"], expectedInvoice)
        self.assertEqual(self.countInvoices(), 9)

    def testFailedRecoveryLookupReturnsFailure(self):
        realSession = self.sessionFactory()
        self.addCleanup(realSession.close)
        brokenRecovery = Mock()
        brokenRecovery.query.side_effect = OperationalError("SELECT", {}, sqlite3.OperationalError("database unavailable"))
        with patch.object(realSession, "refresh", side_effect=RuntimeError("refresh interrupted")), patch.object(self.toolsModule, "SessionLocal", side_effect=[realSession, brokenRecovery]):
            result = self.createInvoice()
        self.assertFalse(result["success"], result)
        brokenRecovery.close.assert_called_once()
        self.assertEqual(self.countInvoices(), 10)

    def testTransientFailureThenRetry(self):
        self.toolsModule.CREATE_FAILURE_SIMULATION = True
        first = self.createInvoice()
        self.assertFalse(first["success"])
        self.assertIn("Simulated temporary", first["error"])
        self.assertEqual(self.countInvoices(), 9)
        self.assertTrue(self.createInvoice()["success"])
        self.assertEqual(self.countInvoices(), 10)

    def testConcurrentCreatorsRecoverAfterRealUniqueConflicts(self):
        creationBarrier = threading.Barrier(4)
        rollbackThreads = []
        stateLock = threading.Lock()

        class ConcurrentSession(Session):
            def rollback(session):
                with stateLock:
                    rollbackThreads.append(threading.get_ident())
                return super().rollback()

        def synchronizeFlush(session, flushContext, instances):
            if any(isinstance(record, self.invoiceModel) and record.invoice_number == "CONCURRENT-1" for record in session.new):
                creationBarrier.wait(timeout=15)

        event.listen(ConcurrentSession, "before_flush", synchronizeFlush)
        try:
            concurrentFactory = sessionmaker(bind=self.engine, class_=ConcurrentSession)
            with patch.object(self.toolsModule, "SessionLocal", concurrentFactory):
                with ThreadPoolExecutor(max_workers=4) as executor:
                    results = list(executor.map(lambda vendor: self.createInvoice(vendor, "CONCURRENT-1"), ["Race Vendor", "RACE VENDOR", " Race Vendor ", "race vendor"]))
            self.assertTrue(all(result["success"] for result in results), results)
            self.assertEqual(len({result["invoice"]["id"] for result in results}), 1)
            self.assertEqual(sum(bool(result.get("recovered_after_error")) for result in results), 3)
            self.assertEqual(len(rollbackThreads), 3)
            self.assertEqual(self.countInvoices(), 10)
            self.assertTrue(self.createInvoice("After Race Vendor", "AFTER-RACE")["success"])
        finally:
            event.remove(ConcurrentSession, "before_flush", synchronizeFlush)

    def testDynamicVendorIdentificationAndLocalPdfRegressions(self):
        for vendor, expectedNumber in (("Acme", "INV-105"), ("Globex", "INV-202"), ("Microsoft", "MS-2026-101"), ("Oracle", "OR-2026-501")):
            with self.subTest(vendor=vendor):
                result = self.agentModule.TaskAgent().run(f"Process the latest {vendor} invoice")
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["invoice"]["invoice_number"], expectedNumber)
                self.assertTrue(any(step["action"] == "create_invoice_skipped" for step in result["steps"]))
        self.assertEqual(self.countInvoices(), 9)

    def testLocalAgentCreatesMissingPdfInvoiceWithRetry(self):
        # Deletion is confined to this disposable copy; PDF contents stay intact.
        with self.engine.begin() as connection:
            connection.execute(self.invoiceModel.__table__.delete().where(self.invoiceModel.invoice_number == "INV-105"))
        self.toolsModule.CREATE_FAILURE_SIMULATION = True
        result = self.agentModule.TaskAgent().run("Process the latest Acme invoice")
        self.assertEqual(result["status"], "completed", result)
        steps = {step["action"]: step["result"] for step in result["steps"]}
        self.assertFalse(steps["create_invoice_attempt_1"]["success"])
        self.assertTrue(steps["create_invoice_attempt_2"]["success"])
        self.assertTrue(steps["verify_invoice"]["verified"])
        self.assertEqual(self.countInvoices(), 9)
        repeated = self.agentModule.TaskAgent().run("Process the latest Acme invoice")
        self.assertEqual(repeated["status"], "completed")
        self.assertEqual(self.countInvoices(), 9)

    def testFastApiSearchAndVerificationRegressions(self):
        cases = [
            ("INV-105", "Acme Corporation", "2026-10-01", "2026-11-01", 84500, True),
            ("INV-202", "Globex Corporation", "2026-10-01", "2026-11-01", 98000, True),
            ("MS-2026-101", "Microsoft Corporation", "2026-11-01", "2026-12-01", 125000, True),
            ("OR-2026-501", "Oracle Corporation", "2026-11-05", "2026-12-05", 75000, True),
            ("INV-105", "Globex Corporation", "2026-10-01", "2026-11-01", 84500, False),
        ]
        with TestClient(self.mainModule.app) as client:
            for number, vendor, invoiceDate, dueDate, amount, expected in cases:
                with self.subTest(number=number, vendor=vendor):
                    searchResponse = client.get(f"/tools/search/{number}", params={"vendor": vendor})
                    self.assertEqual(searchResponse.status_code, 200)
                    self.assertIs(searchResponse.json()["found"], expected)
                    verifyResponse = client.get(f"/tools/verify/{number}", params=dict(vendor=vendor, invoice_date=invoiceDate, due_date=dueDate, amount=amount))
                    self.assertEqual(verifyResponse.status_code, 200)
                    self.assertIs(verifyResponse.json()["verified"], expected)

    def testFastApiInvoiceCreationAndLocalTaskHistory(self):
        with TestClient(self.mainModule.app) as client:
            invoiceData = dict(vendor="API Vendor", invoice_number="API-001", invoice_date="2026-11-01", due_date="2026-12-01", amount=1200)
            first = client.post("/tools/invoices", json=invoiceData)
            self.assertEqual(first.status_code, 200)
            self.assertTrue(first.json()["success"])
            invoiceData["vendor"] = "  API VENDOR  "
            second = client.post("/tools/invoices", json=invoiceData)
            self.assertEqual(second.status_code, 200)
            self.assertTrue(second.json()["already_exists"])
            self.assertEqual(second.json()["invoice"]["id"], first.json()["invoice"]["id"])
            taskResponse = client.post("/tasks", json={"task": "Process the latest Oracle invoice", "mode": "local"})
            self.assertEqual(taskResponse.status_code, 200)
            self.assertEqual(taskResponse.json()["status"], "completed", taskResponse.json())
            self.assertEqual(taskResponse.json()["execution_mode"], "local")
            historyResponse = client.get("/tasks/history")
            self.assertEqual(historyResponse.status_code, 200)
            self.assertEqual(len(historyResponse.json()), 5)
            historyRow = next(row for row in historyResponse.json() if row["id"] == taskResponse.json()["task_run_id"])
            self.assertEqual(historyRow["status"], "completed")
        self.assertEqual(self.countInvoices(), 10)


if __name__ == "__main__":
    unittest.main()
