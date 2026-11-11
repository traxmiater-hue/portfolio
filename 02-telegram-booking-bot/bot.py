"""Telegram-бот онлайн-записи к мастеру.

Клиент: выбор услуги → дня → времени → телефон → подтверждение; список своих записей и отмена;
напоминание за 2 часа. Владелец (/admin): записи на сегодня/завтра/неделю, отмена, выгрузка CSV,
уведомления о новых записях и отменах.
"""
import html
import io
import logging
import os
import re
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton as B, InlineKeyboardMarkup as M, KeyboardButton,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import config
from slots import booking_days, fmt_day, fmt_dt, free_slots
from storage import FMT, Storage

load_dotenv()
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

ADMINS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "")) if x.isdigit()}
db = Storage(os.getenv("DB_PATH", config.DB_PATH))


def now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def rub(x: int) -> str:
    return f"{x:,}".replace(",", " ") + " ₽"


# ---------------- клавиатуры ----------------

def kb_menu(is_admin: bool = False) -> M:
    rows = [[B("✂️ Записаться", callback_data="book")],
            [B("📋 Мои записи", callback_data="my")],
            [B("💈 Цены и адрес", callback_data="info")]]
    if is_admin:
        rows.append([B("🔧 Панель владельца", callback_data="adm:menu")])
    return M(rows)


def kb_services() -> M:
    rows = [[B(f"{s['name']} · {s['minutes']} мин · {rub(s['price'])}", callback_data=f"svc:{s['id']}")]
            for s in config.SERVICES]
    rows.append([B("← Меню", callback_data="menu")])
    return M(rows)


def slots_for(svc: dict, day: date) -> list[datetime]:
    return free_slots(day, svc["minutes"], db.busy(day), now(), config.OPEN_TIME, config.CLOSE_TIME,
                      config.SLOT_STEP_MIN, config.MIN_LEAD_MIN)


def kb_days(svc: dict) -> M:
    days = [d for d in booking_days(now().date(), config.DAYS_AHEAD, config.DAYS_OFF) if slots_for(svc, d)]
    btns = [B(fmt_day(d), callback_data=f"day:{svc['id']}:{d:%Y%m%d}") for d in days]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([B("← Услуги", callback_data="book")])
    return M(rows)


def kb_times(svc: dict, day: date) -> M:
    btns = [B(t.strftime("%H:%M"), callback_data=f"slot:{svc['id']}:{t:%Y%m%d%H%M}") for t in slots_for(svc, day)]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([B("← Другой день", callback_data=f"svc:{svc['id']}")])
    return M(rows)


# ---------------- клиент ----------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    user = update.effective_user
    await update.message.reply_text(
        f"Здравствуйте, {user.first_name}! Это бот {config.BUSINESS_NAME}.\n"
        "Здесь можно записаться на услугу за минуту — без звонков и ожидания ответа.",
        reply_markup=kb_menu(user.id in ADMINS))


