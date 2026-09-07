import unittest

from PIL import Image

from printer_zpl import image_to_zpl_bitmap


class ZplBitmapPolarityTests(unittest.TestCase):
    def test_black_pixel_followed_by_white_pixels_sets_only_high_bit(self):
        image = Image.new("L", (8, 1), 255)
        image.putpixel((0, 0), 0)

        self.assertEqual(image_to_zpl_bitmap(image), b"\x80")

    def test_solid_white_and_black_rows_use_zpl_polarity(self):
        white = Image.new("L", (8, 1), 255)
        black = Image.new("L", (8, 1), 0)

        self.assertEqual(image_to_zpl_bitmap(white), b"\x00")
        self.assertEqual(image_to_zpl_bitmap(black), b"\xff")

    def test_unused_bits_stay_clear_for_non_byte_aligned_width(self):
        white = Image.new("L", (9, 1), 255)
        black = Image.new("L", (9, 1), 0)

        self.assertEqual(image_to_zpl_bitmap(white), b"\x00\x00")
        self.assertEqual(image_to_zpl_bitmap(black), b"\xff\x80")

    def test_threshold_keeps_dark_text_black_and_light_background_white(self):
        image = Image.new("L", (8, 1))
        image.putdata([0, 100, 179, 180, 181, 220, 254, 255])

        self.assertEqual(image_to_zpl_bitmap(image, threshold=180), b"\xe0")


if __name__ == "__main__":
    unittest.main()
