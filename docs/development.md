# Development, migrations, and reproducible validation

Run commands from the repository root unless a section says otherwise. See the
README for normal Windows setup, versions, endpoints, and supported PDF formats.

## Environment and dependency files

- `.env.example` contains no credential; `.env` remains ignored.
- `GEMINI_API_KEY` is optional for startup/Local mode. Gemini constructs its client
  only when requested. Missing configuration gives a structured failure; Auto can
  fall back to Local. Never put API credentials in a `VITE_` variable.
- `backend/requirements.txt` pins runtime direct dependencies to inspected versions.
- `backend/requirements-dev.txt` adds TestClient's explicit httpx requirement.
- `backend/requirements.lock.txt` constrains the tested transitive resolution.
- `frontend/package-lock.json` is committed; use `npm.cmd ci`, not an unrecorded
  dependency upgrade. Node 22.12+ is required for the tested browser harness.

## Database creation and versioned migration

Create empty tables without loading the AI integration:

```powershell
.\.venv\Scripts\python.exe -c "from backend.app.database import Base, engine; import backend.app.models; Base.metadata.create_all(bind=engine)"
```

Apply only to an explicitly selected existing database:

```powershell
.\.venv\Scripts\python.exe backend\migrations\0001_invoice_vendor_uniqueness.py data\taskpilot.db
```

The migration ID is `0001_invoice_vendor_uniqueness`. Its applied-state check is
the verified index definition, not a migration ledger. It uses `BEGIN IMMEDIATE`,
checks `lower(trim(vendor)), invoice_number` duplicates before DDL, and rolls back
on failure. No records or other tables are changed. A same-name incorrect index
causes an abort; it is not silently replaced. Repeating the migration is safe.

`Base.metadata.create_all()` alone does not apply this index to an existing table.
The current application startup also does not automatically execute migrations.

### Back up an existing database first

Pause application writers yourself; do not forcibly terminate processes. Use
SQLite's backup API, a new filename, and an integrity check. For example, this
creates a new backup and refuses to overwrite an existing destination:

```powershell
@'
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3

sourcePath = Path('data/taskpilot.db').resolve(strict=True)
backupDirectory = sourcePath.parent / 'backups'
backupDirectory.mkdir(exist_ok=True)
backupPath = backupDirectory / ('taskpilot_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '.db')
with backupPath.open('xb'):
    pass
source = sqlite3.connect(sourcePath.as_uri() + '?mode=ro', uri=True)
target = sqlite3.connect(backupPath)
try:
    source.backup(target)
    assert target.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
finally:
    source.close()
    target.close()
print(backupPath)
print(hashlib.sha256(backupPath.read_bytes()).hexdigest())
'@ | .\.venv\Scripts\python.exe -B -
```

Review counts, schema, and relevant invoice/history records against the source
before applying a migration. Keep the backup. Do not use blind filesystem copies
of a database that might have active writers or a WAL journal.

## Test isolation and commands

`syntheticFixtures.py` creates a public synthetic baseline (nine invoices, six
vendors, four synthetic history rows, and generated PDFs) under the OS temporary
directory. Existing assertions still check the same business expectations without
reading a developer's records. Browser tests create their own synthetic database.
Actual storage, if present, is separately hash-checked and never seeded by tests.

```powershell
.\.venv\Scripts\python.exe -B backend\tests\runStep4Tests.py
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p step6WorkflowTests.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p freshInstallSmokeTests.py -v
```

The suites are separate intentionally: the first contains the 89 legacy cases,
the second the 22 Step 6 cases, and the third the fresh-install smoke checks.
Repeated baseline/final runs are not extra unique tests. Migration source/test
files remain unchanged; the runner supplies a temporary pre-migration fixture.

```powershell
Set-Location frontend
npm.cmd test
npm.cmd run test:browser
npm.cmd run lint
npm.cmd run build
```

Browser tests require an installed Chrome/Chromium executable. On a machine with
a nonstandard installation, set `TASKPILOT_BROWSER` to its executable path before
running. The harness uses fresh profiles, dynamic ports, and Node's native
WebSocket rather than downloading an automation framework. It blocks external
font requests for deterministic functional checks. Mobile coverage is viewport
emulation, not a physical-device test. The test-only proxy does not change the
production API; configured production/dev CORS origins are checked separately.

Network deadlines include JSON body reads. Upload/list/history use 60 seconds;
task submission uses 180 seconds. Browser deadline tests accelerate only these
timers; they do not wait for a full three-minute outage. Server work can finish
after client timeout, so inspect history/listing before retrying. No request is
automatically replayed by the frontend.

## Demo utilities and local files

`python -m backend.app.seed` optionally creates three synthetic demo invoice rows
only if there are no invoices. It is not required for startup or tests. The
`data/create_invoices.py` utility generates the tracked demo PDFs and can overwrite
those filenames; do not run it over data you need to preserve. The legacy
`backend.app.reset_demo` script deletes a demo invoice and is deliberately excluded
from setup instructions. Never use either tool as an automatic migration/reset.

Do not stage `.env`, SQLite files/journals, `data/backups/`, new uploaded PDFs,
`.venv/`, `node_modules/`, `dist/`, Python caches, or `frontend/test-results/`.
Do stage source, manifests/lockfiles, migrations, synthetic fixture generators,
tests, README, and Markdown validation reports. Do not remove an untracked file
merely because Git does not know it.

## Release boundaries

The tests support local/prototype workflows, not a production certification.
Live Gemini behavior, real-world document diversity, other browsers, physical
devices, production TLS/proxies, authentication/authorization, capacity, and
process/disk failure recovery still need separate review. Existing text-layer,
DMY, INR-only persistence, float-storage, and Unicode normalization limitations
remain documented in the README. No deployment or push is part of this step.