async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("Эта команда только для владельца.")
        return
    await update.message.reply_text("Панель владельца:", reply_markup=kb_admin())


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data, user = q.data, update.effective_user

    if data == "menu":
        ctx.user_data.pop("pending", None)
        await q.edit_message_text("Главное меню:", reply_markup=kb_menu(user.id in ADMINS))

    elif data == "info":
        prices = "\n".join(f"• {s['name']} — {rub(s['price'])} ({s['minutes']} мин)" for s in config.SERVICES)
        await q.edit_message_text(
            f"💈 Услуги и цены:\n{prices}\n\n📍 {config.ADDRESS}\n"
            f"🕙 {config.OPEN_TIME:%H:%M}–{config.CLOSE_TIME:%H:%M}, понедельник — выходной\n📞 {config.PHONE}",
            reply_markup=M([[B("✂️ Записаться", callback_data="book")], [B("← Меню", callback_data="menu")]]))

    elif data == "book":
        await q.edit_message_text("Выберите услугу:", reply_markup=kb_services())

    elif data.startswith("svc:"):
        svc = config.service(data[4:])
        markup = kb_days(svc)
        text = f"{svc['name']}. Выберите день:" if len(markup.inline_keyboard) > 1 else \
            "На ближайшую неделю свободного времени нет 😔 Позвоните нам, постараемся помочь."
        await q.edit_message_text(text, reply_markup=markup)

    elif data.startswith("day:"):
        _, sid, d = data.split(":")
        svc, day = config.service(sid), datetime.strptime(d, "%Y%m%d").date()
        await q.edit_message_text(f"{svc['name']}, {fmt_day(day)}. Свободное время:",
                                  reply_markup=kb_times(svc, day))

    elif data.startswith("slot:"):
        _, sid, t = data.split(":")
        ctx.user_data["pending"] = {"svc": sid, "start": t}
        client = db.client(user.id)
        if client and client["phone"]:
            await show_confirm(q.message.chat_id, ctx)
            await q.delete_message()
        else:
            ctx.user_data["await_phone"] = True
            await q.edit_message_text("Остался телефон — мастер позвонит, только если что-то изменится.")
            await ctx.bot.send_message(
                q.message.chat_id, "Нажмите кнопку ниже или напишите номер вручную:",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 Отправить мой номер", request_contact=True)]],
                                                 resize_keyboard=True, one_time_keyboard=True))

    elif data == "confirm":
        await confirm_booking(update, ctx)

    elif data == "my":
        rows = db.upcoming_for_user(user.id, now())
        if not rows:
            await q.edit_message_text("У вас нет предстоящих записей.", reply_markup=M(
                [[B("✂️ Записаться", callback_data="book")], [B("← Меню", callback_data="menu")]]))
            return
        lines, btns = [], []
        for r in rows:
            start = datetime.strptime(r["start"], FMT)
            lines.append(f"• {fmt_dt(start)} — {r['service_name']}")
            btns.append([B(f"✖ Отменить {fmt_dt(start)}", callback_data=f"cancel:{r['id']}")])
        btns.append([B("← Меню", callback_data="menu")])
        await q.edit_message_text("Ваши записи:\n" + "\n".join(lines), reply_markup=M(btns))

    elif data.startswith("cancel:"):
        bid = int(data[7:])
        b = db.get(bid)
        if b and db.cancel(bid, user_id=user.id):
            await q.edit_message_text(f"Запись на {fmt_dt(datetime.strptime(b['start'], FMT))} отменена.",
                                      reply_markup=kb_menu(user.id in ADMINS))
            await notify_admins(ctx, f"❌ Клиент отменил запись\n{describe(b)}")
        else:
            await q.edit_message_text("Эта запись уже отменена.", reply_markup=kb_menu(user.id in ADMINS))

    elif data.startswith("adm") and user.id in ADMINS:
        await on_admin(q, ctx, data)


PHONE_RE = re.compile(r"^\+?[\d\s\-()]{10,18}$")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg, user = update.message, update.effective_user
    if not ctx.user_data.get("await_phone"):
        await msg.reply_text("Я понимаю кнопки 🙂 Нажмите /start, чтобы открыть меню.")
        return
    phone = msg.contact.phone_number if msg.contact else (msg.text or "").strip()
    if not PHONE_RE.match(phone):
        await msg.reply_text("Не похоже на номер. Пример: +7 900 123-45-67")
        return
    db.save_client(user.id, user.full_name, user.username, phone)
    ctx.user_data.pop("await_phone", None)
    await msg.reply_text("Спасибо, номер сохранён.", reply_markup=ReplyKeyboardRemove())
    await show_confirm(msg.chat_id, ctx)


async def show_confirm(chat_id: int, ctx: ContextTypes.DEFAULT_TYPE):
    p = ctx.user_data["pending"]
    svc, start = config.service(p["svc"]), datetime.strptime(p["start"], "%Y%m%d%H%M")
    client = db.client(chat_id)
    await ctx.bot.send_message(
        chat_id,
        f"Проверьте запись:\n\n✂️ {svc['name']}\n🗓 {fmt_dt(start)}\n💳 {rub(svc['price'])} (оплата на месте)\n"
        f"📞 {client['phone']}\n📍 {config.ADDRESS}",
        reply_markup=M([[B("✅ Подтвердить", callback_data="confirm")],
                        [B("↩ Другое время", callback_data=f"svc:{svc['id']}")]]))


