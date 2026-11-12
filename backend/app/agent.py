import re
from datetime import date
from .invoiceExtractionService import extractInvoiceData, invoicePersistenceErrors

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
        """Use the same deterministic extraction supplied to Gemini tools."""
        return extractInvoiceData(content)



    def find_latest_vendor_invoice(self, invoices, vendor):
        """Find the latest invoice using vendor information inside PDFs."""

        requestedVendor = " ".join(vendor.casefold().split())
        matchingInvoices = []

        for invoice in invoices:
            invoiceResult = read_invoice(invoice["filename"])

            if not invoiceResult.get("success"):
                continue

            invoiceData = self.extract_invoice_data(
                invoiceResult.get("content", "")
            )

            extractedVendor = invoiceData.get("vendor")
            invoiceDate = invoiceData.get("invoice_date")

            if not extractedVendor:
                candidateVendors = [" ".join(value.casefold().split()) for value in invoiceData.get("vendor_candidates", [])]
                if any(requestedVendor == value or requestedVendor in value.split() for value in candidateVendors):
                    return None
                continue

            normalizedVendor = " ".join(
                extractedVendor.casefold().split()
            )

            # Match the complete vendor name or a distinct word.
            if (
                requestedVendor != normalizedVendor
                and requestedVendor not in normalizedVendor.split()
            ):
                continue

            # A matching invoice with an unknown/conflicting date prevents a
            # reliable latest-invoice decision; never silently pick an older one.
            if not invoiceDate:
                return None

            try:
                parsedDate = date.fromisoformat(invoiceDate)
            except (ValueError, TypeError):
                continue

            matchingInvoices.append(
                (parsedDate, invoiceData, invoice)
            )

        if not matchingInvoices:
            return None

        latestDate = max(item[0] for item in matchingInvoices)
        latestMatches = [item for item in matchingInvoices if item[0] == latestDate]
        invoiceSignatures = {
            tuple(item[1].get(fieldName) for fieldName in (
                "invoice_number", "invoice_date", "due_date", "amount_decimal", "currency"
            ))
            for item in latestMatches
        }
        if len(invoiceSignatures) > 1:
            return None
        # Identical repeated uploads are equivalent; filenames never rank dates.
        return latestMatches[0][2]


    def identify_requested_vendor(self, task: str, invoices):
        """Identify the requested vendor from available PDF contents."""

        normalizedTask = " ".join(task.casefold().split())
        availableVendors = {}

        for invoice in invoices:
            invoiceResult = read_invoice(invoice["filename"])

            if not invoiceResult.get("success"):
                continue

            invoiceData = self.extract_invoice_data(
                invoiceResult.get("content", "")
            )

            vendorName = invoiceData.get("vendor")

            if not vendorName:
                continue

            normalizedVendor = " ".join(
                vendorName.casefold().split()
            )

            availableVendors[normalizedVendor] = vendorName

        matchingVendors = []

        for normalizedVendor, vendorName in availableVendors.items():
            vendorWords = normalizedVendor.split()

            candidates = [normalizedVendor]

            if vendorWords:
                candidates.append(vendorWords[0])

            for candidate in candidates:
                pattern = (
                    r"(?<!\w)"
                    + re.escape(candidate)
                    + r"(?!\w)"
                )

                if re.search(pattern, normalizedTask):
                    matchingVendors.append(vendorName)
                    break

        if len(matchingVendors) == 1:
            return matchingVendors[0]

        return None


    def run(self, task: str):
        self.steps = []


        # STEP 1 - Discover available invoices.
        invoices = list_invoices()
        self.record_step("list_invoices", invoices)

        if not invoices:
            return {
                "task": task,
                "status": "failed",
                "reason": "No invoice files were found.",
                "steps": self.steps
            }

        # STEP 2 - Identify the vendor dynamically.
        requested_vendor = self.identify_requested_vendor(
            task, invoices
        )

        if requested_vendor is None:
            return {
                "task": task,
                "status": "needs_clarification",
                "message": (
                    "Could not uniquely identify a vendor "
                    "from the available invoices. "
                    "Please specify the company name."
                ),
                "steps": self.steps
            }

        self.record_step("identify_requested_vendor", {
            "vendor": requested_vendor
        })

        # STEP 3 - Select the latest vendor invoice.
        latest_invoice = self.find_latest_vendor_invoice(
            invoices, requested_vendor
        )

        if not latest_invoice:
            return {
                "task": task,
                "status": "failed",
                "reason": f"No invoice with a reliable, unambiguous latest date was found for {requested_vendor}.",
                "steps": self.steps
            }

        self.record_step("select_latest_vendor_invoice", {
            "vendor": requested_vendor,
            **latest_invoice
        })

        # STEP 4 — Read the PDF.
        invoice_result = read_invoice(latest_invoice["filename"])
        self.record_step("read_invoice", invoice_result)

        if not invoice_result.get("success"):
            return {
                "task": task,
                "status": "failed",
                "reason": "Unable to read invoice.",
                "steps": self.steps
            }

        # STEP 5 — Extract the invoice fields.
        invoice_data = self.extract_invoice_data(
            invoice_result["content"]
        )
        self.record_step("extract_invoice_data", invoice_data)

        validationErrors = invoicePersistenceErrors(invoice_data)
        if validationErrors:
            return {
                "task": task,
                "status": "needs_clarification",
                "reason": "Invoice extraction or validation failed.",
                "errors": validationErrors,
                "steps": self.steps
            }

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


        # STEP 6 — Check whether the invoice already exists.
        search_result = search_invoice(
            invoice_number=invoice_data["invoice_number"],
            vendor=requested_vendor
        )
        self.record_step("search_invoice", search_result)

        # STEP 7 — Create the invoice if it is missing.
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

        # STEP 8 — Verify the final state against the requested vendor.
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
