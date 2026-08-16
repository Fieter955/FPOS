"""External Windows updater for FPOS.

This file intentionally uses only the standard library so it can be shipped as
a tiny one-file PyInstaller executable beside FPOS.exe.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from update_common import CODE_PATHS, extract_update_archive, install_dir, make_temp_dir


def write_state(root: Path, status: str, **extra: object) -> None:
    payload = {"status": status, "updated_at": datetime.now().isoformat(timespec="seconds"), **extra}
    path = root / ".update-state.json"
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)


def process_exists(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def wait_for_process_exit(pid: int, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while process_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    return not process_exists(pid)


def start_app(root: Path) -> subprocess.Popen[bytes]:
    executable = root / "FPOS.exe"
    if not executable.is_file():
        raise RuntimeError("FPOS.exe tidak ditemukan")
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    return subprocess.Popen(
        [str(executable)],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )


def wait_for_health(expected_version: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    url = "http://127.0.0.1:8010/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status != 200:
                    time.sleep(0.5)
                    continue
                payload = json.loads(response.read().decode("utf-8"))
                return str(payload.get("version", "")) == expected_version
        except Exception:
            time.sleep(0.5)
    return False


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def remove_code(root: Path) -> None:
    for name in CODE_PATHS:
        target = root / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def move_code(source_root: Path, destination_root: Path) -> None:
    for name in CODE_PATHS:
        source = source_root / name
        destination = destination_root / name
        if not source.exists():
            raise RuntimeError(f"Paket update tidak memiliki {name}")
        shutil.move(str(source), str(destination))


def restore_code(root: Path, rollback_root: Path) -> None:
    remove_code(root)
    for name in CODE_PATHS:
        source = rollback_root / name
        if source.exists():
            shutil.move(str(source), str(root / name))


def run(args: argparse.Namespace) -> int:
    root = Path(args.install_dir).resolve()
    archive = Path(args.archive).resolve()
    expected_version = str(args.version)
    backup_path = str(args.backup_path or "")
    if root != install_dir().resolve() and getattr(sys, "frozen", False):
        # A packaged updater only accepts the installation directory that shipped it.
        raise RuntimeError("Lokasi instalasi updater tidak cocok")
    if not archive.is_file() or not root.is_dir():
        raise RuntimeError("Lokasi instalasi atau paket update tidak ditemukan")

    write_state(root, "waiting_for_shutdown", version=expected_version, backup_path=backup_path)
    if not wait_for_process_exit(int(args.pid)):
        raise RuntimeError("FPOS masih berjalan setelah menunggu 90 detik")

    staging = make_temp_dir(root, prefix=".fpos-updater-staging-")
    rollback = root / ".update-rollback" / f"{expected_version}-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    previous_process: subprocess.Popen[bytes] | None = None
    try:
        write_state(root, "installing", version=expected_version)
        package_root = extract_update_archive(archive, staging)
        rollback.mkdir(parents=True, exist_ok=False)
        for name in CODE_PATHS:
            current = root / name
            if not current.exists():
                raise RuntimeError(f"Instalasi lama tidak memiliki {name}")
            shutil.move(str(current), str(rollback / name))
        move_code(package_root, root)

        previous_process = start_app(root)
        if not wait_for_health(expected_version):
            raise RuntimeError("Versi baru gagal melewati health check")

        shutil.rmtree(rollback, ignore_errors=True)
        shutil.rmtree(archive.parent, ignore_errors=True)
        write_state(root, "completed", version=expected_version)
        return 0
    except Exception as exc:
        stop_process(previous_process)
        try:
            if rollback.exists() and any(rollback.iterdir()):
                restore_code(root, rollback)
                old_process = start_app(root)
                # Do not block rollback on health check; the old package is restored.
                _ = old_process
            write_state(root, "rolled_back", version=expected_version, error=str(exc))
        except Exception as restore_exc:
            write_state(root, "failed", version=expected_version, error=f"{exc}; rollback gagal: {restore_exc}")
        return 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            (root / ".update.lock").unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-dir", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--version", required=True)
    parser.add_argument("--backup-path", default="")
    args = parser.parse_args()
    try:
        return run(args)
    except Exception as exc:
        try:
            write_state(Path(args.install_dir).resolve(), "failed", version=args.version, error=str(exc))
            (Path(args.install_dir).resolve() / ".update.lock").unlink(missing_ok=True)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
