r"""Run all backend regressions without writing to the live database or PDFs.

Usage from project root:
    .venv\Scripts\python.exe -B backend/tests/runStep4Tests.py

Migration tests need a pre-migration fixture. A generated synthetic database is
backed up into a temporary database; ONLY that copy has its index removed.
The unchanged migration suite is pointed at this fixture in memory. Actual local
storage, when present, is separately hash-checked and never seeded by tests.
"""

import ast
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


projectRoot = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(projectRoot))
from backend.tests.syntheticFixtures import getTestDataset


def hashFiles():
    protectedPaths = [projectRoot / "data/taskpilot.db"]
    protectedPaths += list((projectRoot / "data").rglob("*.pdf"))
    protectedPaths += list(projectRoot.glob("*.pdf"))
    protectedPaths += list((projectRoot / "data/backups").rglob("*"))
    protectedPaths += list((projectRoot / "backend/migrations").glob("*.py"))
    protectedPaths += list((projectRoot / "frontend/src").rglob("*"))
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protectedPaths if path.is_file()}


def inspectDatabase(databasePath):
    connection = sqlite3.connect(databasePath.as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        return (
            connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall(),
            connection.execute("SELECT * FROM invoices ORDER BY id").fetchall(),
            connection.execute("SELECT * FROM task_runs ORDER BY id").fetchall(),
        )
    finally:
        connection.close()


def main():
    databasePath = projectRoot / "data/taskpilot.db"
    originalHashes = hashFiles()
    originalDatabase = inspectDatabase(databasePath) if databasePath.exists() else None
    fixtureDatabasePath = getTestDataset().databasePath
    result = None
    try:
        pythonPaths = list((projectRoot / "backend").rglob("*.py"))
        for pythonPath in pythonPaths:
            ast.parse(pythonPath.read_text(encoding="utf-8"), filename=str(pythonPath))
        print(f"PASS: syntax validation ({len(pythonPaths)} Python files)", flush=True)
        with tempfile.TemporaryDirectory(prefix="taskpilot-step4-migrations-") as directory:
            fixturePath = Path(directory) / "premigration.db"
            sourceConnection = sqlite3.connect(fixtureDatabasePath.as_uri() + "?mode=ro", uri=True)
            fixtureConnection = sqlite3.connect(fixturePath)
            try:
                sourceConnection.execute("PRAGMA query_only=ON")
                sourceConnection.backup(fixtureConnection)
                fixtureConnection.execute("DROP INDEX ux_invoices_vendor_invoice_number_normalized")
                fixtureConnection.commit()
            finally:
                sourceConnection.close()
                fixtureConnection.close()
            moduleSpec = importlib.util.spec_from_file_location("step4MigrationTests", projectRoot / "backend/migrations/test_invoice_vendor_uniqueness.py")
            migrationTests = importlib.util.module_from_spec(moduleSpec)
            sys.modules[moduleSpec.name] = migrationTests
            moduleSpec.loader.exec_module(migrationTests)
            migrationTests.sourcePath = fixturePath
            migrationSuite = unittest.defaultTestLoader.loadTestsFromModule(migrationTests)
            regressionSuite = unittest.defaultTestLoader.discover(str(projectRoot / "backend/tests"), pattern="test*.py")
            print(f"Running {migrationSuite.countTestCases()} migration tests and {regressionSuite.countTestCases()} application tests", flush=True)
            result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([migrationSuite, regressionSuite]))
    finally:
        assert hashFiles() == originalHashes, "A protected database/PDF/migration/backup/frontend file changed"
        assert (inspectDatabase(databasePath) if databasePath.exists() else None) == originalDatabase, "The real database changed"
        print("PASS: real database integrity/schema/index/invoices/history unchanged", flush=True)
        print("PASS: protected database/PDF/migration/backup/frontend SHA-256 hashes unchanged", flush=True)
    return 0 if result and result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
