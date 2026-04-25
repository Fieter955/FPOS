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
API_URL = "https://desktop-b0e6dv6.balinese-alhena.ts.net/api/print"
APP_NAME = "FPOS_Printer_Agent"


def set_autostart(enable=True):
    exe_path = (
        sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0])
    )
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
    if enable:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
    else:
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)


def load_or_setup():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config.get("branch_id"), config.get("printer_name")

    root = tk.Tk()
    root.withdraw()

    branch_id = simpledialog.askinteger(
        "Setup Printer",
        "Masukkan ID Cabang (contoh: 1, 2, 3):",
        parent=root,
    )

    printers = win32print.EnumPrinters(2)
    printer_names = [p[2] for p in printers]

    printer_name = simpledialog.askstring(
        "Setup Printer",
        f"Masukkan NAMA PRINTER (copy dari ini ya):\n\n" + "\n".join(printer_names),
        parent=root,
    )

    auto_start = messagebox.askyesno("Auto Start", "Jalankan otomatis saat Windows nyala?")
    set_autostart(auto_start)

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "branch_id": branch_id,
                "printer_name": printer_name,
                "autostart": auto_start,
            },
            f,
        )

    messagebox.showinfo("Berhasil", f"Cabang {branch_id} -> Printer: {printer_name}")
    return branch_id, printer_name


BRANCH_ID, PRINTER_NAME = load_or_setup()


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


def print_label_image(content: str, printer_name: str) -> bool:
    """
    Cetak label barcode langsung ke printer via Windows GDI.
    Jalur ini lebih stabil untuk ukuran label custom dibanding window.print().
    """
    h_printer = None
    dc = None

    try:
        if not cek_printer(printer_name):
            print(f"Printer '{printer_name}' tidak ditemukan untuk job label image")
            return False

        payload = json.loads(content) if isinstance(content, str) else content
        image_base64 = str(payload.get("image_base64") or "").strip()
        width_mm = float(payload.get("width_mm") or 0)
        height_mm = float(payload.get("height_mm") or 0)

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
        printer_info = win32print.GetPrinter(h_printer, 2)
        devmode = printer_info["pDevMode"]
        devmode.Orientation = win32con.DMORIENT_PORTRAIT
        devmode.PaperWidth = max(1, int(round(width_mm * 10)))
        devmode.PaperLength = max(1, int(round(height_mm * 10)))
        devmode.Fields |= (
            win32con.DM_ORIENTATION
            | win32con.DM_PAPERWIDTH
            | win32con.DM_PAPERLENGTH
        )

        hdc = win32gui.CreateDC("WINSPOOL", printer_name, devmode)
        dc = win32ui.CreateDCFromHandle(hdc)

        printable_w = dc.GetDeviceCaps(win32con.HORZRES)
        printable_h = dc.GetDeviceCaps(win32con.VERTRES)
        if printable_w <= 0 or printable_h <= 0:
            print("Ukuran area cetak printer tidak valid")
            return False

        target_w = int(printable_w)
        target_h = int(printable_h)
        if image.size != (target_w, target_h):
            if image.width <= target_w and image.height <= target_h:
                centered = Image.new("RGB", (target_w, target_h), "white")
                paste_x = (target_w - image.width) // 2
                paste_y = (target_h - image.height) // 2
                centered.paste(image, (paste_x, paste_y))
                image = centered
            else:
                scale = min(target_w / image.width, target_h / image.height)
                resized_w = max(1, int(round(image.width * scale)))
                resized_h = max(1, int(round(image.height * scale)))
                image = image.resize((resized_w, resized_h), _get_resample_filter())
                centered = Image.new("RGB", (target_w, target_h), "white")
                paste_x = (target_w - resized_w) // 2
                paste_y = (target_h - resized_h) // 2
                centered.paste(image, (paste_x, paste_y))
                image = centered

        dc.StartDoc("Barcode Label")
        dc.StartPage()
        dib = ImageWin.Dib(image)
        dib.draw(
            dc.GetHandleOutput(),
            (0, 0, target_w, target_h),
        )
        dc.EndPage()
        dc.EndDoc()

        print(
            f"LABEL IMAGE PRINT SUCCESS | size={width_mm}x{height_mm}mm | "
            f"dc={printable_w}x{printable_h}px | "
            f"target={target_w}x{target_h}px"
        )
        return True
    except Exception as e:
        print(f"LABEL IMAGE PRINT ERROR: {e}")
        return False
    finally:
        if dc is not None:
            try:
                dc.DeleteDC()
            except Exception:
                pass
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
        print("Branch :", BRANCH_ID)
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


print(f"Printer Agent Hybrid jalan | Cabang {BRANCH_ID} | Printer: {PRINTER_NAME}")

while True:
    try:
        res = requests.get(f"{API_URL}/", params={"branch_id": BRANCH_ID}, timeout=5)

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

                if success:
                    requests.post(f"{API_URL}/done/{job['id']}", timeout=5)
                    print(f"Job #{job['id']} selesai")
                else:
                    print(f"Gagal print Job #{job['id']}, status tidak diubah")
                    try:
                        requests.post(
                            f"{API_URL}/reset-stuck",
                            params={"branch_id": BRANCH_ID},
                            timeout=5,
                        )
                    except Exception:
                        pass

    except requests.exceptions.RequestException:
        pass
    except Exception as e:
        print(f"LOOP ERROR: {e}")

    time.sleep(1)
