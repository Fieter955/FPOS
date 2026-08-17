import unittest
from types import SimpleNamespace

from app.services.payment_change import calculate_change


class SalesChangeTests(unittest.TestCase):
    def test_legacy_overpayment_still_calculates_change(self):
        self.assertEqual(calculate_change(100_000, 75_000), 25_000)

    def test_gross_cash_is_separate_from_net_paid(self):
        payments = [SimpleNamespace(metode="cash", jumlah=75_000)]

        self.assertEqual(
            calculate_change(
                paid=75_000,
                total=75_000,
                payments=payments,
                cash_received=100_000,
            ),
            25_000,
        )

    def test_mixed_payment_returns_change_from_cash_only(self):
        payments = [
            SimpleNamespace(metode="cash", jumlah=70_000),
            SimpleNamespace(metode="debit", jumlah=50_000),
        ]

        self.assertEqual(
            calculate_change(
                paid=120_000,
                total=120_000,
                payments=payments,
                cash_received=100_000,
            ),
            30_000,
        )

    def test_simple_pos_can_send_net_paid_and_gross_cash(self):
        self.assertEqual(
            calculate_change(
                paid=75_000,
                total=75_000,
                cash_received=100_000,
                payment_method="cash",
            ),
            25_000,
        )

    def test_cash_received_cannot_be_less_than_applied_cash(self):
        payments = [SimpleNamespace(metode="cash", jumlah=75_000)]

        with self.assertRaises(ValueError):
            calculate_change(
                paid=75_000,
                total=75_000,
                payments=payments,
                cash_received=50_000,
            )


if __name__ == "__main__":
    unittest.main()
