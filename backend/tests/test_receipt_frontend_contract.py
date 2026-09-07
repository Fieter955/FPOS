import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
FRONTEND_DIST = ROOT / "frontend-dist"
BUILD_MANIFEST = ROOT / "build-manifest.json"


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

    def test_production_frontend_is_built_from_current_contract(self):
        # frontend-dist is intentionally minified and asset references are
        # rewritten by build.mjs, so byte-for-byte comparison with frontend/
        # would reject every valid production build. Check the generated files
        # and the receipt-specific contract that must survive minification.
        required_markers = {
            "pos.html": (
                "receipt_requested:",
                "receipt_job",
                "/sales/print/",
            ),
            "sales.html": ("/sales/print/",),
            "returns.html": ("/returns/",),
            "settings.html": ("/print/settings", "/print/test"),
            "js/print.js": (),
        }
        manifest = json.loads(BUILD_MANIFEST.read_text(encoding="utf-8")) if BUILD_MANIFEST.is_file() else {}
        for relative, markers in required_markers.items():
            source_path = FRONTEND / relative
            built_relative = manifest.get("/" + relative, "/" + relative).lstrip("/")
            dist_path = FRONTEND_DIST / built_relative
            self.assertTrue(source_path.is_file(), f"Sumber frontend hilang: {relative}")
            self.assertTrue(dist_path.is_file(), f"frontend-dist/{relative} belum dibangun")
            built = dist_path.read_text(encoding="utf-8")
            self.assertTrue(built.strip(), f"frontend-dist/{relative} kosong")
            for marker in markers:
                self.assertIn(marker, built, f"Kontrak {marker!r} hilang di frontend-dist/{relative}")


if __name__ == "__main__":
    unittest.main()
