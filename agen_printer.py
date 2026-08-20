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
from tkinter import messagebox, ttk


def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "printer_config.json")
LOG_FILE = os.path.join(APP_DIR, "printer_agent.log")
DEFAULT_SERVER_URL = "https://desktop-b0e6dv6.balinese-alhena.ts.net"
APP_NAME = "FPOS_Printer_Agent"


class FileLogger:
    def write(self, value):
        if not value:
            return
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as log_file:
                for line in value.rstrip().splitlines():
                    log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except OSError:
            pass

    def flush(self):
        pass


if getattr(sys, "frozen", False) or not sys.stdout:
    sys.stdout = FileLogger()
    sys.stderr = sys.stdout


def enumerate_printers():
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    printers = []
    default_name = ""
    try:
        default_name = win32print.GetDefaultPrinter()
    except Exception:
        pass
    for info in win32print.EnumPrinters(flags, None, 2):
        name = str(info.get("pPrinterName") or "").strip()
        if not name:
            continue
        printers.append({
            "name": name,
            "driver": str(info.get("pDriverName") or ""),
            "port": str(info.get("pPortName") or ""),
            "status": int(info.get("Status") or 0),
            "jobs": int(info.get("cJobs") or 0),
            "default": name == default_name,
        })
    return sorted(printers, key=lambda item: (not item["default"], item["name"].lower()))


def detect_print_mode(printer_info, role):
    haystack = " ".join([
        printer_info.get("name", ""),
        printer_info.get("driver", ""),
        printer_info.get("port", ""),
    ]).lower()
    if any(token in haystack for token in ("zpl", "zdesigner", "zebra")):
        return "ZPL"
    if role == "receipt" and any(token in haystack for token in (
        "esc/pos", "epson tm", "xprinter", "rongta", "bixolon", "receipt", "pos printer",
    )):
        return "ESC/POS"
    return "GDI"


def detect_dpi(printer_info):
    haystack = f"{printer_info.get('name', '')} {printer_info.get('driver', '')}".lower()
    if "600dpi" in haystack or "600 dpi" in haystack:
        return 600
    if "300dpi" in haystack or "300 dpi" in haystack:
        return 300
    return 203


def send_raw(printer_name, data, document_name):
    handle = win32print.OpenPrinter(printer_name)
    try:
        job_id = win32print.StartDocPrinter(handle, 1, (document_name, None, "RAW"))
        win32print.StartPagePrinter(handle)
        written = win32print.WritePrinter(handle, data)
        win32print.EndPagePrinter(handle)
        win32print.EndDocPrinter(handle)
        print(f"SPOOL RAW | printer={printer_name} | job={job_id} | bytes={written}")
        return job_id
    finally:
        win32print.ClosePrinter(handle)


