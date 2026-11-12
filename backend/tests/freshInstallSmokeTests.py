"""Fresh-source/empty-storage smoke tests; no active data or live AI requests."""

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


projectRoot = Path(__file__).resolve().parents[2]


class FreshInstallSmokeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="taskpilot-fresh-smoke-")
        self.addCleanup(self.directory.cleanup)
        self.checkout = Path(self.directory.name)
        shutil.copytree(projectRoot / "backend/app", self.checkout / "backend/app", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(projectRoot / "backend/migrations", self.checkout / "backend/migrations", ignore=shutil.ignore_patterns("__pycache__"))
        self.childEnvironment = dict(os.environ)
        self.childEnvironment["GEMINI_API_KEY"] = ""
        self.childEnvironment.pop("PYTHONPATH", None)

    def runPython(self, *arguments):
        result = subprocess.run([sys.executable, "-B", *arguments], cwd=self.checkout, env=self.childEnvironment, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout

    def initializeDatabase(self):
        self.runPython("-c", "from backend.app.database import Base, engine; import backend.app.models; Base.metadata.create_all(bind=engine)")

    def migrateDatabase(self):
        return self.runPython("backend/migrations/0001_invoice_vendor_uniqueness.py", "data/taskpilot.db")

    def testDeclaredDependenciesMatchInstalledVersions(self):
        for manifest in ("requirements.txt", "requirements-dev.txt"):
            for line in (projectRoot / "backend" / manifest).read_text(encoding="utf-8").splitlines():
                if not line or line.startswith(("#", "-r")):
                    continue
                package, version = line.split("==")
                self.assertEqual(importlib.metadata.version(package.split("[")[0]), version)

    def testEmptyInitializationMigrationAndRepeat(self):
        self.assertFalse((self.checkout / "data").exists())
        self.initializeDatabase()
        self.assertIn(": applied", self.migrateDatabase())
        self.assertIn(": already applied", self.migrateDatabase())
        output = self.runPython("-c", "import sqlite3; c=sqlite3.connect('data/taskpilot.db'); assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; assert c.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]==0; assert c.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0]==0; assert any(r[1]=='ux_invoices_vendor_invoice_number_normalized' and r[2]==1 for r in c.execute('PRAGMA index_list(invoices)')); c.close(); print('fresh index ok')")
        self.assertIn("fresh index ok", output)

    def testLocalStartupWithoutKeyRecordsOrPdfs(self):
        output = self.runPython("-c", """
from unittest.mock import patch
from fastapi.testclient import TestClient
with patch('google.genai.Client', side_effect=AssertionError('No AI client at local startup')):
    from backend.app.main import app
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status':'healthy'}
        assert client.get('/tools/invoices').json() == []
        assert client.get('/tasks/history').json() == []
print('startup without local data or credentials ok')
""")
        self.assertIn("startup without local data or credentials ok", output)

    def testFreshUploadLocalRepeatAndUnconfiguredAuto(self):
        self.initializeDatabase()
        self.migrateDatabase()
        output = self.runPython("-c", """
from unittest.mock import patch
from fastapi.testclient import TestClient
import json, pymupdf, sqlite3
with pymupdf.open() as document:
    page=document.new_page()
    page.insert_text((40,40),'Vendor: Fresh Install Services\\nInvoice Number: FRESH-001\\nInvoice Date: 2026-11-05\\nDue Date: 2026-12-05\\nTotal Amount: INR 75000')
    content=document.tobytes()
with patch('google.genai.Client', side_effect=AssertionError('Live AI forbidden')):
    from backend.app.main import app
    with TestClient(app) as client:
        upload=client.post('/invoices/upload',files={'uploadedFile':('fresh.pdf',content,'application/pdf')})
        assert upload.status_code==201, upload.text
        for mode in ('local','local','auto'):
            result=client.post('/tasks',json={'task':'Process the latest Fresh Install Services invoice','mode':mode}).json()
            assert result['status']=='completed', result
            if mode=='auto': assert result['execution_mode']=='local_fallback'
        missing=client.post('/tasks',json={'task':'Process Fresh Install Services invoice','mode':'gemini'}).json()
        assert missing['status']=='failed' and 'not configured' in missing['error']
        assert len(client.get('/tasks/history').json())==4
connection=sqlite3.connect('data/taskpilot.db')
assert connection.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]==1
assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
connection.close()
print('fresh workflow and optional Gemini configuration ok')
""")
        self.assertIn("fresh workflow and optional Gemini configuration ok", output)

    def testOptionalDemoSeedingDoesNotDuplicateOnRepeat(self):
        self.runPython("-m", "backend.app.seed")
        self.migrateDatabase()
        self.assertIn("already contains invoices", self.runPython("-m", "backend.app.seed"))
        self.runPython("-c", "import sqlite3; c=sqlite3.connect('data/taskpilot.db'); assert c.execute('SELECT COUNT(*) FROM invoices').fetchone()[0]==3; assert c.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0]==0; c.close()")


if __name__ == "__main__":
    unittest.main()
