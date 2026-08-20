import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FRONTEND_DIST = ROOT / "frontend-dist"


class ReceiptFrontendContractTests(unittest.TestCase):
    def read(self, relative):
        return (FRONTEND / relative).read_text(encoding="utf-8")

    def test_pos_requests_receipt_in_same_sale_transaction(self):
        pos = self.read("pos.html")
        self.assertIn("receipt_requested: autoPrint", pos)
        self.assertIn("s.receipt_job", pos)
        self.assertIn("`/sales/print/${saleData.id}`", pos)

    def test_receipt_pages_use_queue_instead_of_browser_print(self):
        sales = self.read("sales.html")
        settings = self.read("settings.html")
        print_js = self.read("js/print.js")

        self.assertIn('api("POST", `/sales/print/${id}`', sales)
        self.assertNotIn('<script src="/js/print.js"></script>', sales)
        self.assertIn('api("PUT", "/print/settings"', settings)
        self.assertIn('api("POST", "/print/test"', settings)
        self.assertNotIn('localStorage.setItem("ipos_print_settings"', settings)
        self.assertNotIn("function printReceipt(sale)", print_js)

    def test_return_receipts_have_create_and_history_actions(self):
        returns = self.read("returns.html")
        self.assertIn("Riwayat Retur &amp; Cetak", returns)
        self.assertIn("`/returns/${type}/${id}/print`", returns)

    def test_production_frontend_is_synchronized(self):
        for relative in (
            "pos.html",
            "pos_2.html",
            "sales.html",
            "trade_in.html",
            "returns.html",
            "settings.html",
            "js/print.js",
        ):
            self.assertEqual(
                (FRONTEND / relative).read_bytes(),
                (FRONTEND_DIST / relative).read_bytes(),
                f"frontend-dist/{relative} belum sinkron",
            )


if __name__ == "__main__":
    unittest.main()
