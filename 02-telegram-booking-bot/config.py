"""Настройки бизнеса. Чтобы адаптировать бота под другой салон — меняйте только этот файл."""
from datetime import time

BUSINESS_NAME = "барбершопа «Лезвие»"
ADDRESS = "ул. Садовая, 12 (вход со двора), м. Сенная"
PHONE = "+7 900 123-45-67"

# id — короткий ключ для кнопок, minutes — длительность, price — цена в рублях
SERVICES = [
    {"id": "cut", "name": "Мужская стрижка", "minutes": 60, "price": 1500},
    {"id": "beard", "name": "Оформление бороды", "minutes": 30, "price": 800},
    {"id": "combo", "name": "Стрижка + борода", "minutes": 90, "price": 2100},
    {"id": "kid", "name": "Детская стрижка", "minutes": 45, "price": 1100},
]

OPEN_TIME = time(10, 0)
CLOSE_TIME = time(20, 0)
SLOT_STEP_MIN = 30          # шаг сетки времени
DAYS_AHEAD = 7              # на сколько дней вперёд можно записаться
DAYS_OFF = {0}              # выходные: 0 = понедельник ... 6 = воскресенье
MIN_LEAD_MIN = 60           # нельзя записаться раньше, чем за час до начала
REMIND_BEFORE_MIN = 120     # напоминание за 2 часа

DB_PATH = "bookings.db"


def service(service_id: str) -> dict | None:
    return next((s for s in SERVICES if s["id"] == service_id), None)
