# Step 4: COMPLETE

Steps 4A through 4D completed. Final verification: **89 passed, 0 failed,
0 skipped**. No live Gemini calls, dependency installations, commits, pushes,
database changes, or Step 5 work were performed.

## 4A: Audit and baseline

Reviewed `agent.py`, `tools.py`, `invoiceUploadService.py`, `gemini_agent.py`,
`main.py`, `models.py`, `database.py`, and both existing Step 3 test modules.

| Function or flow | Existing behavior and finding |
| --- | --- |
| `saveInvoicePdf()` | Already checked extension, MIME, signature, 10 MiB size, PDF opening, passwords, and page count. Did not require readable text or a complete invoice. |
| `read_invoice()` | Concatenated text from all pages, so it did not assume a single-page document. Joined an unchecked caller-supplied path to the invoice directory, permitting traversal/absolute paths. Exposed exception strings. |
| `TaskAgent.extract_invoice_data()` | Explicit vendor labels plus a first-line company-heading fallback. Not exclusively dependent on first-line vendor names. Used first-match regexes, one invoice-number label, ISO-only dates, and direct float conversion. Could ignore conflicts, strip negative/currency prefixes, and consume the following field when a value was missing. |
| `identify_requested_vendor()` | Already identified vendors from PDF contents rather than filenames. Depends on extraction quality. |
| `find_latest_vendor_invoice()` / `find_latest_invoice()` | Already selected by extracted invoice date, with filenames used for date ties. Could silently skip undated documents or arbitrarily choose among conflicting latest-date invoices. |
| `TaskAgent.run()` | Had date, positive-amount, and due-date checks, but no centralized ambiguity or currency policy. Creates, retries, and verifies records. |
| `GeminiTaskAgent.run()` / `execute_tool()` | Used the same PDF-reading tools but independently interpreted raw text. No deterministic source-to-write gate; PDF instructions could influence interpretation. |
| FastAPI endpoints | Delegate to the existing services. Startup `create_all()` means tests must redirect the database before importing `main`. |
| Persistence | Float amount and no currency column. Existing normalized unique index and Step 3 transaction recovery must remain unchanged. |

Before editing, **21 compatibility tests and 14 migration tests passed**.
The migration tests used a disposable pre-migration fixture because the actual
database is already migrated. No migration assertions or files were changed.

## 4B: Extraction improvements

Added `invoiceExtractionService.py` as the deterministic parser and PDF text
reader used by Local execution and Gemini tool results.

- Vendor labels: Vendor, Supplier, Seller, From, Company, Billed From, and
  applicable Name variants. Handles capitalization, spacing, delimiters, and
  next-line values. Ignores recipient Company fields inside billing blocks.
- Preserves existing company-heading-plus-INVOICE layouts. Arbitrary first
  lines are not used as vendor names. Conflicting explicit vendors fail.
- Identifiers: Invoice Number, Invoice No., Invoice #, Invoice ID, Bill Number,
  Bill No., Invoice Reference Number, and Reference Number (Invoice). Bare
  references, purchase orders, tax identifiers, and customer IDs are not used.
  Identifiers allow letters, digits, hyphens, slashes, and underscores.
- Dates: ISO, year-first slashes, day-first slash/hyphen formats, day plus
  English month name, and English month name plus day/year. Values normalize
  to ISO. Missing/impossible/conflicting dates remain errors.
- Numeric day/month/year dates explicitly use **DMY**, matching the project's
  Indian context. Ambiguous dates produce a warning. Calling the parser with
  `numericDateOrder=None` rejects ambiguous numeric dates.
- Totals: Total Amount, Invoice Total, Grand Total, Amount Due, Total Due, and
  Balance Due. Subtotal, tax, unit prices, and line items are ignored.
- Money uses `Decimal` for validation and exact decimal text, with at most two
  decimal places and standard comma grouping. Supports bare values, INR,
  rupee, USD, and dollar formats. Explicit currency fields are respected.
- Multiple pages retain boundaries. Matching repeated fields are accepted;
  conflicting identifiers, totals, dates, vendors, or currencies fail.
- Terms/payment-instruction sections are excluded from field collection.
- Latest selection uses dates and invoice content, never filename ordering.
  Unknown/conflicting dates or vendors prevent an unreliable latest decision.
  Different invoices tied on the latest date require clarification; identical
  repeated uploads remain usable.

### Currency and precision decisions

