"""Хранение клиентов и записей в SQLite."""
import csv
import io
import sqlite3
from datetime import date, datetime, timedelta

FMT = "%Y-%m-%d %H:%M"


class Storage:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS clients (
                user_id INTEGER PRIMARY KEY, name TEXT, username TEXT, phone TEXT);
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL, service_id TEXT NOT NULL, service_name TEXT NOT NULL,
                price INTEGER NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active', reminded INTEGER NOT NULL DEFAULT 0,
                created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ix_start ON bookings(start);
        """)

    # ---------- клиенты ----------
    def save_client(self, user_id: int, name: str, username: str | None, phone: str):
        self.db.execute(
            "INSERT INTO clients(user_id, name, username, phone) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username, phone=excluded.phone",
            (user_id, name, username, phone))

    def client(self, user_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM clients WHERE user_id=?", (user_id,)).fetchone()

    # ---------- записи ----------
    def add_booking(self, user_id: int, svc: dict, start: datetime, now: datetime) -> int | None:
        """Создаёт запись. Возвращает id или None, если время уже заняли."""
        end = start + timedelta(minutes=svc["minutes"])
        s, e = start.strftime(FMT), end.strftime(FMT)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            clash = self.db.execute(
                "SELECT 1 FROM bookings WHERE status='active' AND start < ? AND end > ?", (e, s)).fetchone()
            if clash:
                self.db.execute("ROLLBACK")
                return None
            cur = self.db.execute(
                "INSERT INTO bookings(user_id, service_id, service_name, price, start, end, created) "
                "VALUES (?,?,?,?,?,?,?)",
                (user_id, svc["id"], svc["name"], svc["price"], s, e, now.strftime(FMT)))
            self.db.execute("COMMIT")
            return cur.lastrowid
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def busy(self, day: date) -> list[tuple[datetime, datetime]]:
        rows = self.db.execute(
            "SELECT start, end FROM bookings WHERE status='active' AND substr(start,1,10)=?",
            (day.isoformat(),)).fetchall()
        return [(datetime.strptime(r["start"], FMT), datetime.strptime(r["end"], FMT)) for r in rows]

    def get(self, booking_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT b.*, c.name, c.username, c.phone FROM bookings b "
            "LEFT JOIN clients c USING(user_id) WHERE b.id=?", (booking_id,)).fetchone()

    def upcoming_for_user(self, user_id: int, now: datetime) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM bookings WHERE user_id=? AND status='active' AND start>=? ORDER BY start",
            (user_id, now.strftime(FMT))).fetchall()

    def between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT b.*, c.name, c.username, c.phone FROM bookings b LEFT JOIN clients c USING(user_id) "
            "WHERE b.status='active' AND b.start>=? AND b.start<? ORDER BY b.start",
            (start.strftime(FMT), end.strftime(FMT))).fetchall()

    def cancel(self, booking_id: int, user_id: int | None = None) -> bool:
        """Отменяет запись. Если передан user_id — только свою."""
        sql = "UPDATE bookings SET status='cancelled' WHERE id=? AND status='active'"
        args: tuple = (booking_id,)
        if user_id is not None:
            sql += " AND user_id=?"
            args += (user_id,)
        return self.db.execute(sql, args).rowcount == 1

    def due_reminders(self, now: datetime, before_min: int) -> list[sqlite3.Row]:
        limit = now + timedelta(minutes=before_min)
        return self.db.execute(
            "SELECT * FROM bookings WHERE status='active' AND reminded=0 AND start>? AND start<=?",
            (now.strftime(FMT), limit.strftime(FMT))).fetchall()

    def mark_reminded(self, booking_id: int):
        self.db.execute("UPDATE bookings SET reminded=1 WHERE id=?", (booking_id,))

    def export_csv(self) -> bytes:
        rows = self.db.execute(
            "SELECT b.id, b.start, b.end, b.service_name, b.price, b.status, c.name, c.phone, c.username "
            "FROM bookings b LEFT JOIN clients c USING(user_id) ORDER BY b.start").fetchall()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["№", "Начало", "Конец", "Услуга", "Цена", "Статус", "Имя", "Телефон", "Telegram"])
        for r in rows:
            w.writerow([r["id"], r["start"], r["end"], r["service_name"], r["price"],
                        "активна" if r["status"] == "active" else "отменена",
                        r["name"], r["phone"], f"@{r['username']}" if r["username"] else ""])
        return buf.getvalue().encode("utf-8-sig")  # utf-8-sig — чтобы Excel не ломал кириллицу
