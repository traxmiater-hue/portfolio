"""Мониторинг цен и наличия: каждый запуск сохраняет снимок в SQLite, сравнивает с прошлым,
строит Excel-отчёт с изменениями и (по желанию) шлёт сводку в Telegram.

Демо на books.toscrape.com. Запускать по расписанию (Планировщик Windows / cron), например каждое утро.

    python monitor.py --pages 5                       # 100 товаров
    python monitor.py --pages 5 --simulate-yesterday  # для демо: создать «вчерашний» снимок с другими ценами
    TG_TOKEN=... TG_CHAT=... python monitor.py        # + сводка в Telegram
"""
import argparse
import logging
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

BASE = "https://books.toscrape.com/catalogue/"
OUT = Path(__file__).parent
log = logging.getLogger("monitor")
db = sqlite3.connect(OUT / "prices.db")
db.execute("CREATE TABLE IF NOT EXISTS snap(day TEXT, sku TEXT, title TEXT, url TEXT, price REAL, stock INT,"
           " PRIMARY KEY(day, sku))")


def scrape(pages: int) -> list[dict]:
    s, items = requests.Session(), []
    for n in range(1, pages + 1):
        url = urljoin(BASE, f"page-{n}.html")
        page = BeautifulSoup(s.get(url, timeout=20).text, "lxml")
        for a in page.select("article.product_pod"):
            link = a.select_one("h3 a")
            items.append({"sku": link["href"].split("/")[0], "title": link["title"], "url": urljoin(url, link["href"]),
                          "price": float(a.select_one(".price_color").text.lstrip("Â£")),
                          "stock": int("In stock" in a.select_one(".availability").text)})
        log.info("страница %d: всего %d товаров", n, len(items))
    return items


def save(day: str, items: list[dict]):
    db.executemany("INSERT OR REPLACE INTO snap VALUES(?,?,?,?,?,?)",
                   [(day, i["sku"], i["title"], i["url"], i["price"], i["stock"]) for i in items])
    db.commit()


def simulate_yesterday(items: list[dict]):
    """Только для демонстрации: «вчерашние» цены отличаются у части товаров."""
    rnd, prev = random.Random(42), []
    for i in items:
        p = dict(i)
        if rnd.random() < 0.25:
            p["price"] = round(i["price"] * rnd.choice([0.85, 0.9, 1.08, 1.12, 1.2]), 2)
        if rnd.random() < 0.05:
            p["stock"] = 1 - i["stock"]
        prev.append(p)
    save((date.today() - timedelta(days=1)).isoformat(), prev)


def compare(today: str) -> list[dict]:
    prev_day = db.execute("SELECT MAX(day) FROM snap WHERE day < ?", (today,)).fetchone()[0]
    if not prev_day:
        return []
    rows = db.execute("""SELECT t.sku, t.title, t.url, p.price, t.price, p.stock, t.stock FROM snap t
                         JOIN snap p ON p.sku = t.sku AND p.day = ? WHERE t.day = ?""", (prev_day, today)).fetchall()
    out = []
    for sku, title, url, was, now, s_was, s_now in rows:
        if was != now or s_was != s_now:
            out.append({"sku": sku, "title": title, "url": url, "was": was, "now": now,
                        "diff": round((now - was) / was * 100, 1), "stock": "появился" if s_now > s_was else
                        "закончился" if s_now < s_was else "в наличии"})
    return sorted(out, key=lambda r: r["diff"])


def report(changes: list[dict], total: int, day: str) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Изменения"
    ws.append([f"Мониторинг цен · {day} · проверено {total} товаров · изменений: {len(changes)}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Товар", "Было, £", "Стало, £", "Изменение, %", "Наличие", "Ссылка"])
    for c in ws[2]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    for r in changes:
        ws.append([r["title"], r["was"], r["now"], r["diff"], r["stock"], r["url"]])
        ws.cell(ws.max_row, 6).hyperlink = r["url"]
    n = ws.max_row
    ws.conditional_formatting.add(f"D3:D{n}", CellIsRule(operator="lessThan", formula=["0"],
                                  fill=PatternFill("solid", fgColor="D9F2E3"), font=Font(color="146C2E", bold=True)))
    ws.conditional_formatting.add(f"D3:D{n}", CellIsRule(operator="greaterThan", formula=["0"],
                                  fill=PatternFill("solid", fgColor="FBE3E1"), font=Font(color="A61B1B", bold=True)))
    for i, w in enumerate([60, 10, 10, 14, 14, 50], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes, ws.auto_filter.ref = "A3", f"A2:F{n}"
    path = OUT / f"report_{day}.xlsx"
    wb.save(path)
    return path


def telegram(changes: list[dict], total: int):
    token, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT")
    if not (token and chat):
        return
    down = [c for c in changes if c["diff"] < 0][:5]
    up = [c for c in changes if c["diff"] > 0][-5:]
    text = (f"📊 Мониторинг цен: {total} товаров, изменений {len(changes)}\n\n⬇️ Подешевели:\n"
            + "\n".join(f"• {c['title'][:40]} {c['was']} → {c['now']} ({c['diff']}%)" for c in down)
            + "\n\n⬆️ Подорожали:\n" + "\n".join(f"• {c['title'][:40]} {c['was']} → {c['now']} (+{c['diff']}%)" for c in up))
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat, "text": text}, timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--simulate-yesterday", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    today = date.today().isoformat()
    items = scrape(a.pages)
    if a.simulate_yesterday:
        simulate_yesterday(items)
    save(today, items)
    changes = compare(today)
    path = report(changes, len(items), today)
    telegram(changes, len(items))
    log.info("%s: %d товаров, изменений %d → %s", datetime.now().strftime("%H:%M"), len(items), len(changes), path.name)


if __name__ == "__main__":
    main()
