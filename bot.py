# bot.py — aiogram v3, меню + пагинация + несколько файлов
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

# ---------- загрузка .env ----------
load_dotenv()
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
FILE_CHOICES_RAW = os.getenv("FILE_CHOICES", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

# ---------- материалы ----------
# Формат в .env:
# FILE_CHOICES=Опора|./Опора.png; Замок|./Замок1.png,./Замок2.png; Мама|./мама.png
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
            MATERIALS.append((part, [part]))

if not MATERIALS:
    MATERIALS = [("Материал", ["./material.pdf"])]

# ---------- параметры меню ----------
COLS = 3
ROWS = 3
PAGE_SIZE = COLS * ROWS

# ---------- callback-метки ----------
CB_VIEW = "view"
CB_PAGE = "page:"
CB_ITEM = "item:"
CB_BACK = "back_main"

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
        title, _ = items[idx]
        row.append(InlineKeyboardButton(text=title, callback_data=f"{CB_ITEM}{idx}"))
        if len(row) == COLS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # навигация
    rows.append([
        InlineKeyboardButton(text="◀️", callback_data=f"{CB_PAGE}{page-1}" if page > 0 else "noop"),
        InlineKeyboardButton(text=f"Стр. {page+1}/{total_pages}", callback_data="noop"),
        InlineKeyboardButton(text="▶️", callback_data=f"{CB_PAGE}{page+1}" if page < total_pages-1 else "noop"),
    ])
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data=CB_BACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- aiogram объекты ----------
bot: Bot | None = None
dp = Dispatcher()

# ---------- хэндлеры ----------
@dp.message(CommandStart())
async def on_start(message: Message):
    await message.answer(
        "Привет! 👋 Нажми кнопку, чтобы открыть материалы.",
        reply_markup=make_after_check_kb()
    )

@dp.message(Command("help"))
async def on_help(message: Message):
    await message.answer("Нажмите «📚 Смотреть материалы», выберите пункт и бот пришлёт файл(ы).")

@dp.callback_query(F.data == CB_VIEW)
async def open_menu(callback: CallbackQuery):
    kb = paginate_kb(MATERIALS, page=0)
    await callback.message.answer("Выберите материал:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith(CB_PAGE))
async def change_page(callback: CallbackQuery):
    try:
        page = int(callback.data.removeprefix(CB_PAGE))
    except ValueError:
        await callback.answer("Ошибка страницы", show_alert=True)
        return
    kb = paginate_kb(MATERIALS, page)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        await callback.message.answer("Выберите материал:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data == CB_BACK)
async def back_main(callback: CallbackQuery):
    await callback.message.answer("Нажмите «📚 Смотреть материалы».", reply_markup=make_after_check_kb())
    await callback.answer()

@dp.callback_query(F.data.startswith(CB_ITEM))
async def send_material(callback: CallbackQuery):
    try:
        idx = int(callback.data.removeprefix(CB_ITEM))
    except ValueError:
        await callback.answer("Ошибка выбора", show_alert=True)
        return
    if not (0 <= idx < len(MATERIALS)):
        await callback.answer("Материал не найден", show_alert=True)
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

    await callback.answer("Готово" if sent_any else "Файлы не найдены")

# ---------- точка входа ----------
async def main():
    global bot
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    session = AiohttpSession()
    session._connector_init = {
        "family": socket.AF_INET,
        "ttl_dns_cache": 300,
        "ssl": ssl_ctx,
    }

    bot = Bot(BOT_TOKEN, session=session)

    me = await bot.me()
    logger.info(f"Запущен бот @{me.username} (id={me.id})")

    # ВАЖНО: убрать вебхук и очистить старые апдейты
    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
