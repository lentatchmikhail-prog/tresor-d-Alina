"""Douceurs d'Alina — бот нежных комплиментов на aiogram.

Deploy на Amvera (cloud.amvera.ru):
- работает через Telegram Bot API, нужен только BOT_TOKEN;
- токен задаётся в консоли Amvera: «Переменные и секреты» -> BOT_TOKEN;
- утренний комплимент: каждый день в 7:00 МСК (Asia / Europe/Moscow);
- state.json пишется в /data (постоянное хранилище Amvera), не пропадает
  при пересборке.

Возможности:
- кнопка «Комплимент» — случайный персонализированный комплимент;
- каждое утро в 7:00 МСК — принудительный комплимент.
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from compliments import all_compliments
from config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("douceurs")

# Индексы комплиментов, уже ушедших в текущем «раунде» (чтобы без повторов).
_sent_indexes: set[int] = set()


def _pick_state_dir() -> Path:
    """Возвращает папку для state.json.

    Порядок: /app/data (постоянное хранилище Bothost) -> /data (прочие
    хостинги с volume) -> рядом с кодом (локальный запуск).
    """
    for candidate in ("/app/data", "/data"):
        data = Path(candidate)
        if data.is_dir():
            probe = data / ".write_test"
            try:
                probe.touch()
                probe.unlink()
                return data
            except OSError:
                continue
    return Path(__file__).resolve().parent


STATE_FILE = _pick_state_dir() / "state.json"


def load_target_chat_id() -> int | None:
    """Читает последний chat_id, куда отправляли утренний комплимент."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        chat_id = data.get("last_chat_id")
        return int(chat_id) if chat_id else None
    except (OSError, ValueError, TypeError):
        return None


def save_target_chat_id(chat_id: int) -> None:
    """Запоминает chat_id, чтобы утренний комплимент находил её после рестарта."""
    STATE_FILE.write_text(
        json.dumps({"last_chat_id": chat_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_keyboard() -> ReplyKeyboardMarkup:
    """Закреплённая reply-клавиатура внизу, кнопка «Хочу!»."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Хочу!")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def pick_compliment() -> str:
    """Возвращает комплимент без повтора, пока не разойдутся все."""
    pool = all_compliments()
    remaining = [i for i in range(len(pool)) if i not in _sent_indexes]
    if not remaining:
        _sent_indexes.clear()
        remaining = sorted(range(len(pool)), key=lambda _: random.random())

    idx = random.choice(remaining)
    _sent_indexes.add(idx)
    return pool[idx].format(name=settings.girl_name)


def format_for_her(compliment: str) -> str:
    """Форматирует комплимент с подписью бота."""
    return f"{compliment}\n\n— Douceurs d'Alina ✨"


async def send_compliment(bot: Bot, chat_id: int) -> None:
    """Отправляет комплимент в чат."""
    await bot.send_message(chat_id=chat_id, text=format_for_her(pick_compliment()))


async def send_morning_compliment(bot: Bot) -> None:
    """Отправляет утренний комплимент тому, кого запомнили (или из настроек)."""
    chat_id = settings.her_chat_id or load_target_chat_id()
    log.info("Утренний комплимент -> chat_id=%s в %s", chat_id,
             datetime.now(timezone.utc).isoformat(timespec="seconds"))
    if chat_id:
        try:
            await send_compliment(bot, chat_id)
        except Exception:
            log.exception("Не удалось отправить утренний комплимент.")


async def main() -> None:
    if settings.her_chat_id:
        save_target_chat_id(settings.her_chat_id)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Кнопка «Хочу!» — получить комплимент.
    @dp.message(F.text == "Хочу!")
    async def on_want(message: Message, bot: Bot) -> None:
        save_target_chat_id(message.chat.id)
        await send_compliment(bot, message.chat.id)

    # Стартовая команда.
    @dp.message(CommandStart())
    async def on_start(message: Message, bot: Bot) -> None:
        save_target_chat_id(message.chat.id)
        await message.answer(
            "Привет, Алиночка! 💗\n"
            "Этот бот создан, чтобы каждый день напоминать тебе, как ты важна.\n"
            "Жми кнопку - и получай ещё один кусочек нежности",
            reply_markup=build_keyboard(),
        )

    # Любой другой текст — «не знаю такой команды».
    @dp.message(F.text)
    async def on_unknown(message: Message) -> None:
        if message.text.startswith("/"):
            return
        save_target_chat_id(message.chat.id)
        await message.answer("Алиночка, я не знаю такой команды")

    # Планировщик утреннего комплимента (7:00 МСК, каждый день).
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        send_morning_compliment,
        CronTrigger(hour=settings.morning_hour, minute=settings.morning_minute),
        args=[bot],
        id="morning_compliment",
        replace_existing=True,
    )
    scheduler.start()

    log.info("Бот Douceurs d'Alina запущен. Утренний комплимент: %02d:%02d МСК",
             settings.morning_hour, settings.morning_minute)
    log.info("state.json -> %s", STATE_FILE)

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен.")