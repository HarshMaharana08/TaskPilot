"""Run from the project root with python -B -m unittest discover -s backend/migrations -v.

The live database is opened read-only for SQLite backup into a temporary folder.
All migrations and insertion attempts target disposable copies exclusively.
"""

import hashlib
import importlib.util
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


migrationPath = Path(__file__).with_name("0001_invoice_vendor_uniqueness.py")
moduleSpec = importlib.util.spec_from_file_location("invoiceMigration", migrationPath)
migration = importlib.util.module_from_spec(moduleSpec)
moduleSpec.loader.exec_module(migration)
sourcePath = Path(__file__).resolve().parents[2] / "data" / "taskpilot.db"


def snapshotDatabase(connection):
    schemaRows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()
    tableData = {}
    for (tableName,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        quotedName = '"' + tableName.replace('"', '""') + '"'
        tableData[tableName] = connection.execute(
            f"SELECT * FROM {quotedName} ORDER BY rowid"
        ).fetchall()
    return schemaRows, tableData


class InvoiceMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sourceHash = hashlib.sha256(sourcePath.read_bytes()).hexdigest()
        cls.tempDirectory = tempfile.TemporaryDirectory(prefix="taskpilot-migration-")
        cls.backupPath = Path(cls.tempDirectory.name) / "baseline.db"
        sourceConnection = sqlite3.connect(sourcePath.as_uri() + "?mode=ro", uri=True)
        backupConnection = sqlite3.connect(cls.backupPath)
        try:
            sourceConnection.execute("PRAGMA query_only=ON")
            sourceConnection.backup(backupConnection)
        finally:
            sourceConnection.close()
            backupConnection.close()

    @classmethod
    def tearDownClass(cls):
        try:
            assert hashlib.sha256(sourcePath.read_bytes()).hexdigest() == cls.sourceHash
            print("\nPASS: active database SHA-256 unchanged")
        finally:
            cls.tempDirectory.cleanup()

    def setUp(self):
        self.databasePath = Path(self.tempDirectory.name) / (self.id().split(".")[-1] + ".db")
        shutil.copyfile(self.backupPath, self.databasePath)
        self.connection = sqlite3.connect(self.databasePath)
        self.addCleanup(self.connection.close)

    def insertInvoice(self, vendor, invoiceNumber):
        self.connection.execute(
            "INSERT INTO invoices (vendor, invoice_number, invoice_date, due_date, amount, status) "
            "VALUES (?, ?, '2026-11-01', '2026-12-01', 1000, 'Pending')",
            (vendor, invoiceNumber),
        )

    def assertDuplicateRejected(self, vendor):
        migration.migrateDatabase(self.databasePath)
        self.connection.execute("BEGIN")
        try:
            with self.assertRaises(sqlite3.IntegrityError) as errorContext:
                self.insertInvoice(vendor, "INV-105")
            self.assertIn(migration.indexName, str(errorContext.exception))
        finally:
            self.connection.rollback()

    def testExistingInvoicesPreserved(self):
        beforeRows = self.connection.execute("SELECT * FROM invoices ORDER BY id").fetchall()
        self.assertEqual(len(beforeRows), 9)
        migration.migrateDatabase(self.databasePath)
        self.assertEqual(self.connection.execute("SELECT * FROM invoices ORDER BY id").fetchall(), beforeRows)

    def testSixVendorsPreserved(self):
        beforeVendors = self.connection.execute("SELECT DISTINCT vendor FROM invoices ORDER BY vendor").fetchall()
        self.assertEqual(len(beforeVendors), 6)
        migration.migrateDatabase(self.databasePath)
        self.assertEqual(self.connection.execute("SELECT DISTINCT vendor FROM invoices ORDER BY vendor").fetchall(), beforeVendors)

    def testCorrectUniqueExpressionIndex(self):
        self.assertEqual(migration.migrateDatabase(self.databasePath), "applied")
        self.assertTrue(migration.verifyIndexDefinition(self.connection))
        storedSql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (migration.indexName,)
        ).fetchone()[0]
        self.assertEqual(storedSql, migration.indexSql)
        self.assertEqual(self.connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])

    def testExactDuplicateRejected(self):
        self.assertDuplicateRejected("Acme Corporation")

    def testCaseDuplicateRejected(self):
        self.assertDuplicateRejected("ACME CORPORATION")

    def testPaddedDuplicateRejected(self):
        self.assertDuplicateRejected("  Acme Corporation  ")

    def testDifferentVendorsCanShareNumber(self):
        migration.migrateDatabase(self.databasePath)
        self.connection.execute("BEGIN")
        try:
            self.insertInvoice("Migration Vendor One", "MIGRATION-SHARED")
            self.insertInvoice("Migration Vendor Two", "MIGRATION-SHARED")
            self.assertEqual(self.connection.execute(
                "SELECT COUNT(*) FROM invoices WHERE invoice_number='MIGRATION-SHARED'"
            ).fetchone()[0], 2)
        finally:
            self.connection.rollback()

    def testSecondRunIsIdempotent(self):
        migration.migrateDatabase(self.databasePath)
        beforeSnapshot = snapshotDatabase(self.connection)
        self.assertEqual(migration.migrateDatabase(self.databasePath), "already applied")
        self.assertEqual(snapshotDatabase(self.connection), beforeSnapshot)

    def testOtherTablesAndIndexesPreserved(self):
        beforeSchema, beforeData = snapshotDatabase(self.connection)
        self.assertIn("task_runs", beforeData)
        migration.migrateDatabase(self.databasePath)
        afterSchema, afterData = snapshotDatabase(self.connection)
        self.assertEqual(afterData, beforeData)
        self.assertEqual([row for row in afterSchema if row[1] != migration.indexName], beforeSchema)

    def testDuplicateDatabaseAbortsWithoutChanges(self):
        self.insertInvoice("  ACME CORPORATION  ", "INV-105")
        self.connection.commit()
        beforeSnapshot = snapshotDatabase(self.connection)
        with self.assertRaisesRegex(ValueError, "Normalized duplicate"):
            migration.migrateDatabase(self.databasePath)
        self.assertEqual(snapshotDatabase(self.connection), beforeSnapshot)
        self.assertFalse(migration.verifyIndexDefinition(self.connection))

    def testWrongNamedIndexDefinitionsRejected(self):
        wrongDefinitions = [
            f"CREATE INDEX {migration.indexName} ON invoices (lower(trim(vendor)), invoice_number)",
            f"CREATE UNIQUE INDEX {migration.indexName} ON invoices (vendor, invoice_number)",
            f"CREATE UNIQUE INDEX {migration.indexName} ON invoices (lower(vendor), invoice_number)",
            f"CREATE UNIQUE INDEX {migration.indexName} ON invoices (lower(trim(vendor)), invoice_number) WHERE id > 0",
            f"CREATE UNIQUE INDEX {migration.indexName} ON invoices (lower(trim(vendor)), invoice_number COLLATE NOCASE)",
            f"CREATE UNIQUE INDEX {migration.indexName} ON task_runs (id)",
        ]
        for wrongSql in wrongDefinitions:
            with self.subTest(sql=wrongSql):
                self.connection.execute(wrongSql)
                self.connection.commit()
                beforeSnapshot = snapshotDatabase(self.connection)
                with self.assertRaisesRegex(ValueError, "unexpected definition"):
                    migration.migrateDatabase(self.databasePath)
                self.assertEqual(snapshotDatabase(self.connection), beforeSnapshot)
                self.connection.execute(f"DROP INDEX {migration.indexName}")
                self.connection.commit()

    def testFailureAfterCreationRollsBackIndex(self):
        beforeSnapshot = snapshotDatabase(self.connection)
        with patch.object(migration, "verifyIndexDefinition", side_effect=[False, RuntimeError("injected failure")]):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                migration.migrateDatabase(self.databasePath)
        self.assertEqual(snapshotDatabase(self.connection), beforeSnapshot)
        self.assertFalse(migration.verifyIndexDefinition(self.connection))

    def testMissingPathDoesNotCreateDatabase(self):
        missingPath = Path(self.tempDirectory.name) / "missing.db"
        with self.assertRaises(FileNotFoundError):
            migration.migrateDatabase(missingPath)
        self.assertFalse(missingPath.exists())

    def testCliRequiresExplicitPathAndCanRepeat(self):
        missingResult = subprocess.run([sys.executable, "-B", str(migrationPath)], capture_output=True, text=True)
        self.assertEqual(missingResult.returncode, 2)
        for expectedResult in ("applied", "already applied"):
            cliResult = subprocess.run(
                [sys.executable, "-B", str(migrationPath), str(self.databasePath)],
                capture_output=True, text=True,
            )
            self.assertEqual(cliResult.returncode, 0, cliResult.stderr)
            self.assertEqual(cliResult.stdout.strip(), f"{migration.migrationId}: {expectedResult}")


if __name__ == "__main__":
    unittest.main()
