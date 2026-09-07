"""Local client and enforcement state for the central FPOS license service."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..config import settings


STATE_DIR = Path("instance")
STATE_FILE = STATE_DIR / "central-license.json"
STATE_LOCK = threading.RLock()
OFFLINE_GRACE = timedelta(hours=max(1, settings.LICENSE_OFFLINE_GRACE_HOURS))
WITA = ZoneInfo("Asia/Makassar")
ALLOWED_WHEN_BLOCKED = (
    "/api/license/",
    "/api/auth/login",
    "/api/auth/me",
    "/health",
    "/login",
    "/settings",
    "/js/",
    "/css/",
    "/assets/",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "license_server_url": settings.LICENSE_SERVER_URL.rstrip("/"),
        "public_key_b64": settings.LICENSE_PUBLIC_KEY_B64,
        "fpos_url": "",
        "license_key": "",
        "installation_id": str(uuid.uuid4()),
        "installation_token": "",
        "last_successful_check_at": None,
        "last_check_date_wita": None,
        "decision": {"status": "not_configured"},
        "signature": "",
    }


def load_state() -> dict[str, Any]:
    with STATE_LOCK:
        state = default_state()
        try:
            stored = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                state.update(stored)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return state


def save_state(state: dict[str, Any]) -> None:
    with STATE_LOCK:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, STATE_FILE)
        try:
            os.chmod(STATE_FILE, 0o600)
        except OSError:
            pass


def hardware_fingerprint() -> str:
    raw = f"{uuid.getnode()}|{platform.system()}|{platform.machine()}|{socket.gethostname()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def device_payload(state: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "license_key": state["license_key"],
        "installation_id": state["installation_id"],
        "hostname": socket.gethostname(),
        "hardware_fingerprint": hardware_fingerprint(),
    }
    if state.get("fpos_url"):
        payload["fpos_url"] = state["fpos_url"]
    if state.get("installation_token"):
        payload["installation_token"] = state["installation_token"]
    return payload


def validate_server_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    parsed = urlparse(normalized)
    allowed_scheme = parsed.scheme == "https" or (
        settings.LICENSE_ALLOW_HTTP and parsed.scheme == "http"
    )
    if not allowed_scheme or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("URL lisensi wajib berupa alamat HTTPS yang valid")
    return normalized


def verify_signed_response(body: dict[str, Any], public_key_b64: str) -> dict[str, Any]:
    decision = body.get("decision")
    signature = body.get("signature")
    if not isinstance(decision, dict) or not isinstance(signature, str) or not public_key_b64:
        raise ValueError("Respons lisensi tidak memiliki tanda tangan yang lengkap")
    canonical = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode("utf-8")
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(public_key_b64))
        key.verify(base64.urlsafe_b64decode(signature), canonical)
    except (ValueError, InvalidSignature) as exc:
        raise ValueError("Tanda tangan keputusan lisensi tidak valid") from exc
    return decision


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "FPOS-License/1"},
        method="POST",
    )
    delays = (0, 5, 15, 30)
    last_error: Exception | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code not in {502, 503, 504}:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise ValueError(f"Server lisensi menolak permintaan ({exc.code}): {detail}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
    raise ConnectionError("Server lisensi belum dapat dihubungi") from last_error


def apply_response(
    state: dict[str, Any], body: dict[str, Any], *, mark_daily_check: bool = False
) -> dict[str, Any]:
    decision = verify_signed_response(body, state.get("public_key_b64", ""))
    if decision.get("license_key") != state.get("license_key"):
        raise ValueError("Keputusan diterbitkan untuk kode lisensi lain")
    if decision.get("installation_id") != state.get("installation_id"):
        raise ValueError("Keputusan diterbitkan untuk instalasi lain")

    server_time = parse_datetime(decision.get("server_time"))
    previous_time = parse_datetime((state.get("decision") or {}).get("server_time"))
    if server_time is None or (previous_time and server_time < previous_time - timedelta(minutes=5)):
        raise ValueError("Waktu keputusan lisensi tidak valid")

    if body.get("installation_token"):
        state["installation_token"] = body["installation_token"]
    state["decision"] = decision
    state["signature"] = body["signature"]
    state["last_successful_check_at"] = utc_now().isoformat()
    if mark_daily_check:
        state["last_check_date_wita"] = datetime.now(WITA).date().isoformat()
    save_state(state)
    if decision.get("status") == "blocked":
        disable_windows_autostart()
    return public_status(state)


def activate(license_key: str) -> dict[str, Any]:
    state = load_state()
    if not state.get("license_server_url") or not state.get("public_key_b64"):
        raise ValueError("URL dan public key server lisensi belum dikonfigurasi oleh Fieter")
    state["license_key"] = license_key.strip().upper()
    body = post_json(
        f"{state['license_server_url']}/api/v1/activate", device_payload(state)
    )
    return apply_response(state, body)


def check_now() -> dict[str, Any]:
    state = load_state()
    if not state.get("license_server_url"):
        return public_status(state)
    if not state.get("license_key") or not state.get("installation_token"):
        return public_status(state, override="setup_required")
    try:
        body = post_json(f"{state['license_server_url']}/api/v1/check", device_payload(state))
        return apply_response(state, body, mark_daily_check=True)
    except ConnectionError:
        return public_status(state)


def select_server(selected_installation_id: str, confirmation_hostname: str) -> dict[str, Any]:
    state = load_state()
    payload = device_payload(state) | {
        "selected_installation_id": selected_installation_id,
        "confirmation_hostname": confirmation_hostname,
    }
    body = post_json(f"{state['license_server_url']}/api/v1/select-server", payload)
    return apply_response(state, body)


def reclaim() -> dict[str, Any]:
    state = load_state()
    body = post_json(f"{state['license_server_url']}/api/v1/reclaim", device_payload(state))
    return apply_response(state, body)


def configure(license_server_url: str, public_key_b64: str, fpos_url: str) -> dict[str, Any]:
    state = load_state()
    state["license_server_url"] = validate_server_url(license_server_url)
    try:
        if len(base64.urlsafe_b64decode(public_key_b64)) != 32:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError("Public key Ed25519 tidak valid") from exc
    state["public_key_b64"] = public_key_b64.strip()
    normalized_fpos_url = fpos_url.strip().rstrip("/")
    if normalized_fpos_url:
        parsed_fpos = urlparse(normalized_fpos_url)
        if parsed_fpos.scheme not in {"http", "https"} or not parsed_fpos.netloc:
            raise ValueError("URL FPOS pelanggan tidak valid")
    state["fpos_url"] = normalized_fpos_url
    save_state(state)
    return public_status(state)


def public_status(state: dict[str, Any] | None = None, override: str | None = None) -> dict[str, Any]:
    state = state or load_state()
    decision = dict(state.get("decision") or {})
    status_value = override or decision.get("status", "not_configured")
    if state.get("license_server_url") and state.get("public_key_b64") and not state.get(
        "installation_token"
    ):
        status_value = "setup_required"
    last_success = parse_datetime(state.get("last_successful_check_at"))
    if (
        state.get("license_server_url")
        and state.get("license_key")
        and (last_success is None or utc_now() - last_success > OFFLINE_GRACE)
    ):
        status_value = "offline_blocked"
    return {
        "status": status_value,
        "configured": bool(state.get("license_server_url") and state.get("public_key_b64")),
        "activated": bool(state.get("license_key") and state.get("installation_token")),
        "license_key": state.get("license_key") or None,
        "installation_id": state.get("installation_id"),
        "hostname": socket.gethostname(),
        "license_server_url": state.get("license_server_url", ""),
        "fpos_url": state.get("fpos_url", ""),
        "last_successful_check_at": state.get("last_successful_check_at"),
        "blocked_until": decision.get("blocked_until"),
        "can_reclaim": bool(
            decision.get("can_reclaim")
            or (
                parse_datetime(decision.get("blocked_until"))
                and parse_datetime(decision.get("blocked_until")) <= utc_now()
            )
        ),
        "installations": decision.get("installations", []),
        "message": status_message(status_value),
    }


def status_message(status_value: str) -> str:
    return {
        "active": "Lisensi server aktif.",
        "conflict": "Kode lisensi terdeteksi pada lebih dari satu server.",
        "blocked": "Server ini tidak dipilih dan diblokir selama 24 jam.",
        "offline_blocked": "Validasi lisensi belum berhasil selama lebih dari tiga hari.",
        "setup_required": "Masukkan kode lisensi untuk mengaktifkan server ini.",
        "not_configured": "Server lisensi pusat belum dikonfigurasi oleh Fieter.",
    }.get(status_value, "Status lisensi belum diketahui.")


def is_business_blocked(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in ALLOWED_WHEN_BLOCKED):
        return False
    return public_status()["status"] in {
        "conflict",
        "blocked",
        "offline_blocked",
        "setup_required",
    }


def daily_check_due() -> bool:
    state = load_state()
    if not state.get("license_server_url") or not state.get("installation_token"):
        return False
    now = datetime.now(WITA)
    jitter_minute = int(hashlib.sha256(state["installation_id"].encode()).hexdigest()[:4], 16) % 11
    due = now.replace(hour=12, minute=jitter_minute, second=0, microsecond=0)
    return now >= due and state.get("last_check_date_wita") != now.date().isoformat()


def scheduler_loop() -> None:
    while True:
        try:
            if daily_check_due() or public_status()["status"] == "conflict":
                check_now()
        except Exception as exc:
            print(f"WARNING Pemeriksaan lisensi gagal: {exc}")
        time.sleep(60)


def disable_windows_autostart() -> None:
    if os.name != "nt":
        return
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, "EvaStore_FPOS")
            except FileNotFoundError:
                pass
    except OSError:
        pass
    for flag in (Path(".autostart_configured"), Path("..").resolve() / ".autostart_configured"):
        try:
            flag.unlink(missing_ok=True)
        except OSError:
            pass


def set_windows_autostart(enabled: bool) -> None:
    if os.name != "nt":
        raise ValueError("Pengaturan auto-start hanya tersedia di Windows")
    import sys
    import winreg

    command = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" "{Path("main.py").resolve()}"'
    path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, "EvaStore_FPOS", 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, "EvaStore_FPOS")
            except FileNotFoundError:
                pass
    flag = Path(".autostart_configured")
    if not getattr(sys, "frozen", False):
        flag = Path("..").resolve() / ".autostart_configured"
    flag.write_text("done", encoding="utf-8")
