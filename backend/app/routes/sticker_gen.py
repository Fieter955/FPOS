import io
import math
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from PIL import Image, ImageDraw, ImageFont

import barcode
from barcode.writer import ImageWriter

router = APIRouter()

# =========================================================
# MODELS
# =========================================================
class ProdukItem(BaseModel):
    nama: str
    barcode: str
    harga: float = 0
    kategori: Optional[str] = None


class StikerBatchRequest(BaseModel):
    data_produk: List[ProdukItem]
    jumlah_kolom: int = Field(3, ge=1, le=6)
    jumlah_kolom_sheet: Optional[int] = Field(None, ge=1, le=6)
    gap_mm: float = Field(2.0, ge=0)
    gap_vertical_mm: float = Field(0.0, ge=0)
    lebar_mm: float = Field(33.0, gt=0)
    tinggi_mm: float = Field(15.0, gt=0)
    dpi_printer: int = Field(203, ge=72)
    font_harga_px: int = Field(14, ge=6, le=72)
    font_nama_px: int = Field(12, ge=6, le=72)
    barcode_scale_pct: int = Field(100, ge=40, le=100)


# =========================================================
# HELPERS
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "fonts")


def mm_to_px(mm: float, dpi: int) -> int:
    return max(1, int(round((mm * dpi) / 25.4)))


def css_px_to_print_px(px: float, dpi: int) -> int:
    return max(1, int(round((float(px) * dpi) / 96.0)))


def format_rp(val: float) -> str:
    try:
        return "Rp " + f"{int(float(val)):,}".replace(",", ".")
    except Exception:
        return "Rp 0"


def load_font(font_name: str, size: int):
    candidates = [
        os.path.join(FONT_DIR, font_name),
        os.path.join(BASE_DIR, font_name),
        font_name,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def fit_font(draw: ImageDraw.ImageDraw, text: str, max_w: int, max_h: int,
             base_size: int, font_name: str, min_size: int = 8):
    for size in range(base_size, min_size - 1, -1):
        font = load_font(font_name, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            return font
    return load_font(font_name, min_size)


def truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text

    text = text.strip()
    while text and draw.textlength(text + "...", font=font) > max_w:
        text = text[:-1]
    return text + "..." if text else "-"


def wrap_text_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_w: int,
    max_lines: int,
):
    text = " ".join(str(text or "-").split())
    if not text:
        text = "-"

    lines = []
    remaining = text
    truncated = False

    while remaining:
        if len(lines) == max_lines:
            truncated = True
            break

        if draw.textlength(remaining, font=font) <= max_w:
            lines.append(remaining)
            remaining = ""
            break

        cut = len(remaining)
        while cut > 0 and draw.textlength(remaining[:cut], font=font) > max_w:
            cut -= 1

        if cut <= 0:
            cut = 1

        break_at = remaining.rfind(" ", 0, cut + 1)
        if break_at > 0:
            cut = break_at

        line = remaining[:cut].rstrip()
        if not line:
            line = remaining[:cut]

        lines.append(line)
        remaining = remaining[cut:].lstrip()

    if remaining and lines:
        lines[-1] = truncate_text(draw, lines[-1] + " " + remaining, font, max_w)
        truncated = True

    return lines or ["-"], truncated


def measure_multiline_text(draw: ImageDraw.ImageDraw, lines, font, line_gap: int):
    sample_bbox = draw.textbbox((0, 0), "Ag", font=font)
    line_h = sample_bbox[3] - sample_bbox[1]
    width = 0
    for line in lines:
        width = max(width, int(math.ceil(draw.textlength(line, font=font))))
    height = (line_h * len(lines)) + (line_gap * max(0, len(lines) - 1))
    return width, height, line_h


def fit_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    max_h: int,
    base_size: int,
    font_name: str,
    min_size: int = 8,
    max_lines: int = 2,
):
    fallback = None

    for size in range(base_size, min_size - 1, -1):
        font = load_font(font_name, size)
        line_gap = max(0, int(round(size * 0.08)))
        lines, truncated = wrap_text_lines(draw, text, font, max_w, max_lines)
        text_w, text_h, line_h = measure_multiline_text(draw, lines, font, line_gap)

        if text_w <= max_w and text_h <= max_h and not truncated:
            return font, lines, line_gap, line_h

        if text_w <= max_w and text_h <= max_h:
            fallback = (font, lines, line_gap, line_h)

    if fallback:
        return fallback

    font = load_font(font_name, min_size)
    line_gap = max(0, int(round(min_size * 0.08)))
    lines, _ = wrap_text_lines(draw, text, font, max_w, max_lines)
    _, _, line_h = measure_multiline_text(draw, lines, font, line_gap)
    return font, lines, line_gap, line_h


