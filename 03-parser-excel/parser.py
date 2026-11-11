"""Парсер каталога товаров → Excel.

Демо на books.toscrape.com — открытой песочнице для обучения парсингу (1000 товаров, 50 страниц).
Собирает список товаров, затем заходит в карточку каждого за деталями
(артикул, категория, остаток, отзывы, описание) и сохраняет красиво оформленный Excel:
лист «Товары» с фильтрами и ссылками + лист «Сводка» по категориям с диаграммой.

    python parser.py                       # весь каталог
    python parser.py --pages 3             # первые 3 страницы (60 товаров)
    python parser.py --category Poetry     # одна категория
    python parser.py --no-details          # только список, без захода в карточки (быстро)
"""
import argparse
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BASE = "https://books.toscrape.com/"
RATING = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
log = logging.getLogger("parser")


@dataclass
class Product:
    title: str
    url: str
    price: float
    rating: int
    in_stock: bool
    image: str
    category: str = ""
    upc: str = ""
    stock_qty: int | None = None
    reviews: int | None = None
    description: str = ""
    errors: list[str] = field(default_factory=list)


class Client:
    """HTTP с повторами, паузой между запросами и общим соединением."""

    def __init__(self, delay: float = 0.1, retries: int = 3, timeout: int = 20):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (demo catalog parser)"
        self.delay, self.retries, self.timeout = delay, retries, timeout

    def soup(self, url: str) -> BeautifulSoup:
        for attempt in range(1, self.retries + 1):
            try:
                r = self.s.get(url, timeout=self.timeout)
                r.raise_for_status()
                time.sleep(self.delay)
                return BeautifulSoup(r.content, "lxml", from_encoding="utf-8")
            except requests.RequestException as e:
                if attempt == self.retries:
                    raise
                wait = 2 ** attempt
                log.warning("%s — %s, повтор через %s с", url, e, wait)
                time.sleep(wait)
        raise RuntimeError("unreachable")


def money(text: str) -> float:
    return float(re.sub(r"[^\d.]", "", text))


def category_url(client: Client, name: str) -> str:
    soup = client.soup(BASE)
    for a in soup.select(".side_categories ul ul a"):
        if a.get_text(strip=True).lower() == name.lower():
            return urljoin(BASE, a["href"])
    cats = [a.get_text(strip=True) for a in soup.select(".side_categories ul ul a")]
    raise SystemExit(f"Категория «{name}» не найдена. Есть: {', '.join(cats)}")


def parse_listing(client: Client, start_url: str, max_pages: int | None) -> list[Product]:
    items, url, page = [], start_url, 0
    while url and (max_pages is None or page < max_pages):
        page += 1
        soup = client.soup(url)
        for pod in soup.select("article.product_pod"):
            a = pod.select_one("h3 a")
            items.append(Product(
                title=a["title"],
                url=urljoin(url, a["href"]),
                price=money(pod.select_one(".price_color").text),
                rating=RATING.get(pod.select_one(".star-rating")["class"][1], 0),
                in_stock="In stock" in pod.select_one(".availability").text,
                image=urljoin(url, pod.select_one("img")["src"]),
            ))
        nxt = soup.select_one("li.next a")
        url = urljoin(url, nxt["href"]) if nxt else None
        log.info("Страница %d: всего %d товаров", page, len(items))
    return items


def parse_details(client: Client, p: Product) -> Product:
    try:
        soup = client.soup(p.url)
        table = {th.text.strip(): td.text.strip() for th, td in
                 zip(soup.select("table.table th"), soup.select("table.table td"))}
        crumbs = soup.select("ul.breadcrumb li a")
        p.category = crumbs[2].text.strip() if len(crumbs) > 2 else ""
        p.upc = table.get("UPC", "")
        m = re.search(r"(\d+) available", table.get("Availability", ""))
        p.stock_qty = int(m.group(1)) if m else 0
        p.reviews = int(table.get("Number of reviews", "0") or 0)
        desc = soup.select_one("#product_description + p")
        p.description = desc.text.strip().removesuffix("...more").strip() if desc else ""
    except Exception as e:  # одна битая карточка не должна ронять весь сбор
        p.errors.append(str(e))
        log.warning("Карточка %s: %s", p.url, e)
    return p


# ---------------- Excel ----------------

HEAD_FILL = PatternFill("solid", fgColor="1F3A5F")
HEAD_FONT = Font(bold=True, color="FFFFFF")
THIN = Side(style="thin", color="D9DEE5")


