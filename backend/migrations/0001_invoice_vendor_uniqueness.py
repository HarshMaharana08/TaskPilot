"""Explicit, standalone migration; importing this module performs no database I/O.

Run with: python backend/migrations/0001_invoice_vendor_uniqueness.py DATABASE_PATH
Back up the target and pause application writes before a real deployment.
No migration ledger is added: the versioned index definition is the applied-state
check. Existing definitions must match this migration (case/whitespace aside);
equivalent but differently quoted SQL is conservatively rejected for review.
"""

import argparse
from pathlib import Path
import re
import sqlite3


migrationId = "0001_invoice_vendor_uniqueness"
indexName = "ux_invoices_vendor_invoice_number_normalized"
indexSql = (
    f"CREATE UNIQUE INDEX {indexName} "
    "ON invoices (lower(trim(vendor)), invoice_number)"
)


def canonicalizeSql(sql: str) -> str:
    return re.sub(r"\s+", "", sql).rstrip(";").casefold()


def verifyIndexDefinition(connection: sqlite3.Connection) -> bool:
    indexRow = connection.execute(
        "SELECT type, tbl_name, sql FROM sqlite_master WHERE name = ? COLLATE NOCASE",
        (indexName,),
    ).fetchone()
    if indexRow is None:
        return False

    if (
        indexRow[0] != "index"
        or indexRow[1] != "invoices"
        or not indexRow[2]
        or canonicalizeSql(indexRow[2]) != canonicalizeSql(indexSql)
    ):
        raise ValueError(f"Existing {indexName} has an unexpected definition.")

    indexRows = connection.execute("PRAGMA index_list('invoices')").fetchall()
    matchingRows = [row for row in indexRows if row[1].casefold() == indexName]
    keyRows = [
        row for row in connection.execute(f"PRAGMA index_xinfo('{indexName}')")
        if row[5] == 1
    ]
    if (
        len(matchingRows) != 1
        or matchingRows[0][2] != 1
        or matchingRows[0][4] != 0
        or [(row[1], row[2], row[3], row[4]) for row in keyRows]
        != [(-2, None, 0, "BINARY"), (2, "invoice_number", 0, "BINARY")]
    ):
        raise ValueError(f"Existing {indexName} has unexpected index metadata.")
    return True


def migrateDatabase(databasePath: str | Path) -> str:
    """Apply only to an explicitly supplied, existing SQLite file.

    BEGIN IMMEDIATE prevents another writer inserting duplicates between the
    preflight check and index creation. All failures roll back the transaction.
    """
    resolvedPath = Path(databasePath).resolve(strict=True)
    connection = sqlite3.connect(
        resolvedPath.as_uri() + "?mode=rw", uri=True, isolation_level=None,
    )
    try:
        connection.execute("BEGIN IMMEDIATE")
        duplicateRows = connection.execute(
            "SELECT lower(trim(vendor)), invoice_number, COUNT(*) "
            "FROM invoices GROUP BY lower(trim(vendor)), invoice_number "
            "HAVING COUNT(*) > 1"
        ).fetchall()
        if duplicateRows:
            raise ValueError(
                f"Normalized duplicate invoice keys found; migration aborted: {duplicateRows!r}"
            )

        alreadyApplied = verifyIndexDefinition(connection)
        if not alreadyApplied:
            connection.execute(indexSql)
            if not verifyIndexDefinition(connection):
                raise ValueError("Index creation could not be verified.")
        connection.commit()
        return "already applied" if alreadyApplied else "applied"
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    argumentParser = argparse.ArgumentParser(description=__doc__)
    argumentParser.add_argument("databasePath", type=Path, help="Existing SQLite database file")
    arguments = argumentParser.parse_args()
    try:
        result = migrateDatabase(arguments.databasePath)
    except (OSError, sqlite3.Error, ValueError) as error:
        argumentParser.exit(1, f"{migrationId}: failed: {error}\n")
    print(f"{migrationId}: {result}")


if __name__ == "__main__":
    main()
