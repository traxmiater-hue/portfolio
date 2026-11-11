"""Разбор «хаотичного» описания квартиры в шаблон объявления.

Из текста вида «сдаю 1к на Кремлевская 19, 5/9 эт, 50000+ку залог 20т, можно с котом» получается:

    1к квартира
    Кремлевская 19
    50 000+ку
    Залог 20т
    5/9 эт.

    Можно с котом
"""
import re

ROOMS = re.compile(r"\b(\d)\s*-?\s*(?:к|кк|комн\w*|ком)\b\.?(?:\s*кв\w*)?|\bстуди\w*", re.I)
DEPOSIT = re.compile(r"(?:залог|депозит)\s*:?\s*(\d[\d\s]*\d|\d)\s*(т\.?р\.?|тыс\.?|т\b|к\b)?", re.I)
FLOOR = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*эт\w*\.?", re.I)
STREET = re.compile(r"(?:ул\.?\s*|улица\s+|пр-т\s*|проспект\s+)?([А-ЯЁ][а-яё]+(?:\s[А-ЯЁ][а-яё]+)?\s+\d{1,3}[а-я]?(?:/\d{1,3})?)\b")
PRICE = re.compile(r"(?:цена\s*:?\s*)?(\d[\d\s]{0,6}\d)\s*(т\.?р\.?|тыс\.?|т\b|₽|руб\w*|р\b)?\s*"
                   r"(\+\s*к\.?\s*у\.?|\+\s*коммунал\w*|\+\s*сч[её]тчик\w*|вс[её]\s+включено)?", re.I)
FILLER = re.compile(r"\b(сдаю|сдам|сдается|сдаётся|квартиру|квартира|на|цена)\b", re.I)


def _num(s: str, thousands: bool) -> int:
    n = int(re.sub(r"\D", "", s))
    return n * 1000 if thousands or n < 1000 else n


def parse(text: str) -> dict:
    t = " ".join(text.split())
    taken: list[tuple[int, int]] = []
    free = lambda m: all(m.end() <= a or m.start() >= b for a, b in taken)  # noqa: E731

    def masked() -> str:   # уже разобранные куски заменяем пробелами, чтобы их не взяли повторно
        return "".join(" " if any(a <= i < b for a, b in taken) else ch for i, ch in enumerate(t))

    def grab(rx):
        for m in rx.finditer(masked()):
            if free(m):
                taken.append(m.span())
                return m
        return None

    out = {"rooms": None, "address": None, "price": None, "utilities": "", "deposit": None, "floor": None}
    if m := grab(ROOMS):
        out["rooms"] = f"{m.group(1)}к квартира" if m.group(1) else "Студия"
    if m := grab(DEPOSIT):
        out["deposit"] = _num(m.group(1), bool(m.group(2)))
    if m := grab(FLOOR):
        out["floor"] = f"{m.group(1)}/{m.group(2)} эт."
    if m := grab(STREET):
        out["address"] = m.group(1)
    for m in PRICE.finditer(masked()):
        val = _num(m.group(1), bool(m.group(2)) and m.group(2).lower().startswith("т"))
        if free(m) and 3000 <= val <= 900000:
            taken.append(m.span())
            out["price"] = val
            u = (m.group(3) or "").lower()
            out["utilities"] = " + счётчики" if "сч" in u else ", всё включено" if "включ" in u else \
                "+ку" if u else ""
            break
    rest = "".join(ch if all(not a <= i < b for a, b in taken) else " " for i, ch in enumerate(t))
    rest = FILLER.sub(" ", rest)
    parts = [p.strip(" .;:-") for p in re.split(r"[,.;]\s*|\s{2,}", rest)]
    extra = ", ".join(p for p in parts if len(p) > 2)
    out["extra"] = extra[:1].upper() + extra[1:] if extra else ""
    return out


def render(d: dict) -> str:
    rub = lambda n: f"{n:,}".replace(",", " ")  # noqa: E731
    lines = [d["rooms"] or "Квартира", d["address"] or "Адрес уточняйте"]
    if d["price"]:
        lines.append(f"{rub(d['price'])}{d['utilities']}")
    if d["deposit"]:
        lines.append(f"Залог {d['deposit'] // 1000}т" if d["deposit"] % 1000 == 0 else f"Залог {rub(d['deposit'])}")
    if d["floor"]:
        lines.append(d["floor"])
    if d["extra"]:
        lines += ["", d["extra"]]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(parse("сдаю 1к квартиру на Кремлевская 19, 5/9 эт, свежий ремонт, 50000+ку залог 20т, можно с котом")))