def style_header(ws, row: int = 1):
    for c in ws[row]:
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def save_excel(products: list[Product], path: str, source: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Товары"
    cols = [("№", 6), ("Название", 46), ("Категория", 18), ("Цена, £", 10), ("Рейтинг", 9),
            ("В наличии", 11), ("Остаток, шт", 11), ("Отзывы", 9), ("Артикул (UPC)", 19),
            ("Ссылка", 12), ("Фото", 10), ("Описание", 70)]
    ws.append([c[0] for c in cols])
    for i, (_, w) in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    style_header(ws)

    for n, p in enumerate(products, 1):
        ws.append([n, p.title, p.category, p.price, "★" * p.rating, "да" if p.in_stock else "нет",
                   p.stock_qty, p.reviews, p.upc, "открыть", "фото", p.description[:1000]])
        r = ws.max_row
        ws.cell(r, 10).hyperlink, ws.cell(r, 10).style = p.url, "Hyperlink"
        ws.cell(r, 11).hyperlink, ws.cell(r, 11).style = p.image, "Hyperlink"
        ws.cell(r, 4).number_format = "0.00"
        for c in ws[r]:
            c.border = Border(bottom=THIN)
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (2, 12))
        ws.cell(r, 5).font = Font(color="E0A100")
    last = ws.max_row
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last}"
    if last > 1:  # цветовая шкала по цене: дешёвые — зелёные, дорогие — красные
        ws.conditional_formatting.add(f"D2:D{last}", ColorScaleRule(
            start_type="min", start_color="C6EFCE", mid_type="percentile", mid_value=50,
            mid_color="FFF2CC", end_type="max", end_color="F8CBAD"))

    # ---- сводка по категориям ----
    sm = wb.create_sheet("Сводка")
    sm.append([f"Источник: {source}"])
    sm.append([f"Собрано: {datetime.now():%d.%m.%Y %H:%M}, товаров: {len(products)}"])
    sm.append([])
    sm.append(["Категория", "Товаров", "Средняя цена, £", "Мин, £", "Макс, £", "Средний рейтинг", "Остаток, шт"])
    style_header(sm, 4)
    groups: dict[str, list[Product]] = {}
    for p in products:
        groups.setdefault(p.category or "—", []).append(p)
    for cat, ps in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        prices = [p.price for p in ps]
        sm.append([cat, len(ps), round(sum(prices) / len(ps), 2), min(prices), max(prices),
                   round(sum(p.rating for p in ps) / len(ps), 2), sum(p.stock_qty or 0 for p in ps)])
    for i, w in enumerate([24, 10, 16, 10, 10, 16, 13], 1):
        sm.column_dimensions[get_column_letter(i)].width = w
    sm["A1"].font = Font(bold=True, size=13)
    sm.freeze_panes = "A5"

    top = min(len(groups), 15)
    if top:
        ch = BarChart()
        ch.type, ch.title, ch.style = "bar", "Средняя цена по категориям (топ по числу товаров)", 10
        ch.y_axis.title, ch.legend = "£", None
        ch.add_data(Reference(sm, min_col=3, min_row=4, max_row=4 + top), titles_from_data=True)
        ch.set_categories(Reference(sm, min_col=1, min_row=5, max_row=4 + top))
        ch.height, ch.width = 2 + top * 0.6, 18
        sm.add_chart(ch, "I4")

    errs = [p for p in products if p.errors]
    if errs:
        es = wb.create_sheet("Ошибки")
        es.append(["Ссылка", "Ошибка"])
        style_header(es)
        for p in errs:
            es.append([p.url, "; ".join(p.errors)])
    wb.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Парсер каталога books.toscrape.com в Excel")
    ap.add_argument("--pages", type=int, help="сколько страниц каталога собрать (по умолчанию все)")
    ap.add_argument("--category", help="собрать только одну категорию, например Poetry")
    ap.add_argument("--no-details", action="store_true", help="не заходить в карточки товаров")
    ap.add_argument("--workers", type=int, default=8, help="параллельных запросов к карточкам")
    ap.add_argument("--delay", type=float, default=0.1, help="пауза после каждого запроса, с")
    ap.add_argument("--out", help="имя файла (по умолчанию products_ДАТА.xlsx)")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # кириллица в логах при выводе в файл/пайп на Windows
        stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(format="%(asctime)s %(message)s", datefmt="%H:%M:%S", level=logging.INFO)

    t0 = time.time()
    client = Client(delay=a.delay)
    start = category_url(client, a.category) if a.category else urljoin(BASE, "catalogue/page-1.html")
    products = parse_listing(client, start, a.pages)
    if not a.no_details:
        done = 0
        with ThreadPoolExecutor(a.workers) as pool:
            for _ in as_completed([pool.submit(parse_details, client, p) for p in products]):
                done += 1
                if done % 50 == 0 or done == len(products):
                    log.info("Карточки: %d/%d", done, len(products))
    out = a.out or f"products_{datetime.now():%Y-%m-%d_%H%M}.xlsx"
    save_excel(products, out, start)
    bad = sum(1 for p in products if p.errors)
    log.info("Готово: %d товаров → %s за %.0f с%s", len(products), out, time.time() - t0,
             f" (ошибок: {bad}, см. лист «Ошибки»)" if bad else "")
    return products


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
