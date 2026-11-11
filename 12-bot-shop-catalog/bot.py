"""Telegram-бот «каталог + корзина + заказ» для небольшой компании.

Каталог хранится в catalog.json (или подтягивается из опубликованной Google Таблицы в CSV — CATALOG_CSV_URL),
поэтому владелец меняет товары и цены без программиста. Заказ уходит администратору в личку или группу.

    pip install -r requirements.txt
    BOT_TOKEN=... ADMIN_CHAT_ID=... python bot.py
"""
import csv
import io
import json
import logging
import os
from pathlib import Path

import requests
from telegram import InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler, filters)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
ROOT = Path(__file__).parent
ADMIN = int(os.getenv("ADMIN_CHAT_ID", "0"))
CSV_URL = os.getenv("CATALOG_CSV_URL")          # «Файл → Опубликовать → CSV» в Google Таблицах
NAME, PHONE, COMMENT = range(3)


def load_catalog() -> dict:
    """{категория: [{id, title, price, text, photo}]} — из Google Таблицы или из catalog.json."""
    if CSV_URL:
        rows = csv.DictReader(io.StringIO(requests.get(CSV_URL, timeout=15).text))
        cat: dict = {}
        for r in rows:
            cat.setdefault(r["category"], []).append({"id": r["id"], "title": r["title"], "price": int(r["price"]),
                                                     "text": r["text"], "photo": r.get("photo") or None})
        return cat
    return json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))


def item(cid: str) -> dict | None:
    return next((i for items in load_catalog().values() for i in items if i["id"] == cid), None)


def rub(n: int) -> str:
    return f"{n:,} ₽".replace(",", " ")


def cart(ctx) -> dict:
    return ctx.user_data.setdefault("cart", {})


def cart_text(ctx) -> tuple[str, int]:
    lines, total = [], 0
    for cid, qty in cart(ctx).items():
        it = item(cid)
        if it:
            total += it["price"] * qty
            lines.append(f"• {it['title']} × {qty} = {rub(it['price'] * qty)}")
    return "\n".join(lines), total


MAIN = M([[B("🗂 Каталог", callback_data="cats")], [B("🛒 Корзина", callback_data="cart")],
          [B("📍 Контакты", callback_data="contacts")]])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Здравствуйте! Это бот студии уборки «Чистый дом» 🧽\nВыберите услуги в каталоге — оформим заказ за минуту.",
        reply_markup=MAIN)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kind, _, arg = q.data.partition(":")
    cat = load_catalog()
    if kind == "cats":
        await q.edit_message_text("Выберите раздел:", reply_markup=M(
            [[B(c, callback_data=f"cat:{c}")] for c in cat] + [[B("⬅️ Меню", callback_data="menu")]]))
    elif kind == "cat":
        await q.edit_message_text(f"<b>{arg}</b>", parse_mode="HTML", reply_markup=M(
            [[B(f"{i['title']} — {rub(i['price'])}", callback_data=f"item:{i['id']}")] for i in cat[arg]]
            + [[B("⬅️ Разделы", callback_data="cats")]]))
    elif kind == "item":
        it = item(arg)
        text = f"<b>{it['title']}</b>\n{it['text']}\n\n💰 {rub(it['price'])}"
        kb = M([[B("➕ В корзину", callback_data=f"add:{arg}")], [B("🛒 Корзина", callback_data="cart"),
                                                                    B("⬅️ Назад", callback_data="cats")]])
        if it.get("photo"):
            await q.message.reply_photo(it["photo"], caption=text, parse_mode="HTML", reply_markup=kb)
        else:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    elif kind == "add":
        cart(ctx)[arg] = cart(ctx).get(arg, 0) + 1
        await q.message.reply_text(f"✅ {item(arg)['title']} в корзине.", reply_markup=M(
            [[B("🛒 Оформить", callback_data="cart"), B("🗂 Ещё услуги", callback_data="cats")]]))
    elif kind == "del":
        cart(ctx).pop(arg, None)
        await show_cart(q, ctx)
    elif kind == "cart":
        await show_cart(q, ctx)
    elif kind == "contacts":
        await q.edit_message_text("📍 Москва, ул. Новая, 5\n🕘 Ежедневно 8:00–22:00\n☎️ +7 495 000-00-00",
                                  reply_markup=M([[B("⬅️ Меню", callback_data="menu")]]))
    elif kind == "menu":
        await q.edit_message_text("Главное меню:", reply_markup=MAIN)


async def show_cart(q, ctx):
    text, total = cart_text(ctx)
    if not text:
        await q.edit_message_text("Корзина пуста.", reply_markup=M([[B("🗂 Каталог", callback_data="cats")]]))
        return
    rows = [[B(f"✖ {item(cid)['title'][:28]}", callback_data=f"del:{cid}")] for cid in cart(ctx)]
    await q.edit_message_text(f"🛒 <b>Корзина</b>\n{text}\n\n<b>Итого: {rub(total)}</b>", parse_mode="HTML",
                              reply_markup=M(rows + [[B("✅ Оформить заказ", callback_data="checkout")]]))


async def checkout(update: Update, ctx):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("Как к вам обращаться?")
    return NAME


async def got_name(update: Update, ctx):
    ctx.user_data["name"] = update.message.text.strip()[:60]
    await update.message.reply_text("Ваш телефон? (или нажмите /skip)")
    return PHONE


async def got_phone(update: Update, ctx):
    digits = "".join(ch for ch in update.message.text if ch.isdigit())
    if len(digits) < 10:
        await update.message.reply_text("Не похоже на номер. Пример: +7 900 123-45-67")
        return PHONE
    ctx.user_data["phone"] = "+" + digits
    await update.message.reply_text("Комментарий к заказу: адрес, удобное время. Или /skip")
    return COMMENT


async def got_comment(update: Update, ctx):
    comment = "" if update.message.text == "/skip" else update.message.text[:500]
    text, total = cart_text(ctx)
    u = update.effective_user
    order = (f"🆕 <b>Новый заказ</b>\n{text}\n<b>Итого: {rub(total)}</b>\n\n"
             f"👤 {ctx.user_data['name']} · {ctx.user_data['phone']}\n💬 {comment or '—'}\n"
             f"Telegram: <a href='tg://user?id={u.id}'>{u.full_name}</a>")
    if ADMIN:
        await ctx.bot.send_message(ADMIN, order, parse_mode="HTML")
    ctx.user_data["cart"] = {}
    await update.message.reply_text("Спасибо! Заказ принят ✅ Администратор свяжется с вами в течение 15 минут.",
                                    reply_markup=MAIN)
    return ConversationHandler.END


def main():
    app = Application.builder().token(os.environ["BOT_TOKEN"]).build()
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(checkout, pattern="^checkout$")],
        states={NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_name)],
                PHONE: [MessageHandler(filters.TEXT, got_phone)],
                COMMENT: [MessageHandler(filters.TEXT, got_comment)]},
        fallbacks=[CommandHandler("start", start)]))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()


if __name__ == "__main__":
    main()
