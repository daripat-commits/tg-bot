# bot.py
import asyncio
import os
import logging
import socket
import ssl
import certifi
from typing import List, Tuple

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

# Параметры меню
COLS = 3                # кнопок в строке
ROWS = 3                # строк на страницу
PAGE_SIZE = COLS * ROWS

# Глобальные объекты (бот создадим внутри main)
bot: Bot | None = None
dp = Dispatcher()

# ----------------- парсинг списка файлов -----------------
# Пример в .env:
# FILE_CHOICES=Материал|./material.pdf;Чек-лист|./checklist.pdf;Медитация|./audio.mp3
FILE_CHOICES: List[Tuple[str, str]] = []
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

# ----------------- константы callback-данных -----------------
CB_VIEW = "view"             # открыть меню (страница 0)
CB_PAGE = "page:"            # открыть конкретную страницу: page:{num}
CB_ITEM = "item:"            # отправить конкретный материал: item:{index}
CB_BACK_MAIN = "back_main"   # вернуться к экрану с «Смотреть материалы»

# ----------------- клавиатуры -----------------
def make_main_kb() -> InlineKeyboardMarkup:
    """Основной экран со стартовой проверкой подписки."""
    kb = [
        [InlineKeyboardButton(text="Проверить подписку", callback_data="check_sub")],
        [InlineKeyboardButton(text="Подписаться на канал", url=CHANNEL_URL)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def make_after_check_kb() -> InlineKeyboardMarkup:
    """После успешной проверки — одна кнопка «Смотреть материалы»."""
    kb = [
        [InlineKeyboardButton(text="📚 Смотреть материалы", callback_data=CB_VIEW)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def paginate_kb(items: List[Tuple[str, str]], page: int) -> InlineKeyboardMarkup:
    """Меню материалов с пагинацией: 3 кнопки в ряд, PAGE_SIZE на страницу."""
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    slice_items = items[start:end]

    rows: List[List[InlineKeyboardButton]] = []
    # материал-кнопки (по COLS в строке)
    row: List[InlineKeyboardButton] = []
    for idx_global in range(start, end):
        title, _path = items[idx_global]
        row.append(InlineKeyboardButton(text=title, callback_data=f"{CB_ITEM}{idx_global}"))
        if len(row) == COLS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # пагинация
    nav_row: List[InlineKeyboardButton] = []

    prev_page = page - 1
    next_page = page + 1

    nav_row.append(
        InlineKeyboardButton(
            text="◀️ Назад" if page > 0 else "◀️",
            callback_data=f"{CB_PAGE}{prev_page}" if page > 0 else "noop"
        )
    )
    nav_row.append(
        InlineKeyboardButton(
            text=f"Стр. {page+1}/{total_pages}",
            callback_data="noop"
        )
    )
    nav_row.append(
        InlineKeyboardButton(
            text="Вперёд ▶️" if page < total_pages - 1 else "▶️",
            callback_data=f"{CB_PAGE}{next_page}" if page < total_pages - 1 else "noop"
        )
    )
    rows.append(nav_row)

    # кнопка назад к главному экрану
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data=CB_BACK_MAIN)])

    return InlineKeyboardMarkup(inline_keyboard=rows)

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
        "1) Нажмите «Проверить подписку».\n"
        "2) Если всё ок — станет доступна кнопка «Смотреть материалы»."
    )
    await message.answer(text, reply_markup=make_main_kb())

@dp.message(Command("help"))
async def on_help(message: Message):
    await message.answer(
        "Нажмите «Проверить подписку». Если подписка подтверждена — нажмите «Смотреть материалы».\n"
        "Дальше выберите нужный материал из меню (в 3 колонки, с переключением страниц)."
    )

@dp.callback_query(F.data == "check_sub")
async def on_check_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"[check_sub] from user_id={user_id}")
    if await is_subscribed(user_id):
        await callback.message.answer(
            "Подписка подтверждена ✅\nНажмите «Смотреть материалы», чтобы открыть меню.",
            reply_markup=make_after_check_kb()
        )
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

# Открыть меню материалов (страница 0)
@dp.callback_query(F.data == CB_VIEW)
async def open_materials(callback: CallbackQuery):
    kb = paginate_kb(FILE_CHOICES, page=0)
    await callback.message.answer("Выберите материал:", reply_markup=kb)
    await callback.answer()

# Переход по страницам
@dp.callback_query(F.data.startswith(CB_PAGE))
async def change_page(callback: CallbackQuery):
    data = callback.data
    try:
        page = int(data.removeprefix(CB_PAGE))
    except ValueError:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    kb = paginate_kb(FILE_CHOICES, page=page)
    # редактируем предыдущее сообщение, чтобы не плодить сообщения
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        # если редактировать нельзя (например, слишком старое сообщение) — отправим новое
        await callback.message.answer("Выберите материал:", reply_markup=kb)
    await callback.answer()

# Вернуться к экрану после проверки
@dp.callback_query(F.data == CB_BACK_MAIN)
async def back_main(callback: CallbackQuery):
    await callback.message.answer(
        "Готово. Нажмите «Смотреть материалы», чтобы открыть меню.",
        reply_markup=make_after_check_kb()
    )
    await callback.answer()

# Отправка выбранного материала
@dp.callback_query(F.data.startswith(CB_ITEM))
async def send_selected(callback: CallbackQuery):
    data = callback.data
    try:
        idx = int(data.removeprefix(CB_ITEM))
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

# Тестовая команда — отправляет первый файл
@dp.message(Command("sendfile"))
async def sendfile(message: Message):
    title, path = FILE_CHOICES[0]
    if os.path.exists(path):
        doc = FSInputFile(path)
        await message.answer_document(document=doc, caption=title)
    else:
        await message.answer(f"Файл не найден: {path}")

# ----------------- точка входа -----------------
async def main():
    global bot

    # SSL-контекст на базе certifi (исправляет ошибки валидации в Windows)
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
