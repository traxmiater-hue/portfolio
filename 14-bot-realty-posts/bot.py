"""Бот публикует объявления о квартирах в группу/канал.

Владелец присылает фото/видео (можно альбомом) и описание как есть → бот приводит текст к шаблону
(parse.py), показывает превью с кнопками «Опубликовать / Исправить текст / Отмена» и только после
подтверждения выкладывает пост с медиа в группу. Бот должен быть администратором группы.

    BOT_TOKEN=... OWNER_ID=... TARGET_CHAT=@my_group python bot.py
"""
import asyncio
import logging
import os

from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from parse import parse, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
OWNER = int(os.environ.get("OWNER_ID", "0"))
TARGET = os.environ.get("TARGET_CHAT", "")
ALBUM_WAIT = 1.5     # Telegram присылает альбом отдельными сообщениями — ждём, пока придут все
KB = M([[B("✅ Опубликовать", callback_data="pub")], [B("✏️ Исправить текст", callback_data="edit"),
                                                        B("✖ Отмена", callback_data="cancel")]])


def draft(ctx) -> dict:
    return ctx.user_data.setdefault("draft", {"media": [], "raw": ""})


async def on_media(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER:
        return
    msg, d = update.message, draft(ctx)
    if msg.photo:
        d["media"].append(("photo", msg.photo[-1].file_id))
    elif msg.video:
        d["media"].append(("video", msg.video.file_id))
    if msg.caption:
        d["raw"] = msg.caption
    if msg.media_group_id:                       # дожидаемся остальных файлов альбома
        key = f"album:{msg.media_group_id}"
        if ctx.user_data.get(key):
            return
        ctx.user_data[key] = True
        await asyncio.sleep(ALBUM_WAIT)
    await preview(update, ctx)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER:
        return
    d = draft(ctx)
    if ctx.user_data.pop("editing", False):
        d["final"] = update.message.text            # владелец прислал исправленный текст целиком
    else:
        d["raw"] = update.message.text
        d.pop("final", None)
    await preview(update, ctx)


async def preview(update: Update, ctx):
    d = draft(ctx)
    if not d["raw"] and not d.get("final"):
        await update.effective_message.reply_text(f"📷 Медиа: {len(d['media'])}. Теперь пришлите описание.")
        return
    text = d.get("final") or render(parse(d["raw"]))
    d["text"] = text
    await update.effective_message.reply_text(f"Превью поста ({len(d['media'])} медиа):\n\n{text}", reply_markup=KB)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = draft(ctx)
    if q.data == "cancel":
        ctx.user_data.clear()
        await q.edit_message_text("Отменено. Присылайте следующее объявление.")
    elif q.data == "edit":
        ctx.user_data["editing"] = True
        await q.message.reply_text("Пришлите исправленный текст целиком:")
    elif q.data == "pub":
        media = [(InputMediaPhoto if k == "photo" else InputMediaVideo)(fid) for k, fid in d["media"][:10]]
        if media:
            media[0] = type(media[0])(media[0].media, caption=d["text"])
            await ctx.bot.send_media_group(TARGET, media)
        else:
            await ctx.bot.send_message(TARGET, d["text"])
        ctx.user_data.clear()
        await q.edit_message_text("✅ Опубликовано в группе.")


def main():
    app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, on_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()


if __name__ == "__main__":
    main()
