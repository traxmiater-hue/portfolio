"""Telegram-бот-консультант на нейросети (OpenAI или Gemini — переключается в .env).

- отвечает только по теме компании: инструкция и база знаний в knowledge.md;
- помнит последние сообщения диалога;
- лимит запросов на пользователя в сутки;
- кнопка «Позвать менеджера» — диалог пересылается в группу менеджеров, ответ менеджера бот вернёт клиенту;
- журнал вопросов и ответов в SQLite, уведомление о новых пользователях.

    pip install -r requirements.txt
    BOT_TOKEN=... OPENAI_API_KEY=... MANAGERS_CHAT_ID=... python bot.py
"""
import logging
import os
import sqlite3
from datetime import date
from pathlib import Path

from openai import AsyncOpenAI
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
ROOT = Path(__file__).parent
MANAGERS = int(os.getenv("MANAGERS_CHAT_ID", "0"))
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "30"))
MODEL = os.getenv("MODEL", "gpt-4o-mini")
HISTORY = 10                                               # сколько последних сообщений помнит бот
llm = AsyncOpenAI(base_url=os.getenv("API_BASE_URL"))      # для Gemini — OpenAI-совместимый адрес Google
db = sqlite3.connect(ROOT / "log.db", check_same_thread=False)
db.executescript("""CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, user_id INT, day TEXT, q TEXT, a TEXT);
                    CREATE TABLE IF NOT EXISTS users(id INT PRIMARY KEY, name TEXT, first_seen TEXT);
                    CREATE TABLE IF NOT EXISTS relay(msg_id INT PRIMARY KEY, user_id INT);""")

SYSTEM = (f"Ты — вежливый консультант компании. Отвечай кратко (до 5 предложений), по-русски, только по теме "
          f"компании и только по фактам из базы знаний ниже. Если ответа нет в базе или вопрос не по теме — "
          f"скажи об этом и предложи нажать «Позвать менеджера». Не выдумывай цены и сроки.\n\n"
          f"БАЗА ЗНАНИЙ:\n{(ROOT / 'knowledge.md').read_text(encoding='utf-8')}")
KB = M([[B("👩‍💼 Позвать менеджера", callback_data="manager")]])


def used_today(uid: int) -> int:
    return db.execute("SELECT COUNT(*) FROM log WHERE user_id=? AND day=?", (uid, date.today().isoformat())).fetchone()[0]


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if db.execute("INSERT OR IGNORE INTO users VALUES(?,?,?)", (u.id, u.full_name, date.today().isoformat())).rowcount:
        db.commit()
        if MANAGERS:
            await ctx.bot.send_message(MANAGERS, f"👋 Новый пользователь: {u.full_name} (id {u.id})")
    await update.message.reply_text("Здравствуйте! Я онлайн-консультант. Задайте вопрос — отвечу сразу, "
                                    "а если нужно, подключу менеджера.", reply_markup=KB)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u, text = update.effective_user, update.message.text.strip()
    if update.effective_chat.id == MANAGERS:                 # ответ менеджера реплаем → клиенту
        if update.message.reply_to_message:
            row = db.execute("SELECT user_id FROM relay WHERE msg_id=?",
                             (update.message.reply_to_message.message_id,)).fetchone()
            if row:
                await ctx.bot.send_message(row[0], f"👩‍💼 Менеджер: {text}")
        return
    if used_today(u.id) >= DAILY_LIMIT:
        await update.message.reply_text("На сегодня лимит вопросов исчерпан. Нажмите «Позвать менеджера».",
                                        reply_markup=KB)
        return
    hist = ctx.user_data.setdefault("history", [])
    hist.append({"role": "user", "content": text[:2000]})
    await ctx.bot.send_chat_action(u.id, ChatAction.TYPING)
    try:
        r = await llm.chat.completions.create(model=MODEL, temperature=0.3, max_tokens=500,
                                              messages=[{"role": "system", "content": SYSTEM}] + hist[-HISTORY:])
        answer = r.choices[0].message.content.strip()
    except Exception:
        logging.exception("LLM")
        answer = "Не получилось ответить прямо сейчас — позову менеджера, если нужно."
    hist.append({"role": "assistant", "content": answer})
    db.execute("INSERT INTO log(user_id, day, q, a) VALUES(?,?,?,?)", (u.id, date.today().isoformat(), text, answer))
    db.commit()
    await update.message.reply_text(answer, reply_markup=KB)


async def call_manager(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q, u = update.callback_query, update.effective_user
    await q.answer()
    last = "\n".join(f"{'Клиент' if m['role'] == 'user' else 'Бот'}: {m['content']}"
                     for m in ctx.user_data.get("history", [])[-6:]) or "—"
    if MANAGERS:
        msg = await ctx.bot.send_message(MANAGERS, f"🔔 {u.full_name} просит менеджера.\n\n{last}\n\n"
                                                   f"↩️ Ответьте реплаем на это сообщение — бот передаст клиенту.")
        db.execute("INSERT OR REPLACE INTO relay VALUES(?,?)", (msg.message_id, u.id))
        db.commit()
    await q.message.reply_text("Позвала менеджера — он ответит здесь в рабочее время (10:00–19:00).")


def main():
    app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(call_manager, pattern="^manager$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
