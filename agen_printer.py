import os
import sys
import requests
import time
import win32print
import win32api
import json
import winreg
import tkinter as tk
import tempfile
import subprocess
from tkinter import simpledialog, messagebox

# ==============================
# CONFIG
# ==============================
def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
CONFIG_FILE = os.path.join(APP_DIR, "printer_config.json")


API_URL = "https://desktop-b0e6dv6.balinese-alhena.ts.net/api/print"
APP_NAME = "FPOS_Printer_Agent"

# ==============================
# AUTO START
# ==============================
def set_autostart(enable=True):
    exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(sys.argv[0])
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

# ==============================
# LOAD CONFIG / SETUP AWAL
# ==============================
def load_or_setup():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            config = json.load(f)
            return config.get("branch_id"), config.get("printer_name")

    root = tk.Tk()
    root.withdraw()

    branch_id = simpledialog.askinteger(
        "Setup Printer",
        "Masukkan ID Cabang (contoh: 1, 2, 3):",
        parent=root
    )

    printers = win32print.EnumPrinters(2)
    printer_names = [p[2] for p in printers]

    printer_name = simpledialog.askstring(
        "Setup Printer",
        f"Masukkan NAMA PRINTER (copy dari ini ya):\n\n" + "\n".join(printer_names),
        parent=root
    )

    auto_start = messagebox.askyesno("Auto Start", "Jalankan otomatis saat Windows nyala?")

    set_autostart(auto_start)

    with open(CONFIG_FILE, "w") as f:
        json.dump({
            "branch_id": branch_id,
            "printer_name": printer_name,
            "autostart": auto_start
        }, f)

    messagebox.showinfo("Berhasil", f"Cabang {branch_id} → Printer: {printer_name}")
    return branch_id, printer_name

BRANCH_ID, PRINTER_NAME = load_or_setup()

# ==============================
# CEK PRINTER
# ==============================
def cek_printer(printer_name):
    try:
        printers = [p[2] for p in win32print.EnumPrinters(2)]
        return printer_name in printers
    except:
        return False

def print_html(html_content: str, printer_name: str) -> bool:
    """
    Print HTML (barcode label) langsung ke printer tanpa buka browser dialog.
    Pakai Chrome/Edge headless --kiosk-printing, tidak ada about:blank, tidak ada header/footer.
    """
    try:
        # Simpan HTML ke file sementara
        tmp = tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        )
        tmp.write(html_content)
        tmp.close()
        tmp_path = tmp.name

        # Cari Chrome atau Edge
        browser_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        browser = next((p for p in browser_paths if os.path.exists(p)), None)

        if not browser:
            print("❌ Chrome/Edge tidak ditemukan, fallback ke ShellExecute")
            # Fallback: print via default app (buka dialog, tapi setidaknya tidak about:blank)
            win32api.ShellExecute(0, "print", tmp_path, f'/d:"{printer_name}"', ".", 0)
            time.sleep(5)
            os.unlink(tmp_path)
            return True

        # Chrome/Edge silent print — tidak ada dialog, tidak ada header/footer
        cmd = [
            browser,
            "--headless",
            "--disable-gpu",
            f"--printer-name={printer_name}",
            "--kiosk-printing",           # langsung print tanpa dialog
            "--no-pdf-header-footer",     # hapus header/footer (about:blank, tanggal, dll)
            f"file:///{tmp_path.replace(os.sep, '/')}",
        ]
        proc = subprocess.run(cmd, timeout=15)
        os.unlink(tmp_path)

        if proc.returncode == 0:
            print("✅ HTML PRINT SUCCESS (headless)")
            return True
        else:
            print(f"⚠️ Browser exit code: {proc.returncode}")
            return False

    except Exception as e:
        print(f"🔥 HTML PRINT ERROR: {e}")
        return False


# ==============================
# PRINT FUNCTION
# ==============================
def print_windows(text):
    try:
        printer_name = PRINTER_NAME

        print("===================================")
        print("🖨️ PRINT JOB")
        print("Branch :", BRANCH_ID)
        print("Printer:", printer_name)
        print("Length :", len(text))

        if not cek_printer(printer_name):
            print(f"❌ Printer '{printer_name}' tidak ditemukan!")
            return False

        hPrinter = win32print.OpenPrinter(printer_name)

        try:
            win32print.StartDocPrinter(hPrinter, 1, ("Struk Kasir", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)

            # reset printer
            win32print.WritePrinter(hPrinter, b'\x1B\x40')

            # kirim text
            win32print.WritePrinter(hPrinter, text.encode("latin-1", errors="replace"))

            # cut kertas
            win32print.WritePrinter(hPrinter, b'\x1D\x56\x00')

            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)

        finally:
            win32print.ClosePrinter(hPrinter)

        print("✅ PRINT SUCCESS")
        return True

    except Exception as e:
        print(f"🔥 PRINT ERROR: {e}")
        return False

# ==============================
# MAIN LOOP
# ==============================
print(f"🚀 Printer Agent Hybrid jalan | Cabang {BRANCH_ID} | Printer: {PRINTER_NAME}")

while True:
    try:
        res = requests.get(
            f"{API_URL}/",
            params={"branch_id": BRANCH_ID},
            timeout=5
        )

        if res.status_code == 200:
            jobs = res.json()

            if jobs:
                print(f"📦 Dapat {len(jobs)} antrean job")

            for job in jobs:
                # ✅ Routing: HTML (barcode label) vs RAW ESC/POS (struk kasir)
                content_type = job.get("content_type", "raw")
                
                if content_type == "html":
                    print(f"🔄 Memproses Job #{job['id']} (Mode: Chrome Headless / HTML)")
                    success = print_html(job["content"], PRINTER_NAME)
                else:
                    print(f"🔄 Memproses Job #{job['id']} (Mode: RAW ESC/POS / Struk)")
                    success = print_windows(job["content"])

                # Jika berhasil dicetak, kirim sinyal 'done' ke backend
                if success:
                    requests.post(f"{API_URL}/done/{job['id']}", timeout=5)
                    print(f"✅ Job #{job['id']} selesai!")
                else:
                    print(f"❌ Gagal print Job #{job['id']}, status tidak diubah.")

    except requests.exceptions.RequestException as e:
        # Error koneksi ke server, di-pass saja supaya tidak menuhin layar
        pass
    except Exception as e:
        print(f"🔥 LOOP ERROR: {e}")

    # Jeda 1 detik sebelum ngecek antrean lagi
    time.sleep(1)