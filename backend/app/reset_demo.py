from .database import SessionLocal
from .models import Invoice


db = SessionLocal()

invoice = (
    db.query(Invoice)
    .filter(Invoice.invoice_number == "INV-105")
    .first()
)

if invoice:
    db.delete(invoice)
    db.commit()
    print("INV-105 removed from database.")
else:
    print("INV-105 was not found.")

db.close()