import json
import os
import base64
import io
import requests
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import win32api
import win32con
import win32gui
import win32print
import win32ui
import winreg
from PIL import Image, ImageWin
from tkinter import messagebox, simpledialog


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "printer_config.json")
DEFAULT_SERVER_URL = "https://desktop-b0e6dv6.balinese-alhena.ts.net"
APP_NAME = "FPOS_Printer_Agent"


def set_autostart(enable=True):
    if getattr(sys, "frozen", False):
        launch_command = f'"{sys.executable}"'
    else:
        launch_command = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
        try:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
        return True
    except OSError:
        return False


def load_or_setup():
    force_setup = "--setup" in sys.argv
    if os.path.exists(CONFIG_FILE) and not force_setup:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            if all(config.get(key) for key in ("server_url", "agent_token", "printer_name")):
                return config
        except (OSError, ValueError):
            pass

    root = tk.Tk()
    root.withdraw()

    server_url = simpledialog.askstring(
        "Setup Printer",
        "Alamat server FPOS:",
        initialvalue=DEFAULT_SERVER_URL,
        parent=root,
    )

    agent_token = simpledialog.askstring(
        "Setup Printer",
        "Tempel token agen dari menu Pengaturan > Printer:",
        parent=root,
    )

    printers = win32print.EnumPrinters(2)
    printer_names = [p[2] for p in printers]

    printer_name = simpledialog.askstring(
        "Setup Printer",
        f"Masukkan NAMA PRINTER (copy dari ini ya):\n\n" + "\n".join(printer_names),
        parent=root,
    )

    if not server_url or not agent_token or not printer_name:
        messagebox.showerror("Setup belum lengkap", "Server, token agen, dan printer wajib diisi.")
        root.destroy()
        raise SystemExit(1)

    auto_start = messagebox.askyesno("Auto Start", "Jalankan otomatis saat Windows nyala?")
    autostart_saved = set_autostart(auto_start)
    if auto_start and not autostart_saved:
        messagebox.showwarning(
            "Auto Start",
            "Konfigurasi printer tersimpan, tetapi Auto Start gagal dibuat. Tambahkan shortcut agen ke folder Startup secara manual.",
        )

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "server_url": server_url.strip().rstrip("/"),
            "agent_token": agent_token.strip(),
            "printer_name": printer_name.strip(),
            "autostart": auto_start and autostart_saved,
        }, f, indent=2)

    messagebox.showinfo("Berhasil", f"Agen terhubung ke printer: {printer_name}")
    root.destroy()
    return {
        "server_url": server_url.strip().rstrip("/"),
        "agent_token": agent_token.strip(),
        "printer_name": printer_name.strip(),
        "autostart": auto_start and autostart_saved,
    }


CONFIG = load_or_setup()
SERVER_URL = CONFIG["server_url"].rstrip("/")
API_URL = SERVER_URL if SERVER_URL.endswith("/api/print") else f"{SERVER_URL}/api/print"
AGENT_TOKEN = CONFIG["agent_token"]
PRINTER_NAME = CONFIG["printer_name"]
AGENT_HEADERS = {"X-Printer-Token": AGENT_TOKEN}
PENDING_RESULTS_FILE = os.path.join(APP_DIR, "printer_pending_results.json")