def run_setup_test(printer_info, mode, role):
    selected_mode = detect_print_mode(printer_info, role) if mode == "Auto" else mode
    if selected_mode == "ZPL":
        send_raw(
            printer_info["name"],
            b"^XA^PW600^LL240^FO35,35^A0N,38,38^FDFPOS TEST ZPL^FS^FO35,95^BY2^BCN,80,Y,N,N^FD123456789^FS^XZ",
            "FPOS Test Barcode ZPL",
        )
    elif selected_mode == "ESC/POS":
        send_raw(
            printer_info["name"],
            b"\x1b@\x1ba\x01FPOS TEST PRINTER\n\x1ba\x00Koneksi printer berhasil.\n\n\n\x1dV\x00",
            "FPOS Test Struk",
        )
    else:
        raise RuntimeError("Test GDI dilakukan lewat job FPOS setelah konfigurasi disimpan")


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
    previous = {}
    if os.path.exists(CONFIG_FILE) and not force_setup:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            printer_names = {item["name"] for item in enumerate_printers()}
            legacy_name = config.get("printer_name")
            receipt_name = config.get("receipt_printer") or legacy_name
            label_name = config.get("label_printer") or legacy_name
            if (
                config.get("server_url")
                and config.get("agent_token")
                and receipt_name in printer_names
                and label_name in printer_names
            ):
                config.update({
                    "receipt_printer": receipt_name,
                    "label_printer": label_name,
                    "receipt_mode": config.get("receipt_mode") or "Auto",
                    "label_mode": config.get("label_mode") or "Auto",
                })
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2)
                return config
            previous = config
        except (OSError, ValueError):
            pass

    root = tk.Tk()
    root.title("Setup Agen Printer FPOS")
    root.geometry("720x520")
    root.resizable(False, False)
    result = {}
    printer_map = {}

    server_var = tk.StringVar(value=previous.get("server_url") or DEFAULT_SERVER_URL)
    token_var = tk.StringVar(value=previous.get("agent_token") or "")
    receipt_var = tk.StringVar(value=previous.get("receipt_printer") or previous.get("printer_name") or "")
    label_var = tk.StringVar(value=previous.get("label_printer") or previous.get("printer_name") or "")
    receipt_mode_var = tk.StringVar(value=previous.get("receipt_mode") or "Auto")
    label_mode_var = tk.StringVar(value=previous.get("label_mode") or "Auto")
    autostart_var = tk.BooleanVar(value=bool(previous.get("autostart", True)))
    receipt_info_var = tk.StringVar()
    label_info_var = tk.StringVar()

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Agen Printer FPOS", font=("Segoe UI", 16, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 14))
    ttk.Label(frame, text="Alamat server FPOS").grid(row=1, column=0, sticky="w")
    ttk.Entry(frame, textvariable=server_var, width=72).grid(row=2, column=0, columnspan=4, sticky="ew", pady=(2, 10))
    ttk.Label(frame, text="Token agen cabang").grid(row=3, column=0, sticky="w")
    ttk.Entry(frame, textvariable=token_var, width=72, show="*").grid(row=4, column=0, columnspan=4, sticky="ew", pady=(2, 14))

    ttk.Label(frame, text="Printer Struk").grid(row=5, column=0, sticky="w")
    receipt_combo = ttk.Combobox(frame, textvariable=receipt_var, state="readonly", width=48)
    receipt_combo.grid(row=6, column=0, columnspan=2, sticky="ew")
    ttk.Combobox(frame, textvariable=receipt_mode_var, values=("Auto", "ESC/POS", "GDI"), state="readonly", width=12).grid(row=6, column=2, sticky="w", padx=8)
    ttk.Button(frame, text="Test Struk", command=lambda: test_role("receipt")).grid(row=6, column=3, sticky="e")
    ttk.Label(frame, textvariable=receipt_info_var, foreground="#555").grid(row=7, column=0, columnspan=4, sticky="w", pady=(3, 14))

    ttk.Label(frame, text="Printer Barcode").grid(row=8, column=0, sticky="w")
    label_combo = ttk.Combobox(frame, textvariable=label_var, state="readonly", width=48)
    label_combo.grid(row=9, column=0, columnspan=2, sticky="ew")
    ttk.Combobox(frame, textvariable=label_mode_var, values=("Auto", "ZPL", "GDI"), state="readonly", width=12).grid(row=9, column=2, sticky="w", padx=8)
    ttk.Button(frame, text="Test Barcode", command=lambda: test_role("label")).grid(row=9, column=3, sticky="e")
    ttk.Label(frame, textvariable=label_info_var, foreground="#555").grid(row=10, column=0, columnspan=4, sticky="w", pady=(3, 14))

    def selected_info(name, role):
        info = printer_map.get(name)
        if not info:
            return "Printer belum dipilih"
        mode_var = receipt_mode_var if role == "receipt" else label_mode_var
        detected = detect_print_mode(info, role) if mode_var.get() == "Auto" else mode_var.get()
        status = "Ready" if info["status"] == 0 else f"Windows status={info['status']}"
        return f"Driver: {info['driver']} | Port: {info['port']} | Mode: {detected} | {status}"

    def refresh_details(*_args):
        receipt_info_var.set(selected_info(receipt_var.get(), "receipt"))
        label_info_var.set(selected_info(label_var.get(), "label"))

    def refresh_printers():
        nonlocal printer_map
        items = enumerate_printers()
        printer_map = {item["name"]: item for item in items}
        names = list(printer_map)
        receipt_combo["values"] = names
        label_combo["values"] = names
        default_name = next((item["name"] for item in items if item["default"]), names[0] if names else "")
        if receipt_var.get() not in printer_map:
            receipt_var.set(default_name)
        if label_var.get() not in printer_map:
            label_var.set(default_name)
        refresh_details()

    def test_role(role):
        name = receipt_var.get() if role == "receipt" else label_var.get()
        mode = receipt_mode_var.get() if role == "receipt" else label_mode_var.get()
        info = printer_map.get(name)
        if not info:
            messagebox.showerror("Test Printer", "Pilih printer terlebih dahulu.", parent=root)
            return
        try:
            run_setup_test(info, mode, role)
            messagebox.showinfo("Test Printer", f"Job test dikirim ke {name}.", parent=root)
        except Exception as exc:
            messagebox.showerror("Test Printer", str(exc), parent=root)

    def save_config():
        if not server_var.get().strip() or not token_var.get().strip():
            messagebox.showerror("Setup belum lengkap", "Server dan token agen wajib diisi.", parent=root)
            return
        if receipt_var.get() not in printer_map or label_var.get() not in printer_map:
            messagebox.showerror("Setup belum lengkap", "Pilih printer struk dan barcode.", parent=root)
            return
        autostart_saved = set_autostart(autostart_var.get())
        label_info = printer_map[label_var.get()]
        result.update({
            "server_url": server_var.get().strip().rstrip("/"),
            "agent_token": token_var.get().strip(),
            "receipt_printer": receipt_var.get(),
            "label_printer": label_var.get(),
            "receipt_mode": receipt_mode_var.get(),
            "label_mode": label_mode_var.get(),
            "label_dpi": detect_dpi(label_info),
            "autostart": bool(autostart_var.get() and autostart_saved),
        })
        with open(CONFIG_FILE, "w", encoding="utf-8") as config_file:
            json.dump(result, config_file, indent=2)
        root.destroy()

    receipt_combo.bind("<<ComboboxSelected>>", refresh_details)
    label_combo.bind("<<ComboboxSelected>>", refresh_details)
    receipt_mode_var.trace_add("write", refresh_details)
    label_mode_var.trace_add("write", refresh_details)
    ttk.Checkbutton(frame, text="Jalankan otomatis saat Windows menyala", variable=autostart_var).grid(row=11, column=0, columnspan=3, sticky="w", pady=(4, 18))
    ttk.Button(frame, text="Refresh Printer", command=refresh_printers).grid(row=12, column=0, sticky="w")
    ttk.Button(frame, text="Simpan dan Jalankan", command=save_config).grid(row=12, column=2, columnspan=2, sticky="e")
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    refresh_printers()
    root.mainloop()
    if not result:
        raise SystemExit(1)
    return result


