from datetime import date

from .database import SessionLocal, engine, Base
from .models import Invoice


Base.metadata.create_all(bind=engine)


def seed_database():
    db = SessionLocal()

    # Avoid inserting duplicate invoices
    existing_invoice = db.query(Invoice).first()

    if existing_invoice:
        print("Database already contains invoices.")
        db.close()
        return

    invoices = [
        Invoice(
            vendor="Acme Corporation",
            invoice_number="INV-103",
            invoice_date=date(2026, 8, 1),
            due_date=date(2026, 9, 1),
            amount=65000,
            status="Paid"
        ),
        Invoice(
            vendor="Acme Corporation",
            invoice_number="INV-104",
            invoice_date=date(2026, 9, 1),
            due_date=date(2026, 10, 1),
            amount=72500,
            status="Pending"
        ),
        Invoice(
            vendor="Globex Corporation",
            invoice_number="INV-201",
            invoice_date=date(2026, 9, 15),
            due_date=date(2026, 10, 15),
            amount=91000,
            status="Pending"
        )
    ]

    db.add_all(invoices)
    db.commit()
    db.close()

    print("Database seeded successfully.")


if __name__ == "__main__":
    seed_database()