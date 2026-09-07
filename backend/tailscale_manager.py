"""Menjaga Tailscale Funnel FPOS aktif tanpa menghambat server lokal."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence


TAILSCALE_CANDIDATES = (
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Tailscale\tailscale.exe"),
)
RETRY_SECONDS = (2, 5, 10, 30, 60)


def find_tailscale_exe() -> str | None:
    """Temukan CLI Tailscale dari lokasi instalasi Windows yang umum."""
    for path in TAILSCALE_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


def run_tailscale(executable: str, arguments: Sequence[str]) -> tuple[int, str, str]:
    """Jalankan CLI secara tersembunyi dan kembalikan hasil yang aman untuk log."""
    try:
        result = subprocess.run(
            [executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as error:
        return -1, "", str(error)


def enable_funnel_once(
    port: int,
    *,
    finder: Callable[[], str | None] = find_tailscale_exe,
    runner: Callable[[str, Sequence[str]], tuple[int, str, str]] = run_tailscale,
) -> tuple[bool, str]:
    """Coba aktifkan Funnel sekali; tidak pernah melempar error ke server FPOS."""
    executable = finder()
    if not executable:
        return False, "Tailscale belum terpasang atau tailscale.exe belum ditemukan."

    status_code, _, status_error = runner(executable, ("status",))
    if status_code != 0:
        detail = status_error or f"tailscale status keluar dengan kode {status_code}"
        return False, f"Tailscale belum siap/login: {detail}"

    code, output, error = runner(
        executable,
        ("funnel", "--bg", "--yes", str(port)),
    )
    combined = f"{output}\n{error}".lower()
    if code == 0 or "already exists" in combined:
        return True, output or "Tailscale Funnel aktif."

    detail = error or output or f"tailscale funnel keluar dengan kode {code}"
    return False, f"Gagal mengaktifkan Tailscale Funnel: {detail}"


def keep_funnel_active(
    port: int,
    *,
    retry_seconds: Sequence[float] = RETRY_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    enable_once: Callable[[int], tuple[bool, str]] = enable_funnel_once,
    max_attempts: int | None = None,
) -> bool:
    """Retry sampai Funnel aktif; dipanggil pada daemon thread saat produksi."""
    if os.environ.get("FPOS_SKIP_TAILSCALE") == "1":
        return True
    if not retry_seconds:
        raise ValueError("retry_seconds tidak boleh kosong")

    attempt = 0
    last_message = None
    while max_attempts is None or attempt < max_attempts:
        attempt += 1
        success, message = enable_once(port)
        if success:
            print(f"Tailscale Funnel aktif untuk FPOS port {port}.")
            if message and message != "Tailscale Funnel aktif.":
                print(message)
            return True

        # Hindari memenuhi error_log.txt dengan pesan identik setiap menit.
        if message != last_message or attempt == 1 or attempt % 10 == 0:
            print(f"Tailscale Funnel belum aktif (percobaan {attempt}): {message}")
            last_message = message

        if max_attempts is not None and attempt >= max_attempts:
            break
        delay = retry_seconds[min(attempt - 1, len(retry_seconds) - 1)]
        sleep(delay)

    return False


def start_funnel_manager(port: int) -> threading.Thread | None:
    """Mulai pengelola Funnel non-blocking untuk lifecycle proses FPOS."""
    if os.environ.get("FPOS_SKIP_TAILSCALE") == "1":
        return None
    worker = threading.Thread(
        target=keep_funnel_active,
        args=(port,),
        name="fpos-tailscale-funnel",
        daemon=True,
    )
    worker.start()
    return worker
