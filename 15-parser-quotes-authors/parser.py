"""Парсер с пагинацией и переходом в карточки → Excel и CSV.

Демо на quotes.toscrape.com (песочница для обучения парсингу): собирает все цитаты со всех страниц,
заходит на страницу каждого автора за датой и местом рождения, считает статистику по тегам и авторам.
Результат — Excel с тремя листами (Цитаты, Авторы, Теги + диаграмма) и CSV для импорта куда угодно.

    python parser.py                 # всё
    python parser.py --tag love      # только цитаты с тегом
"""
import argparse
import csv
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE = "https://quotes.toscrape.com/"
OUT = Path(__file__).parent
log = logging.getLogger("parser")
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (portfolio demo parser)"


def get(url: str, tries: int = 3) -> BeautifulSoup:
    for i in range(tries):
        try:
            r = S.get(url, timeout=20)
            r.raise_for_status()
            return BeautifulSoup(r.text, "lxml")
        except requests.RequestException as e:
            log.warning("%s — попытка %d: %s", url, i + 1, e)
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"не открылась страница {url}")


def quotes(tag: str | None) -> list[dict]:
    url, out = urljoin(BASE, f"tag/{tag}/" if tag else ""), []
    while url:
        page = get(url)
        for q in page.select(".quote"):
            out.append({"text": q.select_one(".text").get_text(strip=True).strip("“”"),
                        "author": q.select_one(".author").get_text(strip=True),
                        "author_url": urljoin(BASE, q.select_one("a[href^='/author/']")["href"]),
                        "tags": [t.get_text() for t in q.select(".tag")]})
        nxt = page.select_one("li.next a")
        url = urljoin(url, nxt["href"]) if nxt else None
        log.info("цитат собрано: %d", len(out))
    return out


def author(url: str) -> dict:
    p = get(url)
    born = p.select_one(".author-born-date").get_text(strip=True)
    return {"name": p.select_one(".author-title").get_text(strip=True), "url": url,
            "born": datetime.strptime(born, "%B %d, %Y").date(),
            "place": p.select_one(".author-born-location").get_text(strip=True).removeprefix("in "),
            "bio": p.select_one(".author-description").get_text(" ", strip=True)[:300] + "…"}


HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="2E4057")


def sheet(ws, header: list[str], rows: list[list], widths: list[int]):
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill, c.alignment = HEAD, FILL, Alignment(vertical="center")
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    t0 = time.time()
    qs = quotes(a.tag)
    urls = sorted({q["author_url"] for q in qs})
    with ThreadPoolExecutor(6) as pool:           # карточки авторов — параллельно, бережно к сайту
        authors = {x["url"]: x for x in pool.map(author, urls)}
    tags = Counter(t for q in qs for t in q["tags"])
    per_author = Counter(q["author"] for q in qs)

    wb = Workbook()
    ws = wb.active
    ws.title = "Цитаты"
    sheet(ws, ["№", "Цитата", "Автор", "Теги", "Год рождения автора"],
          [[i, q["text"], q["author"], ", ".join(q["tags"]), authors[q["author_url"]]["born"].year]
           for i, q in enumerate(qs, 1)], [5, 80, 22, 30, 12])
    for i, q in enumerate(qs, 2):
        ws.cell(i, 3).hyperlink = q["author_url"]
    sheet(wb.create_sheet("Авторы"), ["Автор", "Цитат", "Дата рождения", "Место рождения", "Кратко"],
          [[x["name"], per_author[x["name"]], x["born"], x["place"], x["bio"]]
           for x in sorted(authors.values(), key=lambda x: -per_author[x["name"]])], [24, 8, 14, 34, 70])
    wt = wb.create_sheet("Теги")
    sheet(wt, ["Тег", "Цитат"], tags.most_common(15), [20, 10])
    ch = BarChart()
    ch.title, ch.y_axis.title, ch.height, ch.width = "Топ-15 тегов", "цитат", 9, 18
    ch.add_data(Reference(wt, min_col=2, min_row=1, max_row=16), titles_from_data=True)
    ch.set_categories(Reference(wt, min_col=1, min_row=2, max_row=16))
    wt.add_chart(ch, "D2")
    xlsx = OUT / "quotes.xlsx"
    wb.save(xlsx)
    with open(OUT / "quotes.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["text", "author", "tags", "author_born", "author_place"])
        for q in qs:
            x = authors[q["author_url"]]
            w.writerow([q["text"], q["author"], "|".join(q["tags"]), x["born"], x["place"]])
    log.info("Готово за %.1f с: %d цитат, %d авторов, %d тегов → %s", time.time() - t0, len(qs), len(authors),
             len(tags), xlsx.name)


if __name__ == "__main__":
    main()