def load_pending_results():
    try:
        with open(PENDING_RESULTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_pending_results(results):
    tmp_file = f"{PENDING_RESULTS_FILE}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    os.replace(tmp_file, PENDING_RESULTS_FILE)


PENDING_RESULTS = load_pending_results()


def flush_pending_results():
    for job_id, payload in list(PENDING_RESULTS.items()):
        try:
            result = requests.post(
                f"{API_URL}/agent/jobs/{job_id}/result",
                json=payload,
                headers=AGENT_HEADERS,
                timeout=10,
            )
            if result.status_code in (404, 409):
                print(f"Hasil Job #{job_id} tidak lagi diterima server; data lokal dibersihkan")
                del PENDING_RESULTS[job_id]
                save_pending_results(PENDING_RESULTS)
                continue
            result.raise_for_status()
            del PENDING_RESULTS[job_id]
            save_pending_results(PENDING_RESULTS)
            print(f"Hasil Job #{job_id} diterima server")
        except requests.exceptions.RequestException as e:
            print(f"Hasil Job #{job_id} belum dapat dilaporkan: {e}")
            return False
    return True


def cek_printer(printer_name):
    try:
        printers = [p[2] for p in win32print.EnumPrinters(2)]
        return printer_name in printers
    except Exception:
        return False


def find_browser():
    browser_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((p for p in browser_paths if os.path.exists(p)), None)


def print_html(html_content: str, printer_name: str) -> bool:
    """
    Barcode HTML dicetak via browser karena ini yang paling cocok untuk layout label.
    """
    tmp_path = None
    old_default_printer = None
    proc = None

    try:
        if not cek_printer(printer_name):
            print(f"Printer '{printer_name}' tidak ditemukan untuk job HTML")
            return False

        print_script = """
<script>
window.addEventListener("load", function() {
  setTimeout(function() { window.print(); }, 700);
  setTimeout(function() { window.close(); }, 3500);
});
</script>
""".strip()

        lower_html = html_content.lower()
        if "<html" in lower_html and "</head>" in lower_html:
            wrapped_html = html_content.replace("</head>", f"{print_script}</head>", 1)
        elif "<html" in lower_html and "</body>" in lower_html:
            wrapped_html = html_content.replace("</body>", f"{print_script}</body>", 1)
        else:
            wrapped_html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  {print_script}
</head>
<body>{html_content}</body>
</html>"""

        tmp = tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        )
        tmp.write(wrapped_html)
        tmp.close()
        tmp_path = tmp.name

        browser = find_browser()
        if not browser:
            print("Chrome/Edge tidak ditemukan, fallback ke ShellExecute printto")
            win32api.ShellExecute(0, "printto", tmp_path, f'"{printer_name}"', ".", 0)
            time.sleep(8)
            return True

        old_default_printer = win32print.GetDefaultPrinter()
        if old_default_printer != printer_name:
            win32print.SetDefaultPrinter(printer_name)

        cmd = [
            browser,
            "--kiosk-printing",
            "--disable-print-preview",
            "--allow-file-access-from-files",
            "--disable-features=PrintCompositorLPAC",
            f"file:///{tmp_path.replace(os.sep, '/')}",
        ]
        proc = subprocess.Popen(cmd)
        time.sleep(10)

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        print("HTML PRINT SUCCESS")
        return True
    except Exception as e:
        print(f"HTML PRINT ERROR: {e}")
        return False
    finally:
        if old_default_printer:
            try:
                win32print.SetDefaultPrinter(old_default_printer)
            except Exception:
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _get_resample_filter():
    try:
        return Image.Resampling.NEAREST
    except AttributeError:
        return Image.NEAREST


def _fit_image_to_print_area(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    if image.size == (target_w, target_h):
        return image

    if image.width <= target_w and image.height <= target_h:
        canvas = Image.new("RGB", (target_w, target_h), "white")
        # Untuk stok 2/3 kolom yang tidak penuh, label harus mulai dari slot kiri-atas,
        # bukan di-center ke tengah area print.
        canvas.paste(image, (0, 0))
        return canvas

    scale = min(target_w / image.width, target_h / image.height)
    resized_w = max(1, int(round(image.width * scale)))
    resized_h = max(1, int(round(image.height * scale)))
    resized = image.resize((resized_w, resized_h), _get_resample_filter())
    canvas = Image.new("RGB", (target_w, target_h), "white")
    canvas.paste(resized, (0, 0))
    return canvas


def _build_label_devmode(h_printer, printer_name: str, width_mm: float, height_mm: float):
    printer_info = win32print.GetPrinter(h_printer, 2)
    devmode = printer_info.get("pDevMode")
    if devmode is None:
        raise RuntimeError("Printer tidak mengembalikan DEVMODE")

    devmode.Orientation = win32con.DMORIENT_PORTRAIT
    devmode.PaperSize = win32con.DMPAPER_USER
    devmode.PaperWidth = max(1, int(round(width_mm * 10)))
    devmode.PaperLength = max(1, int(round(height_mm * 10)))
    devmode.Fields |= (
        win32con.DM_ORIENTATION
        | win32con.DM_PAPERSIZE
        | win32con.DM_PAPERWIDTH
        | win32con.DM_PAPERLENGTH
    )

    try:
        win32print.DocumentProperties(
            0,
            h_printer,
            printer_name,
            devmode,
            devmode,
            win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER,
        )
    except Exception as e:
        print(f"DocumentProperties warning: {e}")

    return devmode


def _split_label_rows(
    image: Image.Image,
    total_height_mm: float,
    row_count: int,
    row_height_mm: float,
    gap_vertical_mm: float,
):
    if row_count <= 1 or row_height_mm <= 0 or total_height_mm <= row_height_mm:
        return [(image, total_height_mm)]

    expected_total_mm = (row_height_mm * row_count) + (
        gap_vertical_mm * max(0, row_count - 1)
    )
    if abs(expected_total_mm - total_height_mm) > 0.5:
        print(
            "Split row dibatalkan karena metadata tinggi sheet tidak cocok | "
            f"expected={expected_total_mm:.3f}mm actual={total_height_mm:.3f}mm"
        )
        return [(image, total_height_mm)]

    px_per_mm = image.height / total_height_mm
    pages = []
    page_top_mm = 0.0

    for row_index in range(row_count):
        page_height_mm = row_height_mm
        if row_index < row_count - 1:
            page_height_mm += gap_vertical_mm

        page_bottom_mm = min(total_height_mm, page_top_mm + page_height_mm)
        top_px = max(0, int(round(page_top_mm * px_per_mm)))
        bottom_px = min(image.height, int(round(page_bottom_mm * px_per_mm)))

        if bottom_px > top_px:
            pages.append(
                (
                    image.crop((0, top_px, image.width, bottom_px)),
                    page_bottom_mm - page_top_mm,
                )
            )

        page_top_mm = page_bottom_mm

    return pages or [(image, total_height_mm)]


def _print_label_page(
    image: Image.Image,
    printer_name: str,
    h_printer,
    width_mm: float,
    height_mm: float,
    page_index: int,
    page_count: int,
):
    dc = None

    try:
        devmode = _build_label_devmode(h_printer, printer_name, width_mm, height_mm)
        hdc = win32gui.CreateDC("WINSPOOL", printer_name, devmode)
        dc = win32ui.CreateDCFromHandle(hdc)

        printable_w = dc.GetDeviceCaps(win32con.HORZRES)
        printable_h = dc.GetDeviceCaps(win32con.VERTRES)
        if printable_w <= 0 or printable_h <= 0:
            raise RuntimeError("Ukuran area cetak printer tidak valid")

        image = _fit_image_to_print_area(image, int(printable_w), int(printable_h))
        job_name = (
            "Barcode Label"
            if page_count <= 1
            else f"Barcode Label {page_index}/{page_count}"
        )

        dc.StartDoc(job_name)
        dc.StartPage()
        dib = ImageWin.Dib(image)
        dib.draw(
            dc.GetHandleOutput(),
            (0, 0, int(printable_w), int(printable_h)),
        )
        dc.EndPage()
        dc.EndDoc()

        print(
            f"LABEL PAGE PRINT SUCCESS | page={page_index}/{page_count} | "
            f"size={width_mm}x{height_mm}mm | "
            f"dc={printable_w}x{printable_h}px"
        )
        return True
    finally:
        if dc is not None:
            try:
                dc.DeleteDC()
            except Exception:
                pass


def print_label_image(content: str, printer_name: str) -> bool:
    """
    Cetak label barcode langsung ke printer via Windows GDI.
    Jalur ini lebih stabil untuk ukuran label custom dibanding window.print().
    """
    h_printer = None

    try:
        if not cek_printer(printer_name):
            print(f"Printer '{printer_name}' tidak ditemukan untuk job label image")
            return False

        payload = json.loads(content) if isinstance(content, str) else content
        image_base64 = str(payload.get("image_base64") or "").strip()
        width_mm = float(payload.get("width_mm") or 0)
        height_mm = float(payload.get("height_mm") or 0)
        row_count = max(1, int(payload.get("row_count") or 1))
        row_height_mm = float(payload.get("row_height_mm") or 0)
        gap_vertical_mm = max(0.0, float(payload.get("gap_vertical_mm") or 0))

        if image_base64.startswith("data:"):
            image_base64 = image_base64.split(",", 1)[-1]

        if not image_base64:
            print("Job label image tidak punya image_base64")
            return False

        if width_mm <= 0 or height_mm <= 0:
            print("Job label image tidak punya ukuran label valid")
            return False

        image_bytes = base64.b64decode(image_base64)
        image = Image.open(io.BytesIO(image_bytes))
        if image.mode != "RGB":
            image = image.convert("RGB")

        h_printer = win32print.OpenPrinter(printer_name)

        # Zebra/driver label tertentu sering tetap menganggap tinggi page cuma 1 row.
        # Jadi sheet multi-row dipecah per row agar 5 label di 3 kolom tetap keluar 2 row.
        pages = _split_label_rows(
            image=image,
            total_height_mm=height_mm,
            row_count=row_count,
            row_height_mm=row_height_mm,
            gap_vertical_mm=gap_vertical_mm,
        )

        for page_index, (page_image, page_height_mm) in enumerate(pages, start=1):
            _print_label_page(
                image=page_image,
                printer_name=printer_name,
                h_printer=h_printer,
                width_mm=width_mm,
                height_mm=page_height_mm,
                page_index=page_index,
                page_count=len(pages),
            )

        print(
            f"LABEL IMAGE PRINT SUCCESS | sheet={width_mm}x{height_mm}mm | "
            f"rows={len(pages)} | source={image.width}x{image.height}px"
        )
        return True
    except Exception as e:
        print(f"LABEL IMAGE PRINT ERROR: {e}")
        return False
    finally:
        if h_printer is not None:
            try:
                win32print.ClosePrinter(h_printer)
            except Exception:
                pass


def print_windows(text):
    try:
        printer_name = PRINTER_NAME

        print("===================================")
        print("PRINT JOB")
        print("Printer:", printer_name)
        print("Length :", len(text))

        if not cek_printer(printer_name):
            print(f"Printer '{printer_name}' tidak ditemukan")
            return False

        h_printer = win32print.OpenPrinter(printer_name)

        try:
            win32print.StartDocPrinter(h_printer, 1, ("Struk Kasir", None, "RAW"))
            win32print.StartPagePrinter(h_printer)
            win32print.WritePrinter(h_printer, b"\x1B\x40")
            win32print.WritePrinter(h_printer, text.encode("latin-1", errors="replace"))
            win32print.WritePrinter(h_printer, b"\x1D\x56\x00")
            win32print.EndPagePrinter(h_printer)
            win32print.EndDocPrinter(h_printer)
        finally:
            win32print.ClosePrinter(h_printer)

        print("PRINT SUCCESS")
        return True
    except Exception as e:
        print(f"PRINT ERROR: {e}")
        return False


print(f"Printer Agent Hybrid jalan | Server: {SERVER_URL} | Printer: {PRINTER_NAME}")
print("Jalankan dengan --setup untuk mengganti server, token, atau printer.")

while True:
    try:
        if not flush_pending_results():
            time.sleep(2)
            continue
        res = requests.post(
            f"{API_URL}/agent/claim",
            json={"limit": 1},
            headers=AGENT_HEADERS,
            timeout=10,
        )

        if res.status_code == 200:
            jobs = res.json()

            if jobs:
                print(f"Dapat {len(jobs)} antrean job")

            for job in jobs:
                content_type = (job.get("content_type") or "raw").lower()

                if content_type == "html":
                    print(f"Memproses Job #{job['id']} (Mode: HTML)")
                    success = print_html(job["content"], PRINTER_NAME)
                elif content_type == "label_image":
                    print(f"Memproses Job #{job['id']} (Mode: LABEL IMAGE)")
                    success = print_label_image(job["content"], PRINTER_NAME)
                else:
                    print(f"Memproses Job #{job['id']} (Mode: RAW ESC/POS / Struk)")
                    success = print_windows(job["content"])

                PENDING_RESULTS[str(job["id"])] = {
                    "success": success,
                    "error": None if success else f"Printer '{PRINTER_NAME}' gagal mencetak",
                }
                save_pending_results(PENDING_RESULTS)
                flush_pending_results()
                if success:
                    print(f"Job #{job['id']} selesai")
                else:
                    print(f"Gagal print Job #{job['id']}; server akan mencoba ulang sesuai batas")
        elif res.status_code == 401:
            print("Token agen tidak valid. Rotasi token di Pengaturan lalu jalankan agen dengan --setup.")
            time.sleep(10)
        else:
            print(f"Server menolak claim: HTTP {res.status_code} {res.text[:200]}")

    except requests.exceptions.RequestException as e:
        print(f"Koneksi server gagal: {e}")
    except Exception as e:
        print(f"LOOP ERROR: {e}")

    time.sleep(1)
