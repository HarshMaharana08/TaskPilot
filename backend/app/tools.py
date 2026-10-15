from datetime import date
from pathlib import Path

import pymupdf

from .database import SessionLocal
from .models import Invoice

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
        if file.is_file() and file.suffix.lower() == ".pdf":
            invoices.append({
                "filename": file.name,
                "path": str(file)
            })

    return invoices


def read_invoice(filename: str):
    """
    Read the text content of an invoice PDF.
    """

    file_path = INVOICE_DIR / filename

    if not file_path.exists():
        return {
            "success": False,
            "error": f"Invoice file not found: {filename}"
        }

    if file_path.suffix.lower() != ".pdf":
        return {
            "success": False,
            "error": "Only PDF files are supported."
        }

    try:
        document = pymupdf.open(file_path)

        text = ""

        for page in document:
            text += page.get_text()

        document.close()

        return {
            "success": True,
            "filename": filename,
            "content": text.strip()
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def search_invoice(invoice_number: str):
    """
    Search for an invoice in the internal company database.
    """

    db = SessionLocal()

    try:
        invoice = (
            db.query(Invoice)
            .filter(Invoice.invoice_number == invoice_number)
            .first()
        )

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

    try:
        # Prevent creating the same invoice twice.
        existing_invoice = (
            db.query(Invoice)
            .filter(Invoice.invoice_number == invoice_number)
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
        db.commit()
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

        # A commit may have succeeded even if a later operation failed.
        # Check using a fresh session before reporting failure.
        db.close()
        db = SessionLocal()

        try:
            existing_invoice = (
                db.query(Invoice)
                .filter(Invoice.invoice_number == invoice_number)
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

    result = search_invoice(invoice_number)

    if not result["found"]:
        return {
            "verified": False,
            "invoice_number": invoice_number,
            "reason": "Invoice was not found in the company system."
        }

    invoice = result["invoice"]

    mismatches = []

    if invoice["vendor"] != expected_vendor:
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
    Find the latest invoice PDF for a given vendor.
    The latest invoice is determined by the highest invoice number.
    """

    invoice_dir = Path(__file__).resolve().parents[2] / "data" / "invoices"

    # Match the first meaningful word of the vendor name.
    # Example:
    # "Acme Corporation" -> "acme"
    # "Globex Corporation" -> "globex"
    vendor_key = vendor.lower().split()[0]

    matching_files = []

    for file in invoice_dir.glob("*.pdf"):
        filename_normalized = file.stem.lower()

        if vendor_key in filename_normalized:
            matching_files.append(file)

    if not matching_files:
        return {
            "found": False,
            "vendor": vendor,
            "reason": "No matching invoice files found."
        }

    def invoice_number(file):
        import re

        numbers = re.findall(r"\d+", file.stem)

        if not numbers:
            return 0

        return int(numbers[-1])

    latest_file = max(
        matching_files,
        key=invoice_number
    )

    return {
        "found": True,
        "vendor": vendor,
        "filename": latest_file.name,
        "invoice_number": invoice_number(latest_file)
    }