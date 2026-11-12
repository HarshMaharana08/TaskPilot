"""Deterministic text-layer extraction shared by Local and Gemini execution.

Numeric day/month/year dates use the explicit DMY (Indian) convention; ambiguous
values carry a warning. Pass numericDateOrder=None to reject ambiguous values.
Money is parsed with Decimal; float conversion is only for the existing API/DB.
The legacy ledger is INR-only: explicit other currencies can be extracted and
uploaded, but cannot safely be persisted in a schema without a currency column.
Bare amounts and legacy unreadable currency glyphs retain the existing ledger
convention, with warnings. No exchange rates or currency conversion are used.
"""

from datetime import date
from decimal import Decimal, InvalidOperation
import math
import re

import pymupdf


maxPdfBytes = 10 * 1024 * 1024
maxPdfPages = 200
maxTextCharacters = 2_000_000
fieldLabels = {
    "vendor": r"(?:billed\s+from|vendor|supplier|seller|from|company)(?:\s+name)?",
    "invoice_number": r"(?:invoice\s+(?:number|no\.?|\#|id|reference\s+number)|bill\s+(?:number|no\.?)|reference\s+number\s*\(invoice\))",
    "invoice_date": r"(?:invoice\s+date|date\s+of\s+invoice|bill\s+date)",
    "due_date": r"(?:due\s+date|payment\s+due(?:\s+date)?)",
    "amount": r"(?:total\s+amount|invoice\s+total|grand\s+total|amount\s+due|total\s+due|balance\s+due)",
}
labelPatterns = {
    fieldName: re.compile(r"^" + label + r"(?=\s|[:=#.\-]|$)\s*[:=.\-\u2013\u2014]?\s*(.*?)\s*$", re.I)
    for fieldName, label in fieldLabels.items()
}
monthNumbers = {name: number for number, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
)}


class InvoiceDocumentError(ValueError):
    def __init__(self, message, statusCode=400):
        super().__init__(message)
        self.statusCode = statusCode


def extractPdfText(fileContent: bytes) -> tuple[str, int]:
    if not fileContent:
        raise InvoiceDocumentError("PDF file is empty.")
    if len(fileContent) > maxPdfBytes:
        raise InvoiceDocumentError("PDF exceeds the 10 MiB size limit.", 413)
    if not fileContent.startswith(b"%PDF-"):
        raise InvoiceDocumentError("Invalid PDF file signature.")
    try:
        with pymupdf.open(stream=fileContent, filetype="pdf") as document:
            if not document.is_pdf or document.is_repaired:
                raise InvoiceDocumentError("PDF structure is invalid or damaged.")
            if document.needs_pass:
                raise InvoiceDocumentError("Password-protected PDFs are not supported.")
            pageCount = document.page_count
            if not pageCount:
                raise InvoiceDocumentError("PDF contains no pages.")
            if pageCount > maxPdfPages:
                raise InvoiceDocumentError("PDF exceeds the 200-page extraction limit.", 413)
            pageTexts = []
            textLength = 0
            for page in document:
                pageText = page.get_text(sort=True)
                textLength += len(pageText)
                if textLength > maxTextCharacters:
                    raise InvoiceDocumentError("PDF contains too much text for supported extraction.", 413)
                pageTexts.append(pageText)
            text = "\n\f\n".join(pageTexts).strip()
            if not text:
                raise InvoiceDocumentError("PDF has no readable text. Image-only PDFs require OCR, which is unsupported.", 422)
            return text, pageCount
    except InvoiceDocumentError:
        raise
    except Exception:
        raise InvoiceDocumentError("PDF could not be read; its structure may be invalid.") from None


def matchField(line):
    for fieldName, pattern in labelPatterns.items():
        match = pattern.fullmatch(line)
        if match:
            return fieldName, match.group(1)
    return None


