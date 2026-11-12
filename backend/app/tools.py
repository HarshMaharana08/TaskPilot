from datetime import date
from pathlib import Path
import sqlite3

from .database import SessionLocal
from .models import Invoice
from .invoiceExtractionService import (
    InvoiceDocumentError, extractInvoiceData, extractPdfText, maxPdfBytes,
)
from sqlalchemy import func
from sqlalchemy.exc import DBAPIError, IntegrityError

CREATE_FAILURE_SIMULATION = True

# Project root: TaskPilot/
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Invoice folder: TaskPilot/data/invoices/
INVOICE_DIR = PROJECT_ROOT / "data" / "invoices"


def list_invoices():
    """
    Return all PDF invoices available to the agent.
    """

    if not INVOICE_DIR.exists():
        return []

    invoices = []

    for file in INVOICE_DIR.iterdir():
        try:
            safePath = resolveInvoicePath(file.name)
        except (ValueError, OSError):
            continue
        if safePath.is_file():
            invoices.append({
                "filename": file.name,
                "path": str(safePath)
            })

    return invoices


def resolveInvoicePath(filename: str) -> Path:
    """Allow a single PDF basename, contained in the configured directory."""
    if (
        not isinstance(filename, str)
        or not filename
        or filename != filename.strip()
        or filename.endswith((".", " "))
        or any(character in filename for character in '/\\:<>"|?*')
        or any(ord(character) < 32 for character in filename)
        or Path(filename).is_absolute()
        or Path(filename).suffix.lower() != ".pdf"
        or filename.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
    ):
        raise ValueError("A safe PDF filename without path components is required.")
    invoiceRoot = INVOICE_DIR.resolve()
    candidatePath = invoiceRoot / filename
    if candidatePath.is_symlink():
        raise ValueError("Linked invoice files are not supported.")
    resolvedPath = candidatePath.resolve()
    if resolvedPath.parent != invoiceRoot:
        raise ValueError("Invoice path is outside the invoice directory.")
    return resolvedPath


def read_invoice(filename: str):
    """
    Read the text content of an invoice PDF.
    """

    try:
        filePath = resolveInvoicePath(filename)
        if not filePath.is_file():
            return {"success": False, "error": "Invoice PDF was not found."}
        with filePath.open("rb") as invoiceFile:
            fileContent = invoiceFile.read(maxPdfBytes + 1)
        text, pageCount = extractPdfText(fileContent)
        return {
            "success": True,
            "filename": filename,
            "content": text,
            "extraction": extractInvoiceData(text),
            "page_count": pageCount,
            "content_is_untrusted": True,
        }
    except (ValueError, InvoiceDocumentError) as error:
        return {"success": False, "error": str(error)}
    except OSError:
        return {"success": False, "error": "Invoice PDF could not be accessed."}


def search_invoice(invoice_number: str, vendor: str | None = None):
    """
    Search for an invoice using its number and, when provided,
    its vendor.

    Vendor matching is case-insensitive and ignores leading
    and trailing whitespace.
    """

    db = SessionLocal()

    try:
        query = db.query(Invoice).filter(
            Invoice.invoice_number == invoice_number
        )

        if vendor is not None:
            normalizedVendor = vendor.strip().casefold()

            query = query.filter(
                func.lower(func.trim(Invoice.vendor)) == normalizedVendor
            )
        else:
            # Legacy callers may omit the vendor.
            # Never arbitrarily select a record when multiple vendors
            # share the same invoice number.
            matches = query.limit(2).all()

            if len(matches) > 1:
                return {
                    "found": False,
                    "invoice_number": invoice_number,
                    "ambiguous": True,
                    "reason": (
                        "Multiple vendors have this invoice number. "
                        "Specify the vendor."
                    )
                }

        invoice = query.first()

        if invoice is None:
            return {
                "found": False,
                "invoice_number": invoice_number
            }

        return {
            "found": True,
            "invoice": {
                "id": invoice.id,
                "vendor": invoice.vendor,
                "invoice_number": invoice.invoice_number,
                "invoice_date": str(invoice.invoice_date),
                "due_date": str(invoice.due_date),
                "amount": invoice.amount,
                "status": invoice.status
            }
        }

    finally:
        db.close()