def render_code128_fit(
    code_text: str,
    max_w_px: int,
    max_h_px: int,
    dpi: int,
    preferred_height_factor: float = 1.0,
    min_module_height_mm: float = 4.5,
) -> Optional[Image.Image]:
    """
    Generate barcode tanpa resize paksa.
    Dicoba beberapa module_width + quiet_zone sampai muat.
    """
    code_text = str(code_text).strip()
    if not code_text:
        return None

    bc_class = barcode.get_barcode_class("code128")
    writer = ImageWriter(dpi=dpi)

    module_width_candidates = [0.30, 0.27, 0.25, 0.22, 0.20, 0.18, 0.16]
    quiet_zone_candidates = [1.2, 1.0, 0.8, 0.6]
    height_factor_candidates = []
    for candidate in [preferred_height_factor, 1.0, 0.92, 0.84, 0.76, 0.68]:
        normalized = round(max(0.65, min(1.0, candidate)), 2)
        if normalized not in height_factor_candidates:
            height_factor_candidates.append(normalized)

    for qz in quiet_zone_candidates:
        for hf in height_factor_candidates:
            module_height_mm = max(min_module_height_mm, ((max_h_px * hf) / dpi) * 25.4)
            for mw in module_width_candidates:
                options = {
                    "write_text": False,
                    "module_width": mw,
                    "module_height": module_height_mm,
                    "quiet_zone": qz,
                    "font_size": 0,
                    "text_distance": 0,
                }

                buf = io.BytesIO()
                try:
                    bc_obj = bc_class(code_text, writer=writer)
                    bc_obj.write(buf, options=options)
                    buf.seek(0)
                    img = Image.open(buf).convert("1")
                except Exception:
                    continue

                # Jangan crop kiri-kanan. Quiet zone tetap utuh.
                if img.width <= max_w_px and img.height <= max_h_px:
                    return img

    return None


