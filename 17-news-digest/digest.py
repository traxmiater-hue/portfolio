"""Сборщик новостей по ключевым словам: RSS нескольких сайтов → фильтр → Excel и дайджест в Telegram.

Источники и слова — в config.json. Дубли (одна новость на разных сайтах) отсеиваются по похожести заголовков.

    python digest.py                  # собрать за последние 24 часа
    python digest.py --hours 6
    TG_TOKEN=... TG_CHAT=... python digest.py
"""
import argparse
import email.utils
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).parent
log = logging.getLogger("digest")


def fetch(name: str, url: str) -> list[dict]:
    r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (news digest demo)"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        title = html.unescape((it.findtext("title") or "").strip())
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        pub = it.findtext("pubDate")
        out.append({"source": name, "title": title, "text": " ".join(desc.split())[:400], "url": it.findtext("link"),
                    "date": email.utils.parsedate_to_datetime(pub) if pub else datetime.now(timezone.utc)})
    return out


def matches(item: dict, words: list[str]) -> list[str]:
    hay = (item["title"] + " " + item["text"]).lower()
    return [w for w in words if re.search(rf"\b{re.escape(w.lower())}", hay)]


def dedupe(items: list[dict]) -> list[dict]:
    kept = []
    for it in sorted(items, key=lambda x: x["date"], reverse=True):
        if all(SequenceMatcher(None, it["title"].lower(), k["title"].lower()).ratio() < .72 for k in kept):
            kept.append(it)
    return kept


def excel(items: list[dict], path: Path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Дайджест"
    ws.append(["Время", "Источник", "Заголовок", "Ключевые слова", "Кратко", "Ссылка"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="3D2C8D")
    for it in items:
        ws.append([it["date"].astimezone().strftime("%d.%m %H:%M"), it["source"], it["title"], ", ".join(it["hits"]),
                   it["text"], it["url"]])
        ws.cell(ws.max_row, 6).hyperlink = it["url"]
    for col, w in zip("ABCDEF", [12, 12, 60, 22, 70, 40]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes, ws.auto_filter.ref = "A2", ws.dimensions
    wb.save(path)


def telegram(items: list[dict]):
    token, chat = os.getenv("TG_TOKEN"), os.getenv("TG_CHAT")
    if not (token and chat) or not items:
        return
    text = f"🗞 Дайджест: {len(items)} новостей по вашим темам\n\n" + "\n\n".join(
        f"• <a href=\"{it['url']}\">{html.escape(it['title'])}</a> — {it['source']}" for it in items[:15])
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                  data={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    since = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    found, total = [], 0
    for name, url in cfg["sources"].items():
        try:
            items = fetch(name, url)
        except Exception as e:
            log.warning("%s: %s", name, e)
            continue
        total += len(items)
        for it in items:
            it["hits"] = matches(it, cfg["keywords"])
            if it["hits"] and it["date"] >= since:
                found.append(it)
        log.info("%s: %d новостей", name, len(items))
    items = dedupe(found)
    path = ROOT / f"digest_{datetime.now():%Y-%m-%d}.xlsx"
    excel(items, path)
    telegram(items)
    log.info("Просмотрено %d, по ключевым словам %d, без дублей %d → %s", total, len(found), len(items), path.name)


if __name__ == "__main__":
    main()
