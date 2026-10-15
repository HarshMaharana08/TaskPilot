import re
from datetime import date

from .tools import (
    list_invoices,
    read_invoice,
    search_invoice,
    create_invoice,
    verify_invoice,
)


class TaskAgent:
    """Deterministic execution engine for TaskPilot."""

    def __init__(self):
        self.steps = []

    def record_step(self, action, result):
        self.steps.append({
            "action": action,
            "result": result
        })

    def extract_invoice_data(self, content: str):
        invoice_number = re.search(
            r"Invoice Number:\s*(\S+)", content
        )
        invoice_date = re.search(
            r"Invoice Date:\s*(\d{4}-\d{2}-\d{2})", content
        )
        due_date = re.search(
            r"Due Date:\s*(\d{4}-\d{2}-\d{2})", content
        )
        amount = re.search(
            r"Total Amount:\s*[^\d]*([\d,]+(?:\.\d+)?)",
            content
        )

        return {
            "invoice_number": (
                invoice_number.group(1) if invoice_number else None
            ),
            "invoice_date": (
                invoice_date.group(1) if invoice_date else None
            ),
            "due_date": due_date.group(1) if due_date else None,
            "amount": (
                float(amount.group(1).replace(",", ""))
                if amount else None
            ),
        }

    def find_latest_vendor_invoice(self, invoices, vendor):
        """Select the vendor invoice with the latest invoice date."""

        vendor_key = vendor.lower()
        matching = []

        for invoice in invoices:
            if vendor_key not in invoice["filename"].lower():
                continue

            result = read_invoice(invoice["filename"])

            if not result.get("success"):
                continue

            invoice_data = self.extract_invoice_data(
                result.get("content", "")
            )

            invoice_date = invoice_data.get("invoice_date")
            if not invoice_date:
                continue

            try:
                parsed_date = date.fromisoformat(invoice_date)
            except ValueError:
                continue

            matching.append((parsed_date, invoice))

        if not matching:
            return None

        return max(matching, key=lambda item: item[0])[1]

    def run(self, task: str):
        self.steps = []

        # Identify the requested vendor from the task.
        vendor_patterns = {
            "acme": "Acme Corporation",
            "globex": "Globex Corporation",
        }

        task_lower = task.lower()

        requested_vendor = next(
            (
                canonical_name
                for keyword, canonical_name in vendor_patterns.items()
                if keyword in task_lower
            ),
            None
        )

        if requested_vendor is None:
            return {
                "task": task,
                "status": "needs_clarification",
                "message": (
                    "Please specify a supported vendor: Acme or Globex."
                ),
                "steps": self.steps
            }

        # STEP 1 — Discover invoice files.
        invoices = list_invoices()
        self.record_step("list_invoices", invoices)

        if not invoices:
            return {
                "task": task,
                "status": "failed",
                "reason": "No invoice files were found.",
                "steps": self.steps
            }

        # STEP 2 — Select the latest invoice for the requested vendor.
        vendor_key = requested_vendor.split()[0].lower()
        latest_invoice = self.find_latest_vendor_invoice(
            invoices, vendor_key
        )

        if not latest_invoice:
            return {
                "task": task,
                "status": "failed",
                "reason": f"No invoice was found for {requested_vendor}.",
                "steps": self.steps
            }

        self.record_step("select_latest_vendor_invoice", {
            "vendor": requested_vendor,
            **latest_invoice
        })

        # STEP 3 — Read the PDF.
        invoice_result = read_invoice(latest_invoice["filename"])
        self.record_step("read_invoice", invoice_result)

        if not invoice_result.get("success"):
            return {
                "task": task,
                "status": "failed",
                "reason": "Unable to read invoice.",
                "steps": self.steps
            }

        # STEP 4 — Extract the invoice fields.
        invoice_data = self.extract_invoice_data(
            invoice_result["content"]
        )
        self.record_step("extract_invoice_data", invoice_data)

        required_fields = [
            "invoice_number", "invoice_date", "due_date", "amount"
        ]

        if any(invoice_data[field] is None for field in required_fields):
            return {
                "task": task,
                "status": "failed",
                "reason": "Could not extract all required invoice fields.",
                "steps": self.steps
            }

        # Validate extracted invoice data before writing to the database.
        try:
            parsed_invoice_date = date.fromisoformat(
                invoice_data["invoice_date"]
            )
            parsed_due_date = date.fromisoformat(
                invoice_data["due_date"]
            )
        except (ValueError, TypeError):
            return {
                "task": task,
                "status": "failed",
                "reason": "Invoice contains an invalid date.",
                "steps": self.steps
            }

        if not invoice_data["invoice_number"].strip():
            return {
                "task": task,
                "status": "failed",
                "reason": "Invoice number cannot be empty.",
                "steps": self.steps
            }

        if invoice_data["amount"] <= 0:
            return {
                "task": task,
                "status": "failed",
                "reason": "Invoice amount must be greater than zero.",
                "steps": self.steps
            }

        if parsed_due_date < parsed_invoice_date:
            return {
                "task": task,
                "status": "failed",
                "reason": "Invoice due date cannot precede its invoice date.",
                "steps": self.steps
            }

        self.record_step("validate_invoice_data", {
            "valid": True,
            "invoice_number": invoice_data["invoice_number"],
            "invoice_date": invoice_data["invoice_date"],
            "due_date": invoice_data["due_date"],
            "amount": invoice_data["amount"]
        })


        # STEP 5 — Check whether the invoice already exists.
        search_result = search_invoice(invoice_data["invoice_number"])
        self.record_step("search_invoice", search_result)

        # STEP 6 — Create the invoice if it is missing.
        if not search_result["found"]:
            create_result = create_invoice(
                vendor=requested_vendor,
                invoice_number=invoice_data["invoice_number"],
                invoice_date=invoice_data["invoice_date"],
                due_date=invoice_data["due_date"],
                amount=invoice_data["amount"]
            )
            self.record_step("create_invoice_attempt_1", create_result)

            if not create_result["success"]:
                self.record_step("retry_decision", {
                    "reason": "Initial creation failed; retrying once."
                })

                create_result = create_invoice(
                    vendor=requested_vendor,
                    invoice_number=invoice_data["invoice_number"],
                    invoice_date=invoice_data["invoice_date"],
                    due_date=invoice_data["due_date"],
                    amount=invoice_data["amount"]
                )
                self.record_step("create_invoice_attempt_2", create_result)

            if not create_result["success"]:
                return {
                    "task": task,
                    "status": "failed",
                    "reason": "Invoice creation failed after retry.",
                    "steps": self.steps
                }
        else:
            self.record_step("create_invoice_skipped", {
                "reason": "Invoice already exists."
            })

        # STEP 7 — Verify the final state against the requested vendor.
        verification = verify_invoice(
            invoice_number=invoice_data["invoice_number"],
            expected_vendor=requested_vendor,
            expected_invoice_date=invoice_data["invoice_date"],
            expected_due_date=invoice_data["due_date"],
            expected_amount=invoice_data["amount"]
        )
        self.record_step("verify_invoice", verification)

        if not verification.get("verified"):
            return {
                "task": task,
                "status": "failed",
                "reason": "Invoice could not be verified.",
                "steps": self.steps
            }

        return {
            "task": task,
            "status": "completed",
            "message": (
                f"Latest {requested_vendor} invoice "
                "was processed and verified."
            ),
            "invoice": {
                **invoice_data,
                "vendor": requested_vendor
            },
            "steps": self.steps
        }
