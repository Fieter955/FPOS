"""Audit persediaan read-only untuk database SQLite FPOS.

Pemakaian:
    python inventory_audit.py path/ke/salinan.db

Perintah ini tidak pernah menulis database. Exit code 1 berarti ada temuan yang
perlu ditinjau; exit code 2 berarti database tidak dapat diaudit.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


TOLERANSI_QTY = 0.0001
TOLERANSI_NILAI = 1.0


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql).fetchall()]


def audit_inventory(conn: sqlite3.Connection) -> dict[str, Any]:
    """Kembalikan ringkasan dan temuan tanpa mengubah koneksi/database."""
    conn.row_factory = sqlite3.Row
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = _rows(conn, "PRAGMA foreign_key_check")

    checks = {
        "fifo_quantity_drift": _rows(conn, """
            WITH keys AS (
                SELECT warehouse_id, item_id FROM warehouse_stocks
                UNION
                SELECT warehouse_id, item_id FROM stock_batches
            ), stock AS (
                SELECT warehouse_id, item_id, SUM(stock) qty
                FROM warehouse_stocks GROUP BY warehouse_id, item_id
            ), fifo AS (
                SELECT warehouse_id, item_id, SUM(qty_remaining) qty
                FROM stock_batches GROUP BY warehouse_id, item_id
            )
            SELECT k.warehouse_id, k.item_id,
                   ROUND(COALESCE(s.qty, 0), 4) warehouse_qty,
                   ROUND(COALESCE(f.qty, 0), 4) fifo_qty,
                   ROUND(COALESCE(s.qty, 0) - COALESCE(f.qty, 0), 4) gap
            FROM keys k
            LEFT JOIN stock s USING (warehouse_id, item_id)
            LEFT JOIN fifo f USING (warehouse_id, item_id)
            WHERE ABS(COALESCE(s.qty, 0) - COALESCE(f.qty, 0)) > 0.0001
            ORDER BY ABS(COALESCE(s.qty, 0) - COALESCE(f.qty, 0)) DESC
        """),
        "fifo_invalid_batches": _rows(conn, """
            SELECT id, warehouse_id, item_id, qty_received, qty_remaining, unit_cost
            FROM stock_batches
            WHERE qty_received < -0.0001 OR qty_remaining < -0.0001
               OR qty_remaining > qty_received + 0.0001 OR unit_cost < 0
            ORDER BY id
        """),
        "negative_warehouse_stock": _rows(conn, """
            SELECT id, warehouse_id, item_id, stock
            FROM warehouse_stocks WHERE stock < -0.0001 ORDER BY stock
        """),
        "invalid_movements": _rows(conn, """
            SELECT id, date, branch_id, warehouse_id, item_id, type, qty,
                   qty_before, qty_after, reference
            FROM stock_movements
            WHERE qty IS NULL OR qty <= 0
            ORDER BY id
        """),
        "movement_math_drift": _rows(conn, """
            SELECT id, date, branch_id, warehouse_id, item_id, type, qty,
                   qty_before, qty_after, reference
            FROM stock_movements
            WHERE (type IN ('in','item_in','opening_stock','opname_in','return','reversal_in')
                   AND ABS(qty_after - qty_before - qty) > 0.0001)
               OR (type IN ('out','item_out','opname_out','sale','reversal_out')
                   AND ABS(qty_after - qty_before + qty) > 0.0001)
            ORDER BY id
        """),
        "zero_cost_inventory_documents": _rows(conn, """
            SELECT d.id, d.number, d.type, d.date, d.branch_id, d.warehouse_id,
                   ROUND(SUM(ABS(l.qty_delta)), 4) quantity,
                   ROUND(SUM(l.total_cost), 2) total_cost, d.journal_id
            FROM inventory_documents d
            JOIN inventory_document_lines l ON l.document_id = d.id
            WHERE d.status = 'posted'
            GROUP BY d.id
            HAVING SUM(ABS(l.qty_delta)) > 0.0001 AND ABS(SUM(l.total_cost)) < 0.01
            ORDER BY d.id
        """),
        "posted_documents_without_journal": _rows(conn, """
            SELECT d.id, d.number, d.type, d.date, d.branch_id,
                   ROUND(SUM(l.total_cost), 2) total_cost
            FROM inventory_documents d
            JOIN inventory_document_lines l ON l.document_id = d.id
            WHERE d.status = 'posted' AND d.journal_id IS NULL
            GROUP BY d.id
            HAVING ABS(SUM(l.total_cost)) >= 0.01
            ORDER BY d.id
        """),
        "cancelled_documents_without_reversal": _rows(conn, """
            SELECT id, number, type, date, branch_id, journal_id, reversal_journal_id
            FROM inventory_documents
            WHERE status = 'cancelled' AND journal_id IS NOT NULL
              AND reversal_journal_id IS NULL
            ORDER BY id
        """),
        "central_stock_cache_drift": _rows(conn, """
            WITH central AS (
                SELECT ws.item_id, SUM(ws.stock) qty
                FROM warehouse_stocks ws
                JOIN warehouses w ON w.id = ws.warehouse_id
                WHERE w.branch_id = 1
                GROUP BY ws.item_id
            )
            SELECT i.id item_id, i.code, i.name,
                   ROUND(COALESCE(i.stock, 0), 4) cached_qty,
                   ROUND(COALESCE(c.qty, 0), 4) warehouse_qty,
                   ROUND(COALESCE(i.stock, 0) - COALESCE(c.qty, 0), 4) gap
            FROM items i LEFT JOIN central c ON c.item_id = i.id
            WHERE ABS(COALESCE(i.stock, 0) - COALESCE(c.qty, 0)) > 0.0001
            ORDER BY ABS(COALESCE(i.stock, 0) - COALESCE(c.qty, 0)) DESC
        """),
        "inventory_value_drift": _rows(conn, """
            WITH inventory_account AS (
                SELECT id FROM accounts WHERE code = '1-1400'
            ), gl AS (
                SELECT j.branch_id,
                       SUM(CASE WHEN l.debit_account_id IN inventory_account THEN l.amount ELSE 0 END)
                       - SUM(CASE WHEN l.credit_account_id IN inventory_account THEN l.amount ELSE 0 END) value
                FROM journals j JOIN journal_entry_lines l ON l.journal_id = j.id
                GROUP BY j.branch_id
            ), fifo AS (
                SELECT w.branch_id, SUM(sb.qty_remaining * sb.unit_cost) value
                FROM stock_batches sb JOIN warehouses w ON w.id = sb.warehouse_id
                GROUP BY w.branch_id
            )
            SELECT b.id branch_id, b.name branch,
                   ROUND(COALESCE(gl.value, 0), 2) gl_value,
                   ROUND(COALESCE(fifo.value, 0), 2) fifo_value,
                   ROUND(COALESCE(gl.value, 0) - COALESCE(fifo.value, 0), 2) gap
            FROM branches b
            LEFT JOIN gl ON gl.branch_id = b.id
            LEFT JOIN fifo ON fifo.branch_id = b.id
            WHERE ABS(COALESCE(gl.value, 0) - COALESCE(fifo.value, 0)) >= 1
            ORDER BY ABS(COALESCE(gl.value, 0) - COALESCE(fifo.value, 0)) DESC
        """),
    }
    return {
        "database_integrity": integrity,
        "foreign_key_violations": foreign_keys,
        "checks": checks,
        "finding_count": len(foreign_keys) + sum(len(rows) for rows in checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit read-only integritas persediaan FPOS")
    parser.add_argument("database", type=Path, help="Path database salinan SQLite")
    parser.add_argument("--json", action="store_true", help="Cetak hasil lengkap sebagai JSON")
    args = parser.parse_args()
    database = args.database.resolve()
    if not database.is_file():
        parser.error(f"Database tidak ditemukan: {database}")

    try:
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as conn:
            report = audit_inventory(conn)
    except (sqlite3.DatabaseError, sqlite3.OperationalError) as exc:
        print(f"Audit gagal: {exc}")
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Integritas SQLite: {report['database_integrity']}")
        print(f"Pelanggaran foreign key: {len(report['foreign_key_violations'])}")
        for name, rows in report["checks"].items():
            print(f"{name}: {len(rows)}")
        print(f"Total temuan: {report['finding_count']}")
    return 1 if report["database_integrity"] != "ok" or report["finding_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
