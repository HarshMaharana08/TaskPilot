from pathlib import Path
import fitz


INVOICE_DIR = Path(__file__).parent / "invoices"
INVOICE_DIR.mkdir(parents=True, exist_ok=True)


invoices = [
    {
        "filename": "acme_invoice_103.pdf",
        "vendor": "ACME CORPORATION",
        "number": "INV-103",
        "invoice_date": "2026-08-01",
        "due_date": "2026-09-01",
        "amount": "₹65,000",
    },
    {
        "filename": "acme_invoice_104.pdf",
        "vendor": "ACME CORPORATION",
        "number": "INV-104",
        "invoice_date": "2026-09-01",
        "due_date": "2026-10-01",
        "amount": "₹72,500",
    },
    {
        "filename": "acme_invoice_105.pdf",
        "vendor": "ACME CORPORATION",
        "number": "INV-105",
        "invoice_date": "2026-10-01",
        "due_date": "2026-11-01",
        "amount": "₹84,500",
    },
    {
        "filename": "globex_invoice_201.pdf",
        "vendor": "GLOBEX CORPORATION",
        "number": "INV-201",
        "invoice_date": "2026-09-15",
        "due_date": "2026-10-15",
        "amount": "₹91,000",
    },
    {
        "filename": "globex_invoice_202.pdf",
        "vendor": "GLOBEX CORPORATION",
        "number": "INV-202",
        "invoice_date": "2026-10-01",
        "due_date": "2026-11-01",
        "amount": "₹98,000",
    },
]


for invoice in invoices:
    pdf_path = INVOICE_DIR / invoice["filename"]

    document = fitz.open()
    page = document.new_page()

    text = f"""
{invoice["vendor"]}

INVOICE

Invoice Number: {invoice["number"]}
Invoice Date: {invoice["invoice_date"]}
Due Date: {invoice["due_date"]}

Description:
Software Services

Total Amount: {invoice["amount"]}
"""

    page.insert_text(
        (72, 100),
        text,
        fontsize=14,
    )

    document.save(pdf_path)
    document.close()

    print(f"Created: {pdf_path}")


print("\nAll invoices created successfully.")