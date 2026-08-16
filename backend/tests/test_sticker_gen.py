import unittest
from io import BytesIO
from unittest.mock import patch

from PIL import Image

from app.routes import sticker_gen


class StickerPriceVisibilityTests(unittest.TestCase):
    def test_price_visibility_defaults_to_enabled_for_old_payloads(self):
        item = sticker_gen.ProdukItem(nama="Barang Lama", barcode="IPS-LAMA")

        self.assertTrue(item.tampilkan_harga)

    def test_hidden_price_skips_price_layout_and_drawing(self):
        request = sticker_gen.StikerBatchRequest(
            data_produk=[
                sticker_gen.ProdukItem(
                    nama="Barang Fluktuatif",
                    barcode="IPS-FLUKTUATIF",
                    harga=25000,
                    tampilkan_harga=False,
                )
            ],
            jumlah_kolom=1,
            lebar_mm=33,
            tinggi_mm=15,
        )

        # Isolasi pengujian layout dari library barcode yang sebenarnya.
        fake_barcode = Image.new("1", (20, 20), 1)
        with patch.object(
            sticker_gen, "render_code128_fit", return_value=fake_barcode
        ), patch.object(
            sticker_gen,
            "fit_font",
            side_effect=AssertionError("fit_font harga tidak boleh dipanggil"),
        ):
            response = sticker_gen.render_stiker_sheet(request)

        image = Image.open(BytesIO(response.body))
        self.assertGreater(image.width, 0)
        self.assertGreater(image.height, 0)


class StickerColumnLayoutTests(unittest.TestCase):
    def _render(self, columns, item_count=1, legacy_sheet_columns=None):
        request = sticker_gen.StikerBatchRequest(
            data_produk=[
                sticker_gen.ProdukItem(
                    nama=f"Barang {index + 1}",
                    barcode=f"IPS-{index + 1}",
                    tampilkan_harga=False,
                )
                for index in range(item_count)
            ],
            jumlah_kolom=columns,
            jumlah_kolom_sheet=legacy_sheet_columns,
            gap_mm=2,
            gap_vertical_mm=1,
            lebar_mm=33,
            tinggi_mm=15,
            dpi_printer=203,
        )
        fake_barcode = Image.new("1", (20, 20), 0)
        with patch.object(
            sticker_gen, "render_code128_fit", return_value=fake_barcode
        ):
            response = sticker_gen.render_stiker_sheet(request)
        return response, Image.open(BytesIO(response.body))

    def test_single_label_keeps_selected_sheet_width_for_each_lin(self):
        for columns in (1, 2, 3):
            with self.subTest(columns=columns):
                # Nilai legacy 1 sengaja dikirim untuk memastikan renderer tidak
                # lagi menciutkan pilihan 2/3 Lin.
                response, image = self._render(
                    columns,
                    item_count=1,
                    legacy_sheet_columns=1,
                )
                label_width = sticker_gen.mm_to_px(33, 203)
                gap_width = sticker_gen.mm_to_px(2, 203)
                expected_width = (
                    label_width * columns + gap_width * max(0, columns - 1)
                )

                self.assertEqual(image.width, expected_width)
                self.assertEqual(response.headers["X-Sheet-Cols"], str(columns))
                self.assertAlmostEqual(
                    float(response.headers["X-Sheet-Width-Mm"]),
                    33 * columns + 2 * max(0, columns - 1),
                )

                if columns > 1:
                    unused_start = label_width + gap_width
                    unused_area = image.crop((unused_start, 0, image.width, image.height))
                    self.assertEqual(
                        unused_area.getextrema(),
                        ((255, 255), (255, 255), (255, 255)),
                    )

    def test_five_labels_in_three_lin_use_two_full_width_rows(self):
        response, image = self._render(3, item_count=5)
        label_height = sticker_gen.mm_to_px(15, 203)
        gap_height = sticker_gen.mm_to_px(1, 203)

        self.assertEqual(response.headers["X-Sheet-Cols"], "3")
        self.assertEqual(response.headers["X-Sheet-Rows"], "2")
        self.assertEqual(image.height, label_height * 2 + gap_height)


if __name__ == "__main__":
    unittest.main()