No currency column or schema was introduced. The existing ledger is treated as
**INR-only**. Explicit non-INR invoices can be extracted and uploaded but are
blocked from agent persistence and verification, rather than silently mixing
currencies. Dollar-prefixed values use USD; there is no conversion.

Bare amounts retain the legacy INR ledger convention, with a warning that
currency was unspecified. Existing Acme/Globex PDFs encode their currency glyph
as U+00B7, not a readable rupee symbol. This exact legacy glyph (and U+FFFD) is
accepted with an explicit unreadable-currency warning; no currency is claimed
to have been extracted from it.

`amount_decimal` preserves the parsed cents, while the existing `amount` API and
database column remain floats. Binary floating-point precision limitations
therefore remain at the persistence boundary. Values above 999999999999.99,
negative/zero values, non-finite values, malformed grouping, and excessive
decimal precision are rejected. Indian lakh grouping and decimal-comma formats
are outside the supported numeric grammar.

## 4C: Validation, errors, and security

- Upload extension, MIME, signature, and 10 MiB checks remain in place.
- Empty, damaged/repaired, password-protected, zero-page, and textless PDFs are
  rejected. Image-only PDFs return a clear OCR-unsupported error; no OCR added.
- Uploads must contain a complete, unambiguous supported invoice before storage.
- Added bounds of 200 pages and 2,000,000 extracted text characters.
- Valid uploads preserve the exact successful response keys and UUID naming.
  Exclusive file creation prevents overwriting an existing PDF. Storage failures
  return a generic error and attempt cleanup only of a newly created partial file.
- Stored reads/listing use validated PDF basenames, reject path separators,
  absolute paths, control characters, Windows device names, alternate streams,
  and symlinks, and enforce resolved-path containment in the invoice directory.
- PDF and access errors do not expose internal exception details or local paths.
- Local processing validates all required fields and currency before calling
  creation. Invalid extraction cannot trigger creation or its retry path.
- Gemini receives the same validated extraction. A per-run source gate rejects
  creation/verification arguments that differ from a validated source read.
  Run state resets to prevent source reuse across tasks. Unverified invoice work
  cannot be reported as completed merely because the model stops calling tools.
- Gemini instructions explicitly treat PDF text as untrusted data, never agent
  instructions. Tool names, parameter schemas, model selection, and API retry
  behavior remain intact.
- Local/Gemini/Auto modes, vendor searches, database uniqueness, simulated
  transient failures, retry, verification, and history retain their interfaces.

## 4D: Final test results

| Suite | Passed | Failed | Skipped | Coverage |
| --- | ---: | ---: | ---: | --- |
| Unchanged Step 3 migration tests | 14 | 0 | 0 | Preservation, unique index definition/enforcement, repeatability, duplicate abort, rollback, incorrect indexes |
| Unchanged Step 3 compatibility tests | 21 | 0 | 0 | Four vendor workflows, search/create/verify, transaction recovery, genuine errors, four-way concurrency, API/history, retries |
| New extraction tests | 34 | 0 | 0 | Requested labels/layouts, all six date forms, DMY ambiguity, required fields, amounts/currencies, repeated/conflicting fields/pages, invalid/textless/encrypted PDFs |
| New upload/security/Local/Auto integration tests | 17 | 0 | 0 | UUID upload/read, validation failures, size, encryption, traversal, reserved names, symlink rejection, unreadable/missing files, date selection, invalid-source gates, Local retry/duplicates/history, Auto fallback |
| New offline Gemini tests | 3 | 0 | 0 | Real tool loop with mocked model responses: read/create/retry/verify, conflict rejection, unverified completion prevention, run isolation |
| **Total** | **89** | **0** | **0** | Includes subtests for label, date, currency, missing-field, and malicious-path variations |

Syntax validation passed for **15 backend Python files**. Final combined test
execution completed in **9.942 seconds**. Synthetic PDF fixtures and all inserted
test records were confined to disposable directories/databases. Symlink rejection
is exercised by mocking the filesystem symlink indicator, avoiding a requirement
for Windows symbolic-link creation privileges; traversal tests use real paths
and HTTP requests.

An intermediate stricter-parser run exposed three failing legacy Acme/Globex
regressions due to U+00B7 currency encoding. The narrow glyph compatibility path
fixed those failures; all original assertions now pass. No assertions were weakened.

The existing Starlette `httpx` TestClient deprecation warning remains. No dependency
was installed or upgraded to suppress it. No tests were skipped or left failing.