CONFIG = load_or_setup()
SERVER_URL = CONFIG["server_url"].rstrip("/")
API_URL = SERVER_URL if SERVER_URL.endswith("/api/print") else f"{SERVER_URL}/api/print"
AGENT_TOKEN = CONFIG["agent_token"]
RECEIPT_PRINTER = CONFIG["receipt_printer"]
LABEL_PRINTER = CONFIG["label_printer"]
RECEIPT_MODE = CONFIG.get("receipt_mode", "Auto")
LABEL_MODE = CONFIG.get("label_mode", "Auto")
LABEL_DPI = int(CONFIG.get("label_dpi") or 203)
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
        return any(item["name"] == printer_name for item in enumerate_printers())
    except Exception:
        return False


def get_printer_info(printer_name):
    return next((item for item in enumerate_printers() if item["name"] == printer_name), None)


def resolve_mode(printer_name, configured_mode, role):
    if configured_mode and configured_mode != "Auto":
        return configured_mode
    info = get_printer_info(printer_name)
    return detect_print_mode(info or {"name": printer_name}, role)


def assert_printer_ready(printer_name):
    info = get_printer_info(printer_name)
    if not info:
        raise RuntimeError(f"Printer '{printer_name}' tidak ditemukan")
    status = info["status"]
    blocked = {
        win32print.PRINTER_STATUS_PAUSED: "paused",
        win32print.PRINTER_STATUS_ERROR: "error",
        win32print.PRINTER_STATUS_PAPER_JAM: "paper jam",
        win32print.PRINTER_STATUS_PAPER_OUT: "kertas/label habis",
        win32print.PRINTER_STATUS_OFFLINE: "offline",
        win32print.PRINTER_STATUS_DOOR_OPEN: "cover terbuka",
        win32print.PRINTER_STATUS_USER_INTERVENTION: "memerlukan tindakan pengguna",
        win32print.PRINTER_STATUS_NOT_AVAILABLE: "tidak tersedia",
    }
    problems = [label for flag, label in blocked.items() if status & flag]
    if problems:
        raise RuntimeError(f"Printer '{printer_name}' tidak siap: {', '.join(problems)}")
    return info


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
    devmode = printer_info["pDevMode"]

    devmode.Orientation = win32con.DMORIENT_PORTRAIT
    devmode.PaperWidth = max(1, int(round(width_mm * 10)))
    devmode.PaperLength = max(1, int(round(height_mm * 10)))
    devmode.Fields |= (
        win32con.DM_ORIENTATION
        | win32con.DM_PAPERWIDTH
        | win32con.DM_PAPERLENGTH
    )

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