def create_invoice(
    vendor: str,
    invoice_number: str,
    invoice_date: str,
    due_date: str,
    amount: float
):
    """
    Create an invoice safely, checking for an existing record
    and recovering when a database operation has an uncertain outcome.
    """

    global CREATE_FAILURE_SIMULATION

    if CREATE_FAILURE_SIMULATION:
        CREATE_FAILURE_SIMULATION = False
        return {
            "success": False,
            "error": "Simulated temporary system failure."
        }

    db = SessionLocal()
    commitAttempted = False
    commitSucceeded = False

    try:
        # Prevent creating the same invoice twice.
        existing_invoice = (
            db.query(Invoice)
            .filter(
                Invoice.invoice_number == invoice_number,
                func.lower(func.trim(Invoice.vendor)) == vendor.strip().casefold()
            )
            .first()
        )

        if existing_invoice:
            return {
                "success": True,
                "message": (
                    f"Invoice {invoice_number} already exists; "
                    "returning the existing record."
                ),
                "already_exists": True,
                "invoice": {
                    "id": existing_invoice.id,
                    "vendor": existing_invoice.vendor,
                    "invoice_number": existing_invoice.invoice_number,
                    "invoice_date": str(existing_invoice.invoice_date),
                    "due_date": str(existing_invoice.due_date),
                    "amount": existing_invoice.amount,
                    "status": existing_invoice.status
                }
            }

        new_invoice = Invoice(
            vendor=vendor,
            invoice_number=invoice_number,
            invoice_date=date.fromisoformat(invoice_date),
            due_date=date.fromisoformat(due_date),
            amount=amount,
            status="Pending"
        )

        db.add(new_invoice)
        commitAttempted = True
        db.commit()
        commitSucceeded = True
        db.refresh(new_invoice)

        return {
            "success": True,
            "message": f"Invoice {invoice_number} created successfully.",
            "invoice": {
                "id": new_invoice.id,
                "vendor": new_invoice.vendor,
                "invoice_number": new_invoice.invoice_number,
                "invoice_date": str(new_invoice.invoice_date),
                "due_date": str(new_invoice.due_date),
                "amount": new_invoice.amount,
                "status": new_invoice.status
            }
        }

    except Exception as e:
        db.rollback()

        # Recover only a conflict on this invoice key or an uncertain/successful
        # commit. A matching row must not conceal unrelated database failures.
        invoiceUniqueConflict = (
            isinstance(e, IntegrityError)
            and isinstance(e.orig, sqlite3.IntegrityError)
            and getattr(e.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE
            and str(e.orig) == (
                "UNIQUE constraint failed: index "
                "'ux_invoices_vendor_invoice_number_normalized'"
            )
        )
        uncertainCommit = (
            commitAttempted
            and isinstance(e, DBAPIError)
            and e.connection_invalidated
        )
        if not (invoiceUniqueConflict or commitSucceeded or uncertainCommit):
            return {
                "success": False,
                "error": str(e)
            }

        # Rollback above clears the failed transaction before this fresh lookup.
        db.close()
        db = SessionLocal()

        try:
            existing_invoice = (
                db.query(Invoice)
                .filter(
                    Invoice.invoice_number == invoice_number,
                    func.lower(func.trim(Invoice.vendor)) == vendor.strip().casefold()
                )
                .first()
            )

            if existing_invoice:
                return {
                    "success": True,
                    "message": (
                        f"Invoice {invoice_number} was found after "
                        "an uncertain creation outcome."
                    ),
                    "already_exists": True,
                    "recovered_after_error": True,
                    "invoice": {
                        "id": existing_invoice.id,
                        "vendor": existing_invoice.vendor,
                        "invoice_number": existing_invoice.invoice_number,
                        "invoice_date": str(existing_invoice.invoice_date),
                        "due_date": str(existing_invoice.due_date),
                        "amount": existing_invoice.amount,
                        "status": existing_invoice.status
                    }
                }

        except Exception:
            pass

        return {
            "success": False,
            "error": str(e)
        }

    finally:
        db.close()


def verify_invoice(
    invoice_number: str,
    expected_vendor: str,
    expected_invoice_date: str,
    expected_due_date: str,
    expected_amount: float
):
    """
    Verify that an invoice in the company database
    matches the expected values.
    """

    result = search_invoice(
        invoice_number=invoice_number,
        vendor=expected_vendor
    )

    if not result["found"]:
        return {
            "verified": False,
            "invoice_number": invoice_number,
            "reason": "Invoice was not found in the company system."
        }

    invoice = result["invoice"]

    mismatches = []


    normalizedExpectedVendor = " ".join(
        expected_vendor.casefold().split()
    )

    normalizedStoredVendor = " ".join(
        invoice["vendor"].casefold().split()
    )

    if normalizedStoredVendor != normalizedExpectedVendor:
        mismatches.append({
            "field": "vendor",
            "expected": expected_vendor,
            "actual": invoice["vendor"]
        })


    if invoice["invoice_date"] != expected_invoice_date:
        mismatches.append({
            "field": "invoice_date",
            "expected": expected_invoice_date,
            "actual": invoice["invoice_date"]
        })

    if invoice["due_date"] != expected_due_date:
        mismatches.append({
            "field": "due_date",
            "expected": expected_due_date,
            "actual": invoice["due_date"]
        })

    if float(invoice["amount"]) != float(expected_amount):
        mismatches.append({
            "field": "amount",
            "expected": expected_amount,
            "actual": invoice["amount"]
        })

    if mismatches:
        return {
            "verified": False,
            "invoice_number": invoice_number,
            "reason": "Invoice exists but some fields do not match.",
            "mismatches": mismatches
        }

    return {
        "verified": True,
        "invoice_number": invoice_number,
        "message": "Invoice verified successfully.",
        "invoice": invoice
    }


def find_latest_invoice(vendor: str):
    """
    Find the latest invoice for a vendor using PDF contents
    rather than filenames.
    """

    from .agent import TaskAgent

    agent = TaskAgent()
    invoices = list_invoices()

    latestInvoice = agent.find_latest_vendor_invoice(
        invoices,
        vendor
    )

    if latestInvoice is None:
        return {
            "found": False,
            "vendor": vendor,
            "reason": "No matching invoice files found."
        }

    return {
        "found": True,
        "vendor": vendor,
        "filename": latestInvoice["filename"],
        "path": latestInvoice["path"]
    }