### Commands executed

From the project root, using the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -v
.\.venv\Scripts\python.exe -B -m unittest discover -s backend/tests -p test_invoice_uniqueness_compatibility.py -v
.\.venv\Scripts\python.exe -B backend/tests/runStep4Tests.py
```

The first command ran before edits (21 baseline tests), and again after the new
tests were introduced (64 tests). The second exposed the legacy encoding issue.
The final runner first passed 85 tests, then passed all 89 after the additional
currency-field and latest-date ambiguity cases. It includes syntax validation,
unchanged migration tests, all application tests, and protected-file checks.

The 14 pre-edit migration baseline tests were also run with this inline fixture
wrapper, without changing the migration test file or actual database:

```powershell
@'
import importlib.util, sqlite3, tempfile, unittest
from pathlib import Path
root=Path.cwd()
spec=importlib.util.spec_from_file_location('migrationTests',root/'backend/migrations/test_invoice_vendor_uniqueness.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
with tempfile.TemporaryDirectory(prefix='taskpilot-migration-baseline-') as directory:
 path=Path(directory)/'premigration.db'
 source=sqlite3.connect((root/'data/taskpilot.db').as_uri()+'?mode=ro',uri=True)
 target=sqlite3.connect(path)
 try:
  source.backup(target)
  target.execute('DROP INDEX ux_invoices_vendor_invoice_number_normalized')
  target.commit()
 finally:
  source.close(); target.close()
 module.sourcePath=path
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
 raise SystemExit(not result.wasSuccessful())
'@ | .\.venv\Scripts\python.exe -B -
```

### Database and file safety

The actual database was opened only through read-only SQLite connections for
backup and inspection. Application test connections were redirected to disposable
copies before importing FastAPI's startup `create_all()`.

- Database: `<project-root>\data\taskpilot.db`.
- Integrity: `ok` before and after.
- Counts before/after: **9/9 invoices, 6/6 normalized vendors, 4/4 history rows**.
- All existing invoice/history values and schema definitions compared equal.
- Index remains exactly:

```sql
CREATE UNIQUE INDEX ux_invoices_vendor_invoice_number_normalized
ON invoices (lower(trim(vendor)), invoice_number)
```

Actual database SHA-256, unchanged throughout Step 4:

```text
26346584c4dd531925fb7a8658bfef43323f7bf14bce120f95f73efacc53f2b9
```

All **20 files** in the pre-Step-4 safety manifest matched their original hashes
at completion. These include existing PDFs, migrations, the backup, database, and
frontend source/assets. The test runner additionally checks protected-file hashes
and full database contents on each invocation.

## Files changed in Step 4

Created:

- `backend/app/invoiceExtractionService.py`
- `backend/tests/testInvoiceExtraction.py`
- `backend/tests/runStep4Tests.py`
- `backend/tests/step4Report.md`

Modified:

- `backend/app/agent.py`: shared parsing, validation gate, conservative latest selection.
- `backend/app/tools.py`: secure PDF path handling, bounded validated reads, shared extraction metadata.
- `backend/app/invoiceUploadService.py`: readable/valid invoice checks, safe storage errors and exclusive creation.
- `backend/app/gemini_agent.py`: deterministic source gate and untrusted-content instructions.

No Step 4 changes were made to `main.py`, models, database configuration,
requirements, existing test modules, migration files, backups, frontend, actual
database, or existing PDFs. Earlier uncommitted work was preserved.

## Remaining limitations

- Text-layer PDFs only; image-only/scanned invoices need OCR and are rejected.
- English month names, explicit supported labels, and single-value field lines
  or next-line values are supported. Complex free-form/multicolumn layouts may
  require clarification instead of heuristic extraction.
- DMY is explicit; US month-first dates must use ISO or English month names.
- No multi-currency persistence or conversion without a future currency-aware
  schema. Bare/legacy glyph amounts use the documented legacy ledger convention.
- Float storage retains binary precision limitations; Decimal does not change
  the existing database type.
- SQLite/Python Unicode vendor-normalization differences from Step 3 remain.
- Symlink/path validation assumes the configured storage directory is not being
  concurrently rewritten by a hostile local filesystem actor.

All Step 4 completion criteria passed. **Step 5 was not started.**


> Step 7 follow-up: regression tests now generate a synthetic baseline and do not require local database records or uploaded PDFs. Historical fixture/environment limitations above describe the earlier run.
