import unittest

from fastapi import HTTPException

from app.routes.trade_in import (
    trade_in_customer_balance_delta,
    validate_trade_in_tenders,
)


class TradeInTenderTests(unittest.TestCase):
    def test_accepts_mixed_cash_bank_and_customer_balance(self):
        self.assertEqual(
            validate_trade_in_tenders(10_000, 15_000, 25_000, 30_000),
            (10_000.0, 15_000.0, 25_000.0),
        )

    def test_rejects_customer_balance_over_available_amount(self):
        with self.assertRaises(HTTPException) as caught:
            validate_trade_in_tenders(0, 0, 30_000, 25_000)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("tidak cukup", caught.exception.detail)

    def test_rejects_negative_tender(self):
        with self.assertRaises(HTTPException) as caught:
            validate_trade_in_tenders(-1, 0, 0, 0)
        self.assertEqual(caught.exception.status_code, 400)

    def test_customer_balance_is_reduced_by_deposit_tender(self):
        self.assertEqual(
            trade_in_customer_balance_delta(50_000, 100_000, 10_000, 15_000, 25_000),
            -25_000,
        )

    def test_customer_refund_is_added_to_balance(self):
        self.assertEqual(
            trade_in_customer_balance_delta(100_000, 70_000, 0, 0, 0),
            30_000,
        )


if __name__ == "__main__":
    unittest.main()
