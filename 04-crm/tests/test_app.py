import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import create_app  # noqa: E402
from seed import seed  # noqa: E402


@pytest.fixture
def app(tmp_path):
    path = str(tmp_path / "t.db")
    seed(path, n=60)
    return create_app({"DATABASE": path, "TESTING": True, "API_TOKEN": "tok"})


def csrf(client, url="/login"):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


def login(client, who="admin"):
    t = csrf(client)
    r = client.post("/login", data={"login": who, "password": "demo123", "csrf": t})
    assert r.status_code == 302
    return t


def test_requires_login(app):
    c = app.test_client()
    assert c.get("/").status_code == 302
    assert c.get("/leads").headers["Location"].startswith("/login")


def test_bad_password_and_csrf(app):
    c = app.test_client()
    t = csrf(c)
    r = c.post("/login", data={"login": "admin", "password": "nope", "csrf": t})
    assert "Неверный логин" in r.get_data(as_text=True)
    assert c.post("/login", data={"login": "admin", "password": "demo123"}).status_code == 400  # без CSRF


def test_pages_render(app):
    c = app.test_client()
    login(c)
    for url in ["/", "/leads", "/board", "/leads/new", "/leads/1", "/leads?status=won&sort=amount",
                "/leads?q=Иван&page=2", "/leads?manager=none"]:
        assert c.get(url).status_code == 200, url
    assert c.get("/leads/99999").status_code == 404


def test_create_edit_comment_history(app):
    c = app.test_client()
    t = login(c)
    r = c.post("/leads/new", data={"csrf": t, "name": "Тест Тестов", "phone": "+7 900 000-00-00",
                                   "source": "Сайт", "status": "new", "amount": "0"})
    assert r.status_code == 302
    lid = int(r.headers["Location"].rsplit("/", 1)[1])
    c.post(f"/leads/{lid}", data={"csrf": t, "name": "Тест Тестов", "phone": "+7 900 000-00-00",
                                  "source": "Сайт", "status": "offer", "amount": "45000"})
    c.post(f"/leads/{lid}/comment", data={"csrf": t, "text": "Отправил КП"})
    html = c.get(f"/leads/{lid}").get_data(as_text=True)
    assert "Новая → КП отправлено" in html and "Отправил КП" in html and "45 000 ₽" in html


def test_validation(app):
    c = app.test_client()
    t = login(c)
    r = c.post("/leads/new", data={"csrf": t, "name": "", "source": "???", "amount": "abc"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Укажите имя" in html and "Нужен телефон или email" in html and "Выберите источник" in html


def test_board_status_api(app):
    c = app.test_client()
    t = login(c)
    assert c.post("/api/leads/1/status", json={"status": "won"}).status_code == 400  # без CSRF-заголовка
    r = c.post("/api/leads/1/status", json={"status": "won"}, headers={"X-CSRF": t})
    assert r.json == {"ok": True}
    assert "Успешно" in c.get("/leads/1").get_data(as_text=True)
    assert c.post("/api/leads/1/status", json={"status": "zzz"}, headers={"X-CSRF": t}).status_code == 400


def test_delete_only_admin(app):
    c = app.test_client()
    t = login(c, "manager")
    assert c.post("/leads/2/delete", data={"csrf": t}).status_code == 403
    c2 = app.test_client()
    t2 = login(c2, "admin")
    assert c2.post("/leads/2/delete", data={"csrf": t2}).status_code == 302
    assert c2.get("/leads/2").status_code == 404


def test_public_api_and_export(app):
    c = app.test_client()
    assert c.post("/api/public/leads", json={"name": "A", "phone": "1"}).status_code == 401
    assert c.post("/api/public/leads", json={"name": ""}, headers={"X-Token": "tok"}).status_code == 400
    r = c.post("/api/public/leads", json={"name": "С сайта", "phone": "+79990001122", "note": "Лендинг курса"},
               headers={"X-Token": "tok"})
    assert r.status_code == 201
    login(c)
    csv = c.get("/leads/export.csv?q=С сайта").get_data().decode("utf-8-sig")
    assert "С сайта" in csv and "Лендинг курса" in csv and csv.count("\n") == 2