def print_label_image_zpl(content: str, printer_name: str, dpi: int = 203) -> bool:
    """Kirim label sebagai ZPL raster untuk printer Zebra ZDesigner."""
    h_printer = None
    try:
        assert_printer_ready(printer_name)

        payload = json.loads(content) if isinstance(content, str) else content
        image_base64 = str(payload.get("image_base64") or "").strip()
        width_mm = float(payload.get("width_mm") or 0)
        height_mm = float(payload.get("height_mm") or 0)
        if image_base64.startswith("data:"):
            image_base64 = image_base64.split(",", 1)[-1]
        if not image_base64 or width_mm <= 0 or height_mm <= 0:
            print("Job ZPL tidak punya gambar atau ukuran valid")
            return False

        image = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("L")
        target_w = max(1, int(round(width_mm * dpi / 25.4)))
        target_h = max(1, int(round(height_mm * dpi / 25.4)))
        image = image.resize((target_w, target_h), _get_resample_filter())
        image = image.point(lambda pixel: 0 if pixel < 180 else 255, mode="1")
        row_bytes = (target_w + 7) // 8
        bitmap = bytearray()
        for y in range(target_h):
            for byte_x in range(row_bytes):
                value = 0
                for bit in range(8):
                    x = byte_x * 8 + bit
                    if x < target_w and image.getpixel((x, y)) == 0:
                        value |= 1 << (7 - bit)
                bitmap.append(value)

        total_bytes = len(bitmap)
        zpl = (
            f"^XA^PW{target_w}^LL{target_h}^FO0,0^GFA,{total_bytes},{total_bytes},{row_bytes},"
            f"{bitmap.hex().upper()}^FS^XZ"
        ).encode("ascii")

        h_printer = win32print.OpenPrinter(printer_name)
        win32print.StartDocPrinter(h_printer, 1, ("Barcode Label ZPL", None, "RAW"))
        win32print.StartPagePrinter(h_printer)
        win32print.WritePrinter(h_printer, zpl)
        win32print.EndPagePrinter(h_printer)
        win32print.EndDocPrinter(h_printer)
        print(f"ZPL PRINT SUCCESS | size={width_mm}x{height_mm}mm | dots={target_w}x{target_h}")
        return True
    except Exception as e:
        print(f"ZPL PRINT ERROR: {e}")
        return False
    finally:
        if h_printer is not None:
            try:
                win32print.ClosePrinter(h_printer)
            except Exception:
                pass


def print_windows(text, printer_name):
    try:
        print("===================================")
        print("PRINT JOB")
        print("Printer:", printer_name)
        print("Length :", len(text))

        assert_printer_ready(printer_name)

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


def print_text_gdi(text, printer_name):
    dc = None
    try:
        assert_printer_ready(printer_name)
        dc = win32ui.CreateDC()
        dc.CreatePrinterDC(printer_name)
        dc.StartDoc("Struk FPOS")
        dc.StartPage()
        width = dc.GetDeviceCaps(win32con.HORZRES)
        y = 40
        line_height = 28
        for line in str(text).splitlines():
            dc.DrawText(line, (20, y, max(40, width - 20), y + line_height), win32con.DT_LEFT)
            y += line_height
        dc.EndPage()
        dc.EndDoc()
        print(f"GDI TEXT PRINT SUCCESS | printer={printer_name}")
        return True
    except Exception as e:
        print(f"GDI TEXT PRINT ERROR: {e}")
        return False
    finally:
        if dc is not None:
            try:
                dc.DeleteDC()
            except Exception:
                pass


print(
    f"Printer Agent jalan | Server: {SERVER_URL} | "
    f"Struk: {RECEIPT_PRINTER} ({resolve_mode(RECEIPT_PRINTER, RECEIPT_MODE, 'receipt')}) | "
    f"Barcode: {LABEL_PRINTER} ({resolve_mode(LABEL_PRINTER, LABEL_MODE, 'label')})"
)
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
                    success = print_html(job["content"], LABEL_PRINTER)
                elif content_type == "label_image":
                    print(f"Memproses Job #{job['id']} (Mode: LABEL IMAGE)")
                    label_mode = resolve_mode(LABEL_PRINTER, LABEL_MODE, "label")
                    if label_mode == "ZPL":
                        success = print_label_image_zpl(job["content"], LABEL_PRINTER, LABEL_DPI)
                    else:
                        success = print_label_image(job["content"], LABEL_PRINTER)
                else:
                    print(f"Memproses Job #{job['id']} (Mode: RAW ESC/POS / Struk)")
                    receipt_mode = resolve_mode(RECEIPT_PRINTER, RECEIPT_MODE, "receipt")
                    if receipt_mode == "GDI":
                        success = print_text_gdi(job["content"], RECEIPT_PRINTER)
                    else:
                        success = print_windows(job["content"], RECEIPT_PRINTER)

                PENDING_RESULTS[str(job["id"])] = {
                    "success": success,
                    "error": None if success else "Agen printer gagal mengirim job; lihat printer_agent.log",
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
