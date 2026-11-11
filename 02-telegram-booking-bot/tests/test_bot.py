"""Тесты логики и полного сценария записи — без реального Telegram."""
import asyncio
import sys
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot  # noqa: E402
from slots import booking_days, free_slots  # noqa: E402
from storage import Storage  # noqa: E402

TUE = date(2026, 11, 10)  # вторник


# ---------------- логика слотов ----------------

def test_free_slots_skip_busy_and_past():
    busy = [(datetime(2026, 11, 10, 12, 0), datetime(2026, 11, 10, 13, 0))]
    now = datetime(2026, 11, 10, 9, 0)
    s = free_slots(TUE, 60, busy, now, time(10), time(20), 30, 60)
    hm = [t.strftime("%H:%M") for t in s]
    assert hm[0] == "10:00"
    assert "11:30" not in hm and "12:00" not in hm and "12:30" not in hm
    assert "11:00" in hm and "13:00" in hm
    assert hm[-1] == "19:00"  # последний часовой слот заканчивается ровно в 20:00


def test_booking_days_skip_monday():
    days = booking_days(date(2026, 11, 8), 7, {0})
    assert all(d.weekday() != 0 for d in days) and len(days) == 7


def test_storage_conflict_and_cancel(tmp_path):
    db = Storage(str(tmp_path / "t.db"))
    svc = {"id": "cut", "name": "Стрижка", "minutes": 60, "price": 1500}
    n = datetime(2026, 11, 10, 9)
    a = db.add_booking(1, svc, datetime(2026, 11, 10, 12), n)
    assert a
    assert db.add_booking(2, svc, datetime(2026, 11, 10, 12, 30), n) is None  # пересечение
    assert db.add_booking(2, svc, datetime(2026, 11, 10, 13), n)              # стык — можно
    assert not db.cancel(a, user_id=2)                                        # чужую нельзя
    assert db.cancel(a, user_id=1)
    assert db.add_booking(2, svc, datetime(2026, 11, 10, 12), n)              # освободилось
    assert "Стрижка".encode("utf-8") in db.export_csv()


# ---------------- фейковый Telegram ----------------

class FakeBot:
    def __init__(self):
        self.sent = []  # (chat_id, text, markup)

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text, reply_markup))

    async def send_document(self, chat_id, document, filename=None, **kw):
        self.sent.append((chat_id, f"DOC {filename}", document.read()))


class FakeQuery:
    def __init__(self, data, chat_id, tg):
        self.data, self.tg = data, tg
        self.message = SimpleNamespace(chat_id=chat_id)

    async def answer(self): pass

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.tg.sent.append((self.message.chat_id, text, reply_markup))

    async def delete_message(self): pass


class Client:
    def __init__(self, uid, name, tg):
        self.user = SimpleNamespace(id=uid, first_name=name, full_name=name, username=name.lower())
        self.ctx = SimpleNamespace(user_data={}, bot=tg)
        self.tg = tg

    def press(self, data):
        upd = SimpleNamespace(effective_user=self.user, callback_query=FakeQuery(data, self.user.id, self.tg))
        asyncio.run(bot.on_button(upd, self.ctx))
        return self.last()

    def say(self, text):
        async def reply(t, reply_markup=None, **kw):
            self.tg.sent.append((self.user.id, t, reply_markup))
        msg = SimpleNamespace(text=text, contact=None, chat_id=self.user.id, reply_text=reply)
        asyncio.run(bot.on_text(SimpleNamespace(effective_user=self.user, message=msg), self.ctx))
        return self.last()

    def last(self):
        return [m for m in self.tg.sent if m[0] == self.user.id][-1]


def buttons(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "db", Storage(str(tmp_path / "flow.db")))
    monkeypatch.setattr(bot, "ADMINS", {999})
    monkeypatch.setattr(bot, "now", lambda: datetime(2026, 11, 10, 9, 0))
    return FakeBot()


def test_full_booking_flow(env):
    tg = env
    ivan, olga = Client(1, "Иван", tg), Client(2, "Ольга", tg)

    _, text, kb = ivan.press("book")
    assert "svc:cut" in buttons(kb)
    _, text, kb = ivan.press("svc:cut")
    assert "day:cut:20261110" in buttons(kb) and "day:cut:20261116" not in buttons(kb)  # 16.11 — понедельник
    _, text, kb = ivan.press("day:cut:20261110")
    assert buttons(kb)[0] == "slot:cut:202611101000"
    ivan.press("slot:cut:202611101000")
    assert ivan.ctx.user_data["await_phone"]
    _, text, _ = ivan.say("привет")
    assert "Не похоже на номер" in text
    _, text, kb = ivan.say("+7 900 111-22-33")
    assert "Проверьте запись" in text and "confirm" in buttons(kb)

    # Ольга выбрала то же время, но Иван подтвердил первым
    olga.press("slot:cut:202611101000")
    olga.say("89001112244")
    _, text, _ = ivan.press("confirm")
    assert text.startswith("Готово!")
    assert any(m[0] == 999 and "Новая запись" in m[1] for m in tg.sent)
    _, text, _ = olga.press("confirm")
    assert "заняли" in text

    # слот 10:00 пропал из списка
    _, _, kb = olga.press("day:cut:20261110")
    assert "slot:cut:202611101000" not in buttons(kb)

    # напоминание за 2 часа уходит один раз
    asyncio.run(bot.send_reminders(SimpleNamespace(bot=tg)))
    asyncio.run(bot.send_reminders(SimpleNamespace(bot=tg)))
    assert sum("Напоминаем" in m[1] for m in tg.sent if m[0] == 1) == 1

    # повторная запись — телефон уже известен, сразу подтверждение
    ivan.press("slot:beard:202611101500")
    assert "Проверьте запись" in ivan.last()[1]
    ivan.press("confirm")

    # «Мои записи» и отмена
    _, text, kb = ivan.press("my")
    assert "10:00" in text and "15:00" in text
    cancel_id = next(b for b in buttons(kb) if b.startswith("cancel:"))
    _, text, _ = ivan.press(cancel_id)
    assert "отменена" in text
    assert any(m[0] == 999 and "отменил" in m[1] for m in tg.sent)


def test_admin_panel(env):
    tg = env
    ivan, admin = Client(1, "Иван", tg), Client(999, "Владелец", tg)
    ivan.press("slot:combo:202611111200")
    ivan.say("+79001112233")
    ivan.press("confirm")

    _, _, kb = Client(1, "Иван", tg).press("menu")
    assert "adm:menu" not in buttons(kb)            # клиенту панель не видна
    _, _, kb = admin.press("menu")
    assert "adm:menu" in buttons(kb)

    _, text, kb = admin.press("adm:day:1")
    assert "1 записей на 2 100 ₽" in text and "Иван" in text
    _, text, _ = admin.press("adm:week")
    assert "Стрижка + борода" in text
    admin.press("adm:csv")
    assert tg.sent[-1][1].startswith("DOC zapisi_")
    cancel = next(b for b in buttons(kb) if b.startswith("admc:"))
    _, text, _ = admin.press(cancel)
    assert "клиент уведомлён" in text
    assert any(m[0] == 1 and "отменена мастером" in m[1] for m in tg.sent)


def test_app_builds():
    app = bot.build_app("123456:TEST")
    assert app.handlers
