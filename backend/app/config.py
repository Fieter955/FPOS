from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite:///./ipos.db"

    # Security
    SECRET_KEY: str = "ipos-super-secret-key-change-in-production-2025"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

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

    # ── Email Backup ──────────────────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    BACKUP_EMAIL: str = ""
    AUTO_BACKUP_HOUR: int = 21
    EMAIL_BACKUP_ENABLED: bool = False

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


settings = Settings()