async def confirm_booking(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q, user = update.callback_query, update.effective_user
    p = ctx.user_data.pop("pending", None)
    if not p:
        await q.edit_message_text("Запись уже оформлена или устарела.", reply_markup=kb_menu(user.id in ADMINS))
        return
    svc, start = config.service(p["svc"]), datetime.strptime(p["start"], "%Y%m%d%H%M")
    if start < now() + timedelta(minutes=config.MIN_LEAD_MIN):
        await q.edit_message_text("Это время уже прошло. Выберите другое:", reply_markup=kb_days(svc))
        return
    bid = db.add_booking(user.id, svc, start, now())
    if bid is None:
        await q.edit_message_text("Пока вы выбирали, это время заняли 😔 Выберите другое:", reply_markup=kb_days(svc))
        return
    await q.edit_message_text(
        f"Готово! Ждём вас {fmt_dt(start)}.\n✂️ {svc['name']}\n📍 {config.ADDRESS}\n\n"
        f"Напомним за {config.REMIND_BEFORE_MIN // 60} ч. Отменить можно в «Мои записи».",
        reply_markup=kb_menu(user.id in ADMINS))
    await notify_admins(ctx, f"🆕 Новая запись\n{describe(db.get(bid))}")


# ---------------- владелец ----------------

def kb_admin() -> M:
    return M([[B("Сегодня", callback_data="adm:day:0"), B("Завтра", callback_data="adm:day:1")],
              [B("Неделя", callback_data="adm:week"), B("📥 Выгрузить CSV", callback_data="adm:csv")],
              [B("← Меню", callback_data="menu")]])


def describe(b) -> str:
    start = datetime.strptime(b["start"], FMT)
    who = b["name"] or "—"
    if b["username"]:
        who += f" (@{b['username']})"
    return f"🗓 {fmt_dt(start)}–{b['end'][11:]}\n✂️ {b['service_name']}, {rub(b['price'])}\n👤 {who}\n📞 {b['phone'] or '—'}"


async def on_admin(q, ctx, data: str):
    today = datetime.combine(now().date(), datetime.min.time())
    if data == "adm:menu":
        await q.edit_message_text("Панель владельца:", reply_markup=kb_admin())
    elif data.startswith("adm:day:"):
        d = today + timedelta(days=int(data[8:]))
        rows = db.between(d, d + timedelta(days=1))
        total = sum(r["price"] for r in rows)
        text = f"{fmt_day(d.date())}: {len(rows)} записей на {rub(total)}\n\n" + \
               "\n\n".join(describe(r) for r in rows) if rows else f"{fmt_day(d.date())}: записей нет."
        btns = [[B(f"✖ {r['start'][11:]} {r['name'] or ''}", callback_data=f"admc:{r['id']}")] for r in rows]
        btns.append([B("← Панель", callback_data="adm:menu")])
        await q.edit_message_text(text, reply_markup=M(btns))
    elif data == "adm:week":
        rows = db.between(today, today + timedelta(days=7))
        by_day: dict[str, list] = {}
        for r in rows:
            by_day.setdefault(fmt_day(datetime.strptime(r["start"], FMT).date()), []).append(r)
        lines = [f"<b>{d}</b> — {len(rs)} зап., {rub(sum(r['price'] for r in rs))}\n" +
                 "\n".join(f"  {r['start'][11:]} {r['service_name']} — {html.escape(r['name'] or '')}" for r in rs)
                 for d, rs in by_day.items()]
        await q.edit_message_text("\n\n".join(lines) or "На неделю записей нет.", parse_mode="HTML",
                                  reply_markup=M([[B("← Панель", callback_data="adm:menu")]]))
    elif data == "adm:csv":
        await ctx.bot.send_document(q.message.chat_id, io.BytesIO(db.export_csv()),
                                    filename=f"zapisi_{now():%Y-%m-%d}.csv", caption="Все записи")
    elif data.startswith("admc:"):
        bid = int(data[5:])
        b = db.get(bid)
        if b and db.cancel(bid):
            await ctx.bot.send_message(
                b["user_id"], f"К сожалению, запись на {fmt_dt(datetime.strptime(b['start'], FMT))} отменена "
                              f"мастером. Пожалуйста, выберите другое время 🙏", reply_markup=kb_menu())
            await q.edit_message_text(f"Запись отменена, клиент уведомлён.\n\n{describe(b)}", reply_markup=kb_admin())
        else:
            await q.edit_message_text("Запись уже отменена.", reply_markup=kb_admin())


async def notify_admins(ctx, text: str):
    for admin in ADMINS:
        try:
            await ctx.bot.send_message(admin, text)
        except Exception as e:  # владелец мог не нажать /start — не роняем запись клиента
            log.warning("Не удалось уведомить %s: %s", admin, e)


# ---------------- напоминания ----------------

async def send_reminders(ctx: ContextTypes.DEFAULT_TYPE):
    for b in db.due_reminders(now(), config.REMIND_BEFORE_MIN):
        start = datetime.strptime(b["start"], FMT)
        try:
            await ctx.bot.send_message(
                b["user_id"], f"⏰ Напоминаем: {fmt_dt(start)} — {b['service_name']}.\n📍 {config.ADDRESS}\n"
                              "Если не получается прийти — отмените запись в «Мои записи».",
                reply_markup=M([[B("📋 Мои записи", callback_data="my")]]))
        except Exception as e:
            log.warning("Напоминание %s не отправлено: %s", b["id"], e)
        db.mark_reminded(b["id"])


def build_app(token: str) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.CONTACT, on_text))
    app.job_queue.run_repeating(send_reminders, interval=60, first=5)
    return app


if __name__ == "__main__":
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Нет BOT_TOKEN. Скопируйте .env.example в .env и вставьте токен от @BotFather.")
    if not ADMINS:
        log.warning("ADMIN_IDS пуст — уведомления владельцу отправляться не будут.")
    log.info("Бот запущен")
    build_app(token).run_polling(allowed_updates=Update.ALL_TYPES)
