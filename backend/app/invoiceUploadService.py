
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile

from .tools import INVOICE_DIR
from .invoiceExtractionService import (
    InvoiceDocumentError, extractInvoiceData, extractPdfText, maxPdfBytes,
)


MAX_FILE_SIZE = maxPdfBytes


async def saveInvoicePdf(uploadedFile: UploadFile):
    """Validate and store an uploaded invoice PDF."""

    if uploadedFile.content_type != "application/pdf":
        raise HTTPException(
            status_code=415,
            detail="Only PDF files are supported."
        )

    originalFileName = Path((uploadedFile.filename or "").replace("\\", "/")).name

    if not originalFileName.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=415,
            detail="File must have a .pdf extension."
        )

    fileContent = await uploadedFile.read(MAX_FILE_SIZE + 1)

    if len(fileContent) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="PDF exceeds the 10 MB size limit."
        )

    try:
        text, pageCount = extractPdfText(fileContent)
    except InvoiceDocumentError as error:
        raise HTTPException(status_code=error.statusCode, detail=str(error)) from None
    extraction = extractInvoiceData(text)
    if not extraction["valid"]:
        raise HTTPException(status_code=422, detail={
            "message": "PDF does not contain a complete, unambiguous supported invoice.",
            "errors": extraction["errors"],
        })

    storedFileName = f"{uuid4().hex}.pdf"
    createdFile = False
    try:
        INVOICE_DIR.mkdir(parents=True, exist_ok=True)
        destinationPath = INVOICE_DIR / storedFileName
        with destinationPath.open("xb") as destinationFile:
            createdFile = True
            destinationFile.write(fileContent)
    except OSError:
        if createdFile:
            try:
                destinationPath.unlink(missing_ok=True)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="Invoice PDF could not be stored.") from None

    return {
        "success": True,
        "original_filename": originalFileName,
        "filename": storedFileName,
        "size_bytes": len(fileContent),
        "page_count": pageCount,
        "message": "Invoice PDF uploaded successfully."
    }