# =========================================================
# ROUTE
# =========================================================
@router.post("/render-sheet")
def render_stiker_sheet(req: StikerBatchRequest):
    if not req.data_produk:
        raise HTTPException(status_code=400, detail="data_produk tidak boleh kosong")

    dpi = req.dpi_printer
    w_px = mm_to_px(req.lebar_mm, dpi)
    h_px = mm_to_px(req.tinggi_mm, dpi)
    cols = req.jumlah_kolom
    rows = math.ceil(len(req.data_produk) / cols)
    sheet_cols = req.jumlah_kolom_sheet or cols
    sheet_cols = max(1, min(cols, sheet_cols))
    if rows > 1:
        sheet_cols = cols
    gap_x_mm = req.gap_mm
    gap_y_mm = req.gap_vertical_mm
    gap_x_px = mm_to_px(gap_x_mm, dpi) if sheet_cols > 1 and gap_x_mm > 0 else 0
    gap_y_px = mm_to_px(gap_y_mm, dpi) if rows > 1 and gap_y_mm > 0 else 0

    sheet_w = (w_px * sheet_cols) + (gap_x_px * (sheet_cols - 1))
    sheet_h = (h_px * rows) + (gap_y_px * (rows - 1))
    sheet = Image.new("RGB", (sheet_w, sheet_h), "white")
    draw = ImageDraw.Draw(sheet)

    sheet_w_mm = (req.lebar_mm * sheet_cols) + (gap_x_mm * max(0, sheet_cols - 1))
    sheet_h_mm = (req.tinggi_mm * rows) + (gap_y_mm * max(0, rows - 1))

    price_base_size = css_px_to_print_px(req.font_harga_px, dpi)
    name_base_size = css_px_to_print_px(req.font_nama_px, dpi)
    min_font_size = css_px_to_print_px(6, dpi)
    barcode_fill = max(0.4, min(1.0, req.barcode_scale_pct / 100.0))

    for idx, item in enumerate(req.data_produk):
        col = idx % cols
        row = idx // cols

        x = col * (w_px + gap_x_px)
        y = row * (h_px + gap_y_px)

        is_two_col = cols == 2
        side_padding = max(6, mm_to_px(1.0, dpi))
        name_min_size = max(css_px_to_print_px(5, dpi), 8)
        price_font_size = price_base_size
        name_font_size = name_base_size
        barcode_fill_local = barcode_fill

        if is_two_col:
            # Jalur 2 kolom dipisah: sisakan area putih lebih besar di atas
            # dan bawah supaya tidak naik terlalu tinggi / spill ke row berikutnya.
            top_padding = max(16, mm_to_px(5.0, dpi))
            bottom_padding = max(4, mm_to_px(0.55, dpi))
            section_gap = max(1, mm_to_px(0.18, dpi))
            price_text_ratio = 0.10
            bottom_text_ratio = 0.12
            name_max_lines = 1
            price_font_size = max(min_font_size, int(round(price_base_size * 0.90)))
            name_font_size = max(name_min_size, int(round(name_base_size * 0.74)))
            barcode_fill_local = min(barcode_fill, 0.65)
        else:
            top_padding = max(2, mm_to_px(0.35, dpi))
            bottom_padding = max(4, mm_to_px(0.75, dpi))
            section_gap = max(1, mm_to_px(0.12, dpi))
            price_text_ratio = 0.16
            bottom_text_ratio = 0.30
            name_max_lines = 3 if req.tinggi_mm >= 18 else 2

        content_left = x + side_padding
        content_w = max(8, w_px - (side_padding * 2))
        content_center_x = content_left + (content_w // 2)

        nama = (item.nama or "-").strip()
        barcode_val = (item.barcode or "").strip()
        harga = format_rp(item.harga)

        price_text_max_h = max(min_font_size, int(h_px * price_text_ratio))
        bottom_text_max_h = max(min_font_size * 2, int(h_px * bottom_text_ratio))
        font_price = fit_font(
            draw,
            harga,
            max_w=content_w,
            max_h=price_text_max_h,
            base_size=price_font_size,
            font_name="arialbd.ttf",
            min_size=min_font_size,
        )

        font_name, name_lines, name_line_gap, name_line_h = fit_wrapped_text(
            draw,
            nama,
            max_w=max(8, w_px - (side_padding * 2)),
            max_h=bottom_text_max_h,
            base_size=name_font_size,
            font_name="arialbd.ttf",
            min_size=name_min_size,
            max_lines=name_max_lines,
        )

        price_bbox = draw.textbbox((0, 0), harga, font=font_price)
        price_w = price_bbox[2] - price_bbox[0]
        price_h = price_bbox[3] - price_bbox[1]

        name_block_h = (name_line_h * len(name_lines)) + (
            name_line_gap * max(0, len(name_lines) - 1)
        )

        top_h = top_padding + price_h
        bottom_h = bottom_padding + name_block_h
        barcode_top_y = y + top_h + section_gap
        barcode_bottom_y = y + h_px - bottom_h - section_gap
        available_barcode_h = barcode_bottom_y - barcode_top_y
        barcode_band_h = max(1, available_barcode_h) if is_two_col else max(18, available_barcode_h)

        # Top center: harga
        price_x = content_center_x - price_w // 2
        draw.text(
            (price_x, y + top_padding),
            harga,
            fill="black",
            font=font_price,
        )

        # Tengah: barcode
        bc_img = render_code128_fit(
            code_text=barcode_val,
            max_w_px=content_w,
            max_h_px=barcode_band_h,
            dpi=dpi,
            preferred_height_factor=barcode_fill_local,
            min_module_height_mm=3.0 if is_two_col else 4.5,
        )

        if bc_img is None:
            print(
                f"Sticker barcode skip render: kode '{barcode_val}' tidak muat "
                f"di label {req.lebar_mm}x{req.tinggi_mm}mm kolom={cols}"
            )
        else:
            bc_x = content_left + max(0, (content_w - bc_img.width) // 2)
            bc_y = barcode_top_y + max(0, ((barcode_band_h - bc_img.height) // 2))
            sheet.paste(bc_img, (int(bc_x), int(bc_y)))

        # Bottom: nama barang
        name_y = y + h_px - bottom_padding - name_block_h
        for line_idx, line_text in enumerate(name_lines):
            line_w = int(math.ceil(draw.textlength(line_text, font=font_name)))
            line_x = content_center_x - (line_w // 2)
            line_pos_y = name_y + (line_idx * (name_line_h + name_line_gap))
            draw.text((line_x, line_pos_y), line_text, fill="black", font=font_name)

    out = io.BytesIO()
    sheet.save(out, format="PNG", dpi=(dpi, dpi))
    out.seek(0)

    return Response(
        content=out.read(),
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'inline; filename="barcode-sheet.png"',
            "X-Sheet-Cols": str(sheet_cols),
            "X-Sheet-Rows": str(rows),
            "X-Sheet-Width-Mm": f"{sheet_w_mm:.3f}",
            "X-Sheet-Height-Mm": f"{sheet_h_mm:.3f}",
        },
    )
