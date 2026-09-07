import unittest
from unittest.mock import patch

from app.services import journal_service


class _AccountQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return object()


class _Db:
    def query(self, *_args, **_kwargs):
        return _AccountQuery()


class DamagedReturnJournalTests(unittest.TestCase):
    @patch("app.services.journal_service._auto_journal")
    def test_damaged_sale_return_uses_quarantine_asset(self, auto_journal):
        journal_service.create_sale_return_journal(
            _Db(),
            date_val=None,
            number_ref="RS1",
            customer_name="Pelanggan",
            total_sales=100,
            total_tax=0,
            total_cogs=0,
            damaged_cogs=60,
            is_tax_included=True,
            user_id=1,
            branch_id=1,
        )
        entries = auto_journal.call_args.args[4]
        self.assertIn(
            {"code": journal_service.ACCOUNT_DAMAGED_INVENTORY, "debit": 60, "credit": 0},
            entries,
        )
        self.assertIn(
            {"code": journal_service.ACCOUNT_COGS, "debit": 0, "credit": 60},
            entries,
        )

    @patch("app.services.journal_service._auto_journal")
    def test_supplier_return_credits_quarantine_not_saleable_inventory(self, auto_journal):
        journal_service.create_purchase_return_journal(
            _Db(),
            date_val=None,
            number_ref="RP1",
            supplier_name="Supplier",
            total_inventory=100,
            total_tax=0,
            total_carrying=60,
            damaged_carrying=60,
            is_tax_included=True,
            user_id=1,
            branch_id=1,
        )
        entries = auto_journal.call_args.args[4]
        self.assertIn(
            {"code": journal_service.ACCOUNT_DAMAGED_INVENTORY, "debit": 0, "credit": 60},
            entries,
        )
        self.assertFalse(
            any(entry["code"] == journal_service.ACCOUNT_INVENTORY for entry in entries)
        )


if __name__ == "__main__":
    unittest.main()
