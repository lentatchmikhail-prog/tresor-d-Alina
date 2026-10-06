"""Douceurs d'Alina — бот нежных комплиментов на aiogram.

Возможности:
- кнопка «Хочу!» — случайный персонализированный комплимент (без повторов);
- кнопка «Сколько мы вместе?» — счётчик дней с 10.09.2026;
- умные ответы на обычные фразы (привет, как дела, спокойной ночи и т.д.);
- эффект «печатает…» перед ответом;
- иногда — случайная лёгкая нота вместо сухого ответа;
- форматы подачи: иногда короткая эмодзи-фраза вместо длинного текста;
- утренний комплимент каждый день в 7:00 МСК.

Deploy на Bothost / Amvera: нужен только BOT_TOKEN; state.json пишется в
постоянное хранилище /app/data (или /data), не пропадает при пересборке.
"""

import asyncio
import json
import logging
import random
from datetime import date, datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
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

# Кнопки reply-клавиатуры.
KEY_WANT = "Хочу!"
KEY_DAYS = "Сколько мы вместе?"

# От какой даты считаем дни вместе.
START_DATE = date(2026, 9, 10)


def _pick_state_dir() -> Path:
    """Возвращает папку для state.json.

    Порядок: /app/data (Bothost) -> /data (прочие с volume) -> рядом с кодом.
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
    """Закреплённая reply-клавиатура внизу."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=KEY_WANT)],
            [KeyboardButton(text=KEY_DAYS)],
        ],
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


async def _typing(bot: Bot, chat_id: int, seconds: float = 0.7) -> None:
    """Эффект «печатает…» перед ответом (фича 2)."""
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(seconds)


# --- Умные ответы на обычные фразы (фича 1) ---
SMART: list[tuple[tuple[str, ...], str]] = [
    (("доброе утро", "с добрым утром"),
     "Доброе утро, Алиночка! Пусть день будет мягким, как ты 🌅"),
    (("добрый вечер", "спокойной ночи", "баю", "ложусь спать", "буду спать"),
     "Спокойной ночи, Алиночка. Сладких снов — обнимаю 💛"),
    (("как дела", "как ты", "как настроение", "ты как", "что делаешь"),
     "Когда рядом ты — всё хорошо, Алиночка. А ты как? 💗"),
    (("скучно", "грустно", "тоскливо", "плохо мне", "устала", "грущу"),
     "Алиночка, ты не одна. Я рядом, всё наладится — держись, я с тобой 💛"),
    (("люблю тебя", "я тебя люблю"),
     "И я тебя люблю, Алиночка. Сильно-сильно 💗"),
    (("привет", "здравствуй", "хай", "hello", "hey", "добрый день"),
     "Привет, Алиночка! Рада, что ты написала 💗"),
    (("спасибо", "благодарю"),
     "Всегда пожалуйста, Алиночка 💗"),
    (("пойдём", "свидание", "встретимся"),
     "Конечно, Алиночка! Я правда этого жду 🌹"),
]

# --- Случайные лёгкие ноты (фича 3) ---
GENTLE_NOTES: list[str] = [
    "Просто так: ты сегодня особенно милая, Алиночка 💛",
    "Решил написать без повода — у меня всё хорошо, ведь ты рядом 🌸",
    "Заглянул, пока ты писала: думаю о тебе. Всё хорошо 💗",
    "Маленькое «я здесь» для тебя, Алиночка 🎀",
    "Ты что-то набирала — а я уже улыбнулся. Спасибо, Алиночка 💌",
]

# --- Короткие эмодзи-формы (фича 8) ---
SHORT_FORMS: list[str] = [
    "💗",
    "Алиночка, ты — моё сейчас. 🌸",
    "Поймал мысль о тебе и делюсь: 💛",
    "Коротко и честно: ты важна. 🎀",
    "Имя: Алина. Факт: любима. 💌",
    "Простое напоминание: я рядом. ✨",
]


def _day_word(n: int) -> str:
    n10, n100 = n % 10, n % 100
    if 11 <= n100 <= 14:
        return "дней"
    if n10 == 1:
        return "день"
    if 2 <= n10 <= 4:
        return "дня"
    return "дней"


async def send_compliment(bot: Bot, chat_id: int) -> None:
    """Отправляет комплимент: иногда короткой эмодзи-формой (фича 8)."""
    await _typing(bot, chat_id, 0.8)
    if random.random() < 0.22:
        text = random.choice(SHORT_FORMS)
    else:
        text = format_for_her(pick_compliment())
    await bot.send_message(chat_id=chat_id, text=text)


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
    @dp.message(F.text == KEY_WANT)
    async def on_want(message: Message, bot: Bot) -> None:
        save_target_chat_id(message.chat.id)
        await send_compliment(bot, message.chat.id)

    # Кнопка «Сколько мы вместе?» — счётчик дней (фича 4).
    @dp.message(F.text == KEY_DAYS)
    async def on_days(message: Message, bot: Bot) -> None:
        save_target_chat_id(message.chat.id)
        days = (date.today() - START_DATE).days
        await _typing(bot, message.chat.id, 0.6)
        if days <= 0:
            await message.answer("Мы вместе с 10 сентября 2026 🌷")
        else:
            await message.answer(f"Мы вместе уже {days} {_day_word(days)} 💗")

    # Стартовая команда.
    @dp.message(CommandStart())
    async def on_start(message: Message, bot: Bot) -> None:
        save_target_chat_id(message.chat.id)
        await _typing(bot, message.chat.id, 0.4)
        await message.answer(
            "Привет, Алиночка! 💗\n"
            "Я создан, чтобы каждый день напоминать тебе, как ты важна.\n"
            "Жми «Хочу!» — получишь комплимент.\n"
            "А «Сколько мы вместе?» посчитает наши дни 💛",
            reply_markup=build_keyboard(),
        )

    # Любой другой текст: умный ответ, случайная нота или «не знаю команды».
    @dp.message(F.text)
    async def on_anything(message: Message) -> None:
        text = (message.text or "").strip().lower()
        if text.startswith("/"):
            return
        save_target_chat_id(message.chat.id)

        for keywords, reply in SMART:
            if any(k in text for k in keywords):
                await _typing(message.bot, message.chat.id, 0.5)
                await message.answer(reply)
                return

        if random.random() < 0.15:
            await _typing(message.bot, message.chat.id, 0.5)
            await message.answer(random.choice(GENTLE_NOTES))
            return

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