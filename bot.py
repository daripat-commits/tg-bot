# bot.py — aiogram v3, меню + пагинация + несколько файлов на одну кнопку
import os
import asyncio
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
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

# ---------- базовая настройка ----------
load_dotenv()
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # если не используешь проверку подписки — можно не задавать
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/")

# Формат FILE_CHOICES:
#   "Опора|./Опора.png; Замок|./Замок1.png,./Замок2.png; Мама|./мама.png"
FILE_CHOICES_RAW = os.getenv("FILE_CHOICES", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

# меню: 3 кнопки в ряд, 3 строки = 9 элементов на страницу
COLS = 3
ROWS = 3
PAGE_SIZE = COLS * ROWS

# aiogram объекты
bot: Bot | None = None
dp = Dispatcher()

# ---------- парсинг материалов ----------
# Храним как список пар: (название, [список_файлов])
# Пример элемента: ("Замок", ["./Замок1.png", "./Замок2.png"])
MATERIALS: List[Tuple[str, List[str]]] = []
if FILE_CHOICES_RAW.strip():
    for chunk in FILE_CHOICES_RAW.split(";"):
        part = chunk.strip()
        if not part:
            continue
        if "|" in part:
            title, paths_raw = part.split("|", 1)
            paths = [p.strip() for p in paths_raw.split(",") if p.strip()]
            if paths:
                MATERIALS.append((title.strip(), paths))
        else:
            # если без разделителя — считаем, что и заголовок, и путь совпадают
            p = part.strip()
            MATERIALS.append((p, [p]))

# fallback, если не задано
if not MATERIALS:
    default_path = os.getenv("FILE_PATH", "./material.pdf")
    MATERIALS = [("Материал", [default_path])]

# ---------- константы callback ----------
CB_VIEW = "view"          # открыть меню, стр. 0
CB_PAGE = "page:"         # page:{num}
CB_ITEM = "item:"         # item:{index}
CB_BACK = "back_main"     # назад к кнопке "Смотреть материалы"

# ---------- клавиатуры ----------
def make_after_check_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Смотреть материалы", callback_data=CB_VIEW)]
        ]
    )

def paginate_kb(items: List[Tuple[str, List[str]]], page: int) -> InlineKeyboardMarkup:
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)

    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []

    for idx in range(start, end):
        title, _paths = items[idx]
        row.append(InlineKeyboardButton(text=title, callback_data=f"{CB_ITEM}{idx}"))
        if len(row) == COLS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # навигация
    prev_page = page - 1
    next_page = page + 1
    rows.append([
        InlineKeyboardButton(text="◀️", callback_data=f"{CB_PAGE}{prev_page}" if page > 0 else "noop"),
        InlineKeyboardButton(text=f"Стр. {page+1}/{total_pages}", callback_data="noop"),
        InlineKeyboardButton(text="▶️", callback_data=f"{CB_PAGE}{next_page}" if page < total_pages - 1 else "noop"),
    ])

    # назад
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data=CB_BACK)])

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- хэндлеры ----------
@dp.message(CommandStart())
async def on_start(message: Message):
    # если нужна обязательная проверка подписки — добавь свою кнопку "Проверить подписку" и хэндлер,
    # здесь ради простоты сразу даём переход к материалам
    await message.answer(
        "Привет! 👋 Нажмите кнопку, чтобы открыть материалы.",
        reply_markup=make_after_check_kb()
    )

@dp.message(Command("help"))
async def on_help(message: Message):
    await message.answer("Нажмите «📚 Смотреть материалы», затем выберите пункт из меню. При нажатии бот пришлёт файл(ы).")

@dp.callback_query(F.data == CB_VIEW)
async def open_menu(callback: CallbackQuery):
    kb = paginate_kb(MATERIALS, page=0)
    await callback.message.answer("Выберите материал:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith(CB_PAGE))
async def change_page(callback: CallbackQuery):
    data = callback.data
    try:
        page = int(data.removeprefix(CB_PAGE))
    except ValueError:
        await callback.answer("Некорректная страница", show_alert=True)
        return
    kb = paginate_kb(MATERIALS, page)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        await callback.message.answer("Выберите материал:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == CB_BACK)
async def back_main(callback: CallbackQuery):
    await callback.message.answer("Готово. Нажмите «📚 Смотреть материалы».", reply_markup=make_after_check_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith(CB_ITEM))
async def send_material(callback: CallbackQuery):
    data = callback.data
    try:
        idx = int(data.removeprefix(CB_ITEM))
    except ValueError:
        await callback.answer("Некорректный выбор", show_alert=True)
        return

    if not (0 <= idx < len(MATERIALS)):
        await callback.answer("Пункт не найден", show_alert=True)
        return

    title, paths = MATERIALS[idx]
    sent_any = False
    for path in paths:
        if not os.path.exists(path):
            await callback.message.answer(f"❌ Файл не найден: {path}")
            continue
        try:
            await callback.message.answer_document(FSInputFile(path), caption=title if not sent_any else None)
            sent_any = True
        except Exception as e:
            logging.exception(f"Ошибка отправки {path}: {e}")
            await callback.message.answer(f"Не удалось отправить: {path}")

    await callback.answer("Готово" if sent_any else "Не найдено ни одного файла")

# (опционально) быстрая проверка конфигурации
@dp.message(Command("debug"))
async def debug(message: Message):
    lines = []
    for title, paths in MATERIALS:
        exists = [("OK" if os.path.exists(p) else "NO") + f" {p}" for p in paths]
        lines.append(f"{title}: " + " | ".join(exists))
    await message.answer("\n".join(lines) or "Список пуст")

# ---------- точка входа ----------
async def main():
    global bot
    # SSL + IPv4 (в основном актуально для Windows; на Render тоже ок)
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    session = AiohttpSession()
    session._connector_init = {
        "family": socket.AF_INET,
        "ttl_dns_cache": 300,
        "ssl": ssl_ctx,
    }

    bot = Bot(BOT_TOKEN, session=session)

    logger.info("Bot is running...")
    logger.info("Материалы: " + " | ".join([f"{t} -> {', '.join(p)}" for t, p in MATERIALS]))
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
