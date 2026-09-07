"""Prepare the persistent volume, drop privileges, then launch the server."""

import os
import pwd
import subprocess
import sys
from pathlib import Path


def main() -> None:
    data_dir = Path(os.environ.get("FPOS_LICENSE_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    account = pwd.getpwnam("fpos")
    if os.geteuid() == 0:
        for root, directories, files in os.walk(data_dir):
            os.chown(root, account.pw_uid, account.pw_gid)
            for name in directories + files:
                os.chown(Path(root) / name, account.pw_uid, account.pw_gid)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "/app/alembic.ini", "upgrade", "head"],
        check=True,
    )
    port = os.environ.get("PORT", "8080")
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "app.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--workers",
            "1",
            "--no-access-log",
        ],
    )


if __name__ == "__main__":
    main()
