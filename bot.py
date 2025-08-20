# bot.py
import asyncio
import os
import logging
import socket
import ssl
import certifi

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

# ----------------- базовая настройка -----------------
load_dotenv()
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/")
FILE_PATH = os.getenv("FILE_PATH", "./material.pdf")
# Формат: "Название1|./path1;Название2|./path2"
FILE_CHOICES_RAW = os.getenv("FILE_CHOICES", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Укажите его в .env")

# Глобальные объекты (бот создадим внутри main)
bot: Bot | None = None
dp = Dispatcher()

# ----------------- парсинг списка файлов -----------------
# Пример в .env:
# FILE_CHOICES=Материал|./material.pdf;Чек-лист|./checklist.pdf;Медитация|./audio.mp3
FILE_CHOICES: list[tuple[str, str]] = []
if FILE_CHOICES_RAW.strip():
    for chunk in FILE_CHOICES_RAW.split(";"):
        part = chunk.strip()
        if not part:
            continue
        if "|" in part:
            title, path = part.split("|", 1)
            FILE_CHOICES.append((title.strip(), path.strip()))
        else:
            FILE_CHOICES.append((part, part))

# Если список пуст — используем одиночный FILE_PATH
if not FILE_CHOICES:
    FILE_CHOICES = [("Материал", FILE_PATH)]

SEND_PREFIX = "send:"


# ----------------- клавиатуры -----------------
def make_main_kb() -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="Проверить подписку", callback_data="check_sub")],
        [InlineKeyboardButton(text="Подписаться на канал", url=CHANNEL_URL)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


# ----------------- утилиты -----------------
async def is_subscribed(user_id: int) -> bool:
    """Проверяем, состоит ли пользователь в канале."""
    global bot
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        status = getattr(member, "status", None)
        logger.info(f"get_chat_member → user_id={user_id}, status={status}")
        return status in {"member", "administrator", "creator"}
    except TelegramBadRequest as e:
        logger.error(f"get_chat_member error: {e}")
        return False


# ----------------- хэндлеры -----------------
@dp.message(CommandStart())
async def on_start(message: Message):
    text = (
        "Привет! 👋\n\n"
        "Я выдам материалы подписчикам канала.\n"
        "1) Нажмите ‘Проверить подписку’.\n"
        "2) Если всё ок — выберите, какой файл хотите получить."
    )
    await message.answer(text, reply_markup=make_main_kb())


@dp.message(Command("help"))
async def on_help(message: Message):
    await message.answer(
        "Нажмите «Проверить подписку». Если подписка подтверждена — выберите файл из списка.\n"
        "Если ещё не подписаны — подпишитесь на канал и проверьте снова."
    )


@dp.callback_query(F.data == "check_sub")
async def on_check_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"[check_sub] from user_id={user_id}")
    if await is_subscribed(user_id):
        # Если вариантов несколько — показываем меню выбора
        if len(FILE_CHOICES) > 1:
            kb_rows = []
            for idx, (title, _path) in enumerate(FILE_CHOICES):
                kb_rows.append([InlineKeyboardButton(text=title, callback_data=f"{SEND_PREFIX}{idx}")])
            kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
            await callback.message.answer("Отлично! Выберите, какой файл отправить:", reply_markup=kb)
            await callback.answer("Подписка подтверждена ✅")
            return

        # Иначе отправляем единственный файл
        title, path = FILE_CHOICES[0]
        if os.path.exists(path):
            try:
                doc = FSInputFile(path)
                await callback.message.answer_document(document=doc, caption=title)
                await callback.answer("Подписка подтверждена ✅")
                logger.info(f"Файл отправлен пользователю {user_id}: {path}")
            except Exception as e:
                logger.exception(f"Ошибка при отправке файла: {e}")
                await callback.message.answer("Не удалось отправить файл. Посмотри логи консоли.")
                await callback.answer()
        else:
            logger.error(f"Файл не найден: {path}")
            await callback.message.answer(f"Файл не найден: {path}")
            await callback.answer()
    else:
        # Просим подписаться и даём кнопку
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Подписаться на канал", url=CHANNEL_URL)],
                [InlineKeyboardButton(text="Проверил, я подписан ✅", callback_data="check_sub")],
            ]
        )
        await callback.message.answer(
            "Похоже, вы ещё не подписаны. Подпишитесь на канал и нажмите повторную проверку.",
            reply_markup=kb,
        )
        await callback.answer()


@dp.callback_query(F.data.startswith(SEND_PREFIX))
async def send_selected(callback: CallbackQuery):
    """Отправка выбранного файла по кнопке меню."""
    data = callback.data
    try:
        idx = int(data.removeprefix(SEND_PREFIX))
    except ValueError:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    if not (0 <= idx < len(FILE_CHOICES)):
        await callback.answer("Файл не найден", show_alert=True)
        return

    title, path = FILE_CHOICES[idx]
    if os.path.exists(path):
        try:
            doc = FSInputFile(path)
            await callback.message.answer_document(document=doc, caption=title)
            await callback.answer("Отправлено ✅")
            logger.info(f"Выбран и отправлен файл: {path}")
        except Exception as e:
            logger.exception(f"Ошибка при отправке файла {path}: {e}")
            await callback.message.answer("Не удалось отправить файл. Проверьте логи.")
            await callback.answer()
    else:
        await callback.message.answer(f"Файл не найден: {path}")
        await callback.answer()


@dp.message(Command("sendfile"))
async def sendfile(message: Message):
    """Тестовая команда — отправляет первый файл из списка."""
    title, path = FILE_CHOICES[0]
    if os.path.exists(path):
        doc = FSInputFile(path)
        await message.answer_document(document=doc, caption=title)
    else:
        await message.answer(f"Файл не найден: {path}")


# ----------------- точка входа -----------------
async def main():
    global bot

    # SSL-контекст на базе certifi (чтобы не падать из-за системного хранилища сертификатов)
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    # Создаём AiohttpSession и задаём параметры коннектора через _connector_init
    session = AiohttpSession()
    session._connector_init = {
        "family": socket.AF_INET,  # форсируем IPv4
        "ttl_dns_cache": 300,
        "ssl": ssl_ctx,            # используем certifi CA bundle
    }
    # При необходимости можно включить прокси:
    # PROXY_URL = os.getenv("PROXY_URL")  # например: "socks5://user:pass@host:port"
    # session.proxy = PROXY_URL

    bot = Bot(BOT_TOKEN, session=session)

    logger.info("Bot is running...")
    logger.info(f"CHANNEL_ID={CHANNEL_ID} | CHANNEL_URL={CHANNEL_URL} | FILE_PATH={FILE_PATH}")
    if FILE_CHOICES:
        logger.info("Доступные файлы: " + " | ".join([f"{t} -> {p}" for t, p in FILE_CHOICES]))

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