def parseInvoiceDate(value, numericDateOrder="DMY"):
    numericMatch = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
    ambiguous = False
    if numericMatch:
        year, month, day = map(int, numericMatch.groups())
    else:
        numericMatch = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", value)
        if numericMatch:
            first, second, year = map(int, numericMatch.groups())
            ambiguous = first <= 12 and second <= 12 and first != second
            if numericDateOrder not in (None, "DMY"):
                raise ValueError("Only the documented DMY numeric date convention is supported.")
            if ambiguous and numericDateOrder is None:
                raise ValueError("Ambiguous numeric date; specify the DMY convention or use ISO/month names.")
            day, month = first, second
        else:
            monthMatch = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", value)
            if monthMatch:
                day, monthName, year = monthMatch.groups()
            else:
                monthMatch = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", value)
                if not monthMatch:
                    raise ValueError("Unrecognized date format.")
                monthName, day, year = monthMatch.groups()
            fullNames = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december")
            monthName = monthName.casefold()
            if monthName not in monthNumbers and monthName not in fullNames:
                raise ValueError("Invalid month name.")
            month = monthNumbers[monthName[:3]]
            day, year = int(day), int(year)
    return date(year, month, day).isoformat(), ambiguous


def parseInvoiceAmount(value):
    amountMatch = re.fullmatch(
        r"(?:(INR|USD|EUR|GBP|\u20b9|\$|\u20ac|\u00a3|\ufffd|\u00b7)\s*)?"
        r"([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.([0-9]{1,2}))?"
        r"(?:\s*(INR|USD|EUR|GBP))?", value, re.I,
    )
    if not amountMatch:
        raise ValueError("Invalid amount; use a positive amount with at most two decimal places and standard comma grouping.")
    prefix, whole, fraction, suffix = amountMatch.groups()
    currencyNames = {"\u20b9": "INR", "$": "USD", "\u20ac": "EUR", "\u00a3": "GBP", "\ufffd": None, "\u00b7": None}
    prefixCurrency = currencyNames.get(prefix, prefix.upper() if prefix else None)
    suffixCurrency = suffix.upper() if suffix else None
    if prefixCurrency and suffixCurrency and prefixCurrency != suffixCurrency:
        raise ValueError("Conflicting currency markers.")
    try:
        amount = Decimal(whole.replace(",", "") + "." + (fraction or "0"))
    except InvalidOperation:
        raise ValueError("Invalid monetary amount.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal("999999999999.99"):
        raise ValueError("Amount must be positive and within the supported range.")
    return amount, prefixCurrency or suffixCurrency, prefix in ("\ufffd", "\u00b7")


def extractInvoiceData(content: str, numericDateOrder="DMY") -> dict:
    candidates = {fieldName: [] for fieldName in fieldLabels}
    errors, warnings = [], []
    currencies = set()
    headingVendors = []
    for pageText in content.split("\f"):
        lines = [re.sub(r"[\t \u00a0]+", " ", line).strip() for line in pageText.splitlines() if line.strip()]
        if len(lines) > 1 and lines[1].casefold() == "invoice" and re.search(
            r"\b(?:corporation|corp\.?|company|limited|ltd\.?|llc|inc\.?|services|solutions)\b", lines[0], re.I
        ) and not re.match(r"(?:bill(?:ed)?\s+to|customer|ship\s+to|invoice)\b", lines[0], re.I):
            headingVendors.append(lines[0])
        ignoredSection, billingSection = False, False
        for lineIndex, line in enumerate(lines):
            if re.match(r"^(?:terms(?:\s+and\s+conditions)?|payment\s+instructions|bank\s+details|remit\s+to)(?:\s*[:.]|$)", line, re.I):
                ignoredSection = True
            if ignoredSection:
                continue
            currencyMatch = re.fullmatch(r"(?:invoice\s+)?currency(?=\s|[:=]|$)\s*[:=]?\s*(.*?)", line, re.I)
            if currencyMatch:
                currencyValue = currencyMatch.group(1)
                if not currencyValue and lineIndex + 1 < len(lines):
                    currencyValue = lines[lineIndex + 1]
                if re.fullmatch(r"[A-Za-z]{3}", currencyValue):
                    currencies.add(currencyValue.upper())
                else:
                    errors.append("Unsupported or ambiguous currency field; use a three-letter currency code.")
                continue
            if re.match(r"^(?:bill(?:ed)?\s+to|ship\s+to|customer)(?:\s+name)?(?:\s*[:=]|$)", line, re.I):
                billingSection = True
                continue
            fieldMatch = matchField(line)
            if not fieldMatch:
                continue
            fieldName, rawValue = fieldMatch
            if fieldName == "vendor":
                if billingSection and re.match(r"company\b", line, re.I):
                    continue
                billingSection = False
            if not rawValue and lineIndex + 1 < len(lines):
                nextLine = lines[lineIndex + 1]
                if not matchField(nextLine) and not re.match(r"^(?:bill(?:ed)?\s+to|customer|ship\s+to|terms|payment\s+instructions)\b", nextLine, re.I):
                    rawValue = nextLine
            candidates[fieldName].append(rawValue)
    if not candidates["vendor"]:
        candidates["vendor"] = headingVendors

    result = {fieldName: None for fieldName in fieldLabels}
    result.update(currency=None, amount_decimal=None)
    for fieldName, rawValues in candidates.items():
        normalizedValues = {}
        for rawValue in rawValues:
            try:
                if not rawValue:
                    raise ValueError("Missing field value.")
                if fieldName == "vendor":
                    if not re.search(r"[^\W\d_]", rawValue) or len(rawValue) > 200 or re.search(r"[\x00-\x1f]", rawValue):
                        raise ValueError("Invalid vendor name.")
                    normalizedValue = " ".join(rawValue.casefold().split())
                    parsedValue = rawValue
                elif fieldName == "invoice_number":
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_/-]{0,99}", rawValue):
                        raise ValueError("Invalid invoice identifier.")
                    normalizedValue = parsedValue = rawValue
                elif fieldName in ("invoice_date", "due_date"):
                    parsedValue, ambiguous = parseInvoiceDate(rawValue, numericDateOrder)
                    normalizedValue = parsedValue
                    if ambiguous:
                        warnings.append(f"{fieldName}: ambiguous numeric date interpreted using documented day-first DMY convention.")
                else:
                    parsedValue, currency, unreadableCurrency = parseInvoiceAmount(rawValue)
                    normalizedValue = parsedValue
                    if currency:
                        currencies.add(currency)
                    if unreadableCurrency:
                        warnings.append("Legacy PDF currency glyph is unreadable; currency is unspecified.")
                normalizedValues[normalizedValue] = parsedValue
            except (ValueError, OverflowError) as error:
                errors.append(f"{fieldName}: {error}")
        if not rawValues:
            errors.append(f"Missing required field: {fieldName}.")
        elif len(normalizedValues) > 1:
            errors.append(f"Conflicting values for {fieldName}.")
        elif len(normalizedValues) == 1:
            result[fieldName] = next(iter(normalizedValues.values()))
    if len(currencies) > 1:
        errors.append("Conflicting currencies; amounts in different currencies are not equivalent.")
    elif currencies:
        result["currency"] = next(iter(currencies))
    if result["amount"] is not None:
        result["amount_decimal"] = format(result["amount"], ".2f")
        result["amount"] = float(result["amount"])
        if not currencies:
            warnings.append("Currency unspecified; persistence uses the legacy INR ledger convention.")
    if result["invoice_date"] and result["due_date"] and result["due_date"] < result["invoice_date"]:
        errors.append("Due date cannot precede invoice date.")
    result.update(
        valid=not errors, errors=list(dict.fromkeys(errors)), warnings=list(dict.fromkeys(warnings)),
        vendor_candidates=list(dict.fromkeys(value for value in candidates["vendor"] if value)),
    )
    return result


def invoicePersistenceErrors(invoiceData):
    errors = list(invoiceData.get("errors", []))
    if not invoiceData.get("valid"):
        return errors or ["Invoice extraction is incomplete or ambiguous."]
    if invoiceData.get("currency") not in (None, "INR"):
        errors.append("Only INR invoices can be persisted safely in the existing currency-less ledger; no currency conversion is supported.")
    amount = invoiceData.get("amount")
    if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
        errors.append("Invoice total amount is invalid.")
    return errors
