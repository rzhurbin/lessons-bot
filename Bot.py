import os
import re
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# ========= НАСТРОЙКИ ЧЕРЕЗ ENV =========
BOT_TOKEN = os.getenv("BOT_TOKEN")              # токен из BotFather
TEACHER_ID = int(os.getenv("TEACHER_ID", "0")) # твой Telegram ID
SHEET_ID = os.getenv("SHEET_ID")                # ID Google Sheets (между /d/ и /edit)
GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", "credentials.json")

# адрес вебхука (Render даст https-домен)
WEBHOOK_BASE = os.getenv("WEBHOOK_URL")         # например: https://your-app.onrender.com
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret-path")
WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"

PORT = int(os.getenv("PORT", "10000"))  # Render сам прокинет порт

# ========= ПРОВЕРКИ =========
for var_name in ["BOT_TOKEN", "TEACHER_ID", "SHEET_ID", "WEBHOOK_URL"]:
    if not os.getenv(var_name):
        raise RuntimeError(f"ENV {var_name} is required")

# ========= GOOGLE SHEETS =========
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SHEET_ID)
sheet = sh.sheet1

# ========= TELEGRAM =========
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ========= ОБРАБОТЧИКИ =========
@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.reply("✅ Бот запущен и готов к работе!")

@dp.message(Command("id"))
async def get_id(message: Message):
    await message.reply(f"Твой Telegram ID: {message.from_user.id}")

@dp.message(Command("stats"))
@dp.message(Command("статистика"))
async def stats(message: Message):
    if message.from_user.id != TEACHER_ID:
        await message.reply("⛔ Команда доступна только преподавателю.")
        return

    try:
        records = sheet.get_all_records()
    except Exception:
        await message.reply("❌ Ошибка при чтении Google Sheets.")
        return

    if not records:
        await message.reply("Нет данных по ученикам.")
        return

    header = f"{'Ученик':25} | {'Пройдено':8} | {'Всего':5} | {'Осталось':8}"
    lines = [header, "-" * len(header)]

    for r in records:
        student = str(r.get("Student", ""))[:25]
        done = int(r.get("Done", 0) or 0)
        total = int(r.get("Total", 0) or 0)
        remain = max(total - done, 0)
        lines.append(f"{student:25} | {done:^8} | {total:^5} | {remain:^8}")

    text = "📊 Статистика по ученикам:\n\n" + "\n".join(lines)
    await message.reply(f"<pre>{text}</pre>", parse_mode="HTML")

# ========= ФУНКЦИЯ ОБНОВЛЕНИЯ ПРОГРЕССА =========
def update_progress(chat_id, student, done, total):
    try:
        records = sheet.get_all_records()
        for i, row in enumerate(records, start=2):  # первая строка — заголовки
            if str(row.get("ChatID")) == str(chat_id):
                sheet.update_cell(i, 3, str(done))   # Done
                sheet.update_cell(i, 4, str(total))  # Total
                sheet.update_cell(i, 5, datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                return
        # если не нашли — добавляем строку
        sheet.append_row([str(chat_id), student, str(done), str(total),
                          datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")])
    except Exception as e:
        print("Ошибка при работе с Google Sheets:", e)
        raise

# ========= ХЭНДЛЕР ПРОГРЕССА =========
@dp.message()
async def track_lessons(message: Message):
    if not message.text:
        return

    text = message.text.lower()
    match = re.search(r"урок\s+(\d+)\s+из\s+(\d+)", text)
    if not match:
        return

    done, total = map(int, match.groups())
    chat_id = message.chat.id

    if message.chat.type in ("group", "supergroup", "channel"):
        student = message.chat.title or f"chat_{chat_id}"
    else:
        user = message.from_user
        student = f"{user.full_name} (@{user.username})" if user else f"chat_{chat_id}"

    try:
        update_progress(chat_id, student, done, total)
    except Exception:
        await message.reply("Ошибка при сохранении в Google Sheets.")
        return

    if total - done == 1:
        try:
            await bot.send_message(
                TEACHER_ID,
                f"🔔 Напоминание: у {student} осталось 1 занятие из {total} (чат id {chat_id})."
            )
        except Exception as e:
            print("Не удалось отправить напоминание:", e)

    await message.reply(f"✅ Записал: {done} из {total}")

# ========= AIOHTTP + WEBHOOK =========
async def on_startup(app: web.Application):
    await bot.set_webhook(url=WEBHOOK_BASE + WEBHOOK_PATH, drop_pending_updates=True)
    print("Webhook set:", WEBHOOK_BASE + WEBHOOK_PATH)

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()

def build_app() -> web.Application:
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    async def health(_):
        return web.Response(text="OK")
    app.router.add_get("/", health)

    setup_application(app, dp, bot=bot)
    return app

if __name__ == "__main__":
    web.run_app(build_app(), port=PORT)
