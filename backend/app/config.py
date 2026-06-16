import secrets
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite:///./ipos.db"

    # Security
    # JANGAN taruh nilai rahasia di sini. SECRET_KEY di-resolve per-instalasi
    # di bawah (_resolve_secret_key) menjadi kunci unik per mesin.
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # ── Network / Tailscale ───────────────────────────────────────────────────
    # False = tailscale "serve"  → HANYA device di tailnet yang sama (AMAN, default)
    # True  = tailscale "funnel" → TERBUKA ke internet publik (pakai hanya jika perlu)
    TAILSCALE_PUBLIC: bool = False

    # App
    APP_NAME: str = "iPos 5.0"
    APP_VERSION: str = "5.0.0"

    # CORS
    ALLOWED_ORIGINS: str = "*"

    # ── AI Providers ──────────────────────────────────────────────────────────
    # Priority: Gemini → Groq → OpenRouter (free >70B)

    # 1. Google Gemini — https://aistudio.google.com/app/apikey (gratis)
    GEMINI_API_KEY: str = "your_gemini_api_key_here"

    # 2. Groq — https://console.groq.com (gratis, cepat)
    GROQ_API_KEY: str = "your_groq_api_key_here"

    # 3. OpenRouter — https://openrouter.ai/keys (opsional, fallback)
    OPENROUTER_API_KEY: str = "your_openrouter_api_key_here"

    # ── Email & Drive Backup ──────────────────────────────────────────────────
    # SMTP tetap ada sebagai cadangan jika dibutuhkan fitur email lain
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    BACKUP_EMAIL: str = ""
    
    # 👇 INI KUNCINYA: Variabel untuk Google Drive 👇
    DRIVE_FOLDER_ID: str = ""
    DRIVE_BACKUP_ENABLED: bool = True
    AUTO_BACKUP_HOUR: int = 1  # Sudah saya set ke jam 1 pagi sesuai mau Anda
    EMAIL_BACKUP_ENABLED: bool = False # Kita matikan yang email karena pakai Drive

    # ── Telegram Notifications ───────────────────────────────────────────────
    # Setup: @BotFather di Telegram → buat bot → copy token
    # Chat ID: chat ke @userinfobot atau @getidsbot
    TELEGRAM_BOT_TOKEN: str = "your_telegram_bot_token_here"
    TELEGRAM_CHAT_ID: str = "your_telegram_chat_id_here"

    # ── Update System ─────────────────────────────────────────────────────────
    UPDATE_CHECK_URL: str = "https://raw.githubusercontent.com/YOURUSERNAME/ipos-releases/main/version.json"

    class Config:
        env_file = ".env"
        extra = "ignore"


# Nilai SECRET_KEY lama yang pernah hardcoded / bocor di git — wajib diganti otomatis.
_INSECURE_SECRETS = {
    "",
    "ipos-super-secret-key-change-in-production-2025",
    "your_secret_key_here",
}
# Disimpan satu folder dengan ipos.db (CWD = BASE_DIR setelah os.chdir), di luar git.
_SECRET_KEY_FILE = Path("secret.key")


def _resolve_secret_key(configured: str) -> str:
    """Pastikan tiap instalasi punya SECRET_KEY unik & rahasia.

    Jika SECRET_KEY belum diisi (atau masih nilai default lama yang sudah bocor),
    generate kunci acak SEKALI lalu simpan ke `secret.key` (tidak ikut git) agar
    konsisten antar-restart. Hasil: JWT satu client tidak berlaku di client lain
    dan tidak bisa ditebak.
    """
    if configured and configured not in _INSECURE_SECRETS:
        return configured
    try:
        if _SECRET_KEY_FILE.exists():
            existing = _SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        new_key = secrets.token_urlsafe(48)
        _SECRET_KEY_FILE.write_text(new_key, encoding="utf-8")
        return new_key
    except Exception:
        # Fallback terakhir: kunci ephemeral (token invalid saat restart,
        # tapi TIDAK PERNAH memakai kunci default yang bisa ditebak).
        return secrets.token_urlsafe(48)


settings = Settings()
settings.SECRET_KEY = _resolve_secret_key(settings.SECRET_KEY)