"""Демо-данные: 2 пользователя и ~150 заявок за последние 10 недель.

    python seed.py            # создать crm.db с демо-данными (старая база удаляется)

Демо-входы (только для локальной демонстрации):
    admin   / demo123   — руководитель
    manager / demo123   — менеджер
"""
import os
import random
import sys
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from db import FMT, SOURCES, connect, init_schema

FIRST = ["Анна", "Иван", "Мария", "Дмитрий", "Елена", "Сергей", "Ольга", "Алексей", "Наталья", "Павел",
         "Татьяна", "Михаил", "Ксения", "Андрей", "Юлия", "Николай", "Светлана", "Артём"]
LAST = ["Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов", "Новиков",
        "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов"]
COMPANIES = ["", "", "", "ООО «Вектор»", "ИП Громов", "Кофейня «Зёрна»", "Студия «Лофт»", "ООО «СтройМаркет»",
             "Салон «Аура»", "Автосервис «Мотор»", "ООО «ТехноПарк»", "Пекарня «Колос»"]
NOTES = ["Нужен сайт-визитка", "Интересует Telegram-бот для записи", "Просит КП до пятницы",
         "Хочет интернет-магазин на 200 товаров", "Нужна интеграция с 1С", "Перезвонить после 18:00",
         "Сравнивает с конкурентами по цене", "Нужна доработка существующего сайта", ""]
COMMENTS = ["Позвонил, обсудили задачу", "Отправил примеры работ", "Клиент взял паузу до понедельника",
            "Согласовали бюджет", "Уточнил сроки", "Назначили созвон"]


def female(first: str) -> bool:
    return first[-1] in "аяю" and first not in ("Никита",)


def seed(path: str, n: int = 150, rnd: random.Random | None = None):
    rnd = rnd or random.Random(42)
    if os.path.exists(path):
        os.remove(path)
    con = connect(path)
    init_schema(con)
    con.execute("INSERT INTO users(id, login, name, password, role) VALUES (1,'admin','Ирина Белова',?,'admin')",
                (generate_password_hash("demo123"),))
    con.execute("INSERT INTO users(id, login, name, password, role) VALUES (2,'manager','Олег Котов',?,'manager')",
                (generate_password_hash("demo123"),))
    now = datetime.now()
    for _ in range(n):
        first, last = rnd.choice(FIRST), rnd.choice(LAST)
        name = f"{first} {last}{'а' if female(first) else ''}"
        age_days = rnd.triangular(0, 70, 5)
        created = now - timedelta(days=age_days, hours=rnd.randint(0, 9), minutes=rnd.randint(0, 59))
        # чем старше заявка, тем вероятнее она уже закрыта
        if age_days < 3:
            status = rnd.choices(["new", "work"], [3, 1])[0]
        elif age_days < 14:
            status = rnd.choices(["new", "work", "offer", "won", "lost"], [1, 4, 3, 2, 1])[0]
        else:
            status = rnd.choices(["work", "offer", "won", "lost"], [1, 1, 5, 3])[0]
        amount = 0 if status == "new" else rnd.randrange(8, 160) * 1000
        updated = created + timedelta(days=min(age_days, rnd.uniform(0, 7)))
        cur = con.execute(
            "INSERT INTO leads(name, phone, email, company, source, status, amount, manager_id, note, created, updated)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (name, f"+7 9{rnd.randint(10, 99)} {rnd.randint(100, 999)}-{rnd.randint(10, 99)}-{rnd.randint(10, 99)}",
             f"client{rnd.randint(100, 999)}@example.com", rnd.choice(COMPANIES),
             rnd.choices(SOURCES, [5, 3, 2, 2, 1])[0], status, amount,
             None if status == "new" else rnd.choice([1, 2]), rnd.choice(NOTES),
             created.strftime(FMT), updated.strftime(FMT)))
        lid = cur.lastrowid
        con.execute("INSERT INTO events(lead_id, user_id, kind, text, created) VALUES (?,?,?,?,?)",
                    (lid, None, "system", "Заявка создана", created.strftime(FMT)))
        if status != "new":
            for k in range(rnd.randint(1, 3)):
                t = created + (updated - created) * (k + 1) / 4
                con.execute("INSERT INTO events(lead_id, user_id, kind, text, created) VALUES (?,?,?,?,?)",
                            (lid, rnd.choice([1, 2]), "comment", rnd.choice(COMMENTS), t.strftime(FMT)))
    con.commit()
    con.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "crm.db"
    seed(target)
    print(f"Готово: {target}. Вход: admin / demo123")
