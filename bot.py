import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from dotenv import load_dotenv

# Загружаем переменные
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@darizemlyanova")

# Файлы из переменной окружения
FILE_CHOICES_RAW = os.getenv("FILE_CHOICES", "")
# Пример: Опора|./Опора.png; Замок|./Замок1.png,./Замок2.png; Мама|./мама.png
FILE_CHOICES = {}
for part in FILE_CHOICES_RAW.split(";"):
    if "|" in part:
        name, path = part.split("|", 1)
        FILE_CHOICES[name.strip()] = [p.strip() for p in path.split(",") if p.strip()]

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Проверка
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Кнопки ---
def get_main_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("📚 Смотреть материалы", callback_data="show_materials"))
    return kb

def get_materials_keyboard(page: int = 0, per_page: int = 3):
    items = list(FILE_CHOICES.keys())
    start = page * per_page
    end = start + per_page
    current = items[start:end]

    kb = InlineKeyboardMarkup(row_width=3)
    for name in current:
        kb.insert(InlineKeyboardButton(name, callback_data=f"file|{name}"))

    # навигация
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"page|{page-1}"))
    if end < len(items):
        nav.append(InlineKeyboardButton("Вперёд ➡️", callback_data=f"page|{page+1}"))
    if nav:
        kb.row(*nav)

    return kb

# --- Хэндлеры ---
@dp.message(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer(
        "Привет! 👋\n\nЧтобы получить материалы, подпишись на канал и нажми кнопку ниже.",
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(lambda c: c.data == "show_materials")
async def show_materials(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Выбери материал из списка:",
        reply_markup=get_materials_keyboard()
    )

@dp.callback_query(lambda c: c.data.startswith("page|"))
async def change_page(callback: types.CallbackQuery):
    _, page = callback.data.split("|", 1)
    page = int(page)
    await callback.message.edit_reply_markup(get_materials_keyboard(page=page))

@dp.callback_query(lambda c: c.data.startswith("file|"))
async def send_file(callback: types.CallbackQuery):
    _, name = callback.data.split("|", 1)
    files = FILE_CHOICES.get(name, [])
    if not files:
        await callback.answer("Файл не найден", show_alert=True)
        return

    for path in files:
        if not os.path.exists(path):
            await callback.message.answer(f"❌ Файл {path} не найден на сервере.")
            continue
        doc = FSInputFile(path)
        await callback.message.answer_document(doc)

    await callback.answer()  # закрываем «часики»

# --- Запуск ---
async def main():
    logger.info("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
