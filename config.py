"""Загрузка настроек бота из переменных окружения.

На Amvera токен задаётся в разделе «Переменные и секреты» консоли
(переменная BOT_TOKEN), а не в .env. Файл .env (если положить) тоже
подхватится как ориентир для локального запуска.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Типизированные настройки aiogram-бота."""

    def __init__(self) -> None:
        self.bot_token: str = os.getenv("BOT_TOKEN", "").strip()
        self.girl_name: str = os.getenv("GIRL_NAME", "").strip() or "Алина"
        self.her_chat_id: int | None = self._parse_int("HER_CHAT_ID")

        self.proxy_url: str = os.getenv("PROXY_URL", "").strip()

        # Время утреннего комплимента (Europe/Moscow = MSK, UTC+3).
        self.morning_hour: int = self._parse_int("MORNING_HOUR", default=7) or 7
        self.morning_minute: int = self._parse_int("MORNING_MINUTE", default=0) or 0

        if not self.bot_token:
            sys.exit(
                "Не задан BOT_TOKEN. На Amvera добавь его в «Переменные и секреты» "
                "либо создай .env рядом с bot.py."
            )

    @staticmethod
    def _parse_int(name: str, default: int | None = None) -> int | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print(f"Предупреждение: поле {name} не является числом, игнорирую.")
            return default


settings = Settings()