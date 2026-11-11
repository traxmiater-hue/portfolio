"""SQLite: схема, подключение, справочники."""
import sqlite3
from datetime import datetime

from flask import current_app, g

STATUSES = {  # ключ: (название, цвет)
    "new": ("Новая", "#3b82f6"),
    "work": ("В работе", "#f59e0b"),
    "offer": ("КП отправлено", "#8b5cf6"),
    "won": ("Успешно", "#16a34a"),
    "lost": ("Отказ", "#9ca3af"),
}
SOURCES = ["Сайт", "Telegram", "Авито", "Звонок", "Рекомендация"]
FMT = "%Y-%m-%d %H:%M:%S"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, login TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'manager');
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, email TEXT, company TEXT,
    source TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', amount INTEGER NOT NULL DEFAULT 0,
    manager_id INTEGER REFERENCES users(id), note TEXT DEFAULT '',
    created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id), kind TEXT NOT NULL, text TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS ix_leads_created ON leads(created);
CREATE INDEX IF NOT EXISTS ix_events_lead ON events(lead_id);
"""


def now() -> str:
    return datetime.now().strftime(FMT)


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_=None):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def init_schema(con: sqlite3.Connection):
    con.executescript(SCHEMA)
    con.commit()


def log_event(con, lead_id: int, user_id: int | None, kind: str, text: str):
    con.execute("INSERT INTO events(lead_id, user_id, kind, text, created) VALUES (?,?,?,?,?)",
                (lead_id, user_id, kind, text, now()))
