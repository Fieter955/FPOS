import os
import sys
import uvicorn
import multiprocessing
import threading
import time

# --- LOGIKA PENENTU LOKASI (BASE DIR) ---
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ========================================================
# SISTEM LOG PINTAR (Bukan Ruang Hampa)
# Kalau ada error di mode tanpa console, tulis ke file Notepad!
# ========================================================
log_path = os.path.join(BASE_DIR, "error_log.txt")
if sys.stdout is None or sys.stderr is None:
    log_file = open(log_path, "w", encoding="utf-8")
    sys.stdout = log_file
    sys.stderr = log_file

# ========================================================
# IMPORT APLIKASI SECARA LANGSUNG (KUNCI SUKSES PYINSTALLER)
# ========================================================
from main import app  

def start_browser():
    """Membuka Chrome mode aplikasi setelah server siap"""
    time.sleep(3)
    os.system('start chrome --app=http://127.0.0.1:8010')

if __name__ == "__main__":
    multiprocessing.freeze_support()
    
    # Jalankan Chrome di thread terpisah
    threading.Thread(target=start_browser, daemon=True).start()

    # Jalankan Uvicorn menggunakan variabel 'app' langsung, BUKAN teks
    uvicorn.run(app, host="127.0.0.1", port=8010, reload=False)