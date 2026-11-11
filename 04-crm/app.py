"""Мини-CRM для заявок: Flask + SQLite.

    python seed.py        # демо-данные
    python app.py         # http://127.0.0.1:5000  (admin / demo123)
"""
import csv
import io
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash

from db import FMT, SOURCES, STATUSES, close_db, get_db, init_schema, connect, log_event, now

PER_PAGE = 20
SORTS = {"new": "l.created DESC", "old": "l.created ASC", "amount": "l.amount DESC", "updated": "l.updated DESC"}


def rub(v) -> str:
    return f"{int(v or 0):,}".replace(",", " ") + " ₽"


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("CRM_SECRET", "dev-only-change-me"),
        DATABASE=os.getenv("CRM_DB", os.path.join(app.root_path, "crm.db")),
        API_TOKEN=os.getenv("CRM_API_TOKEN", "demo-token"),
    )
    if test_config:
        app.config.update(test_config)
    app.teardown_appcontext(close_db)
    with app.app_context():
        con = connect(app.config["DATABASE"])
        init_schema(con)
        con.close()

    app.jinja_env.filters["rub"] = rub
    app.jinja_env.filters["dt"] = lambda s: datetime.strptime(s, FMT).strftime("%d.%m.%Y %H:%M") if s else ""
    app.jinja_env.filters["d"] = lambda s: datetime.strptime(s, FMT).strftime("%d.%m") if s else ""

    @app.context_processor
    def globals_():
        return {"STATUSES": STATUSES, "SOURCES": SOURCES, "user": g.get("user"), "csrf": session.get("csrf")}

    # ---------- безопасность ----------
    @app.before_request
    def load_user_and_csrf():
        g.user = None
        if uid := session.get("uid"):
            g.user = get_db().execute("SELECT id, login, name, role FROM users WHERE id=?", (uid,)).fetchone()
        session.setdefault("csrf", secrets.token_hex(16))
        if request.method == "POST" and not request.path.startswith("/api/public/"):
            sent = request.form.get("csrf") or request.headers.get("X-CSRF")
            if sent != session["csrf"]:
                abort(400, "CSRF")

    def login_required(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                return redirect(url_for("login", next=request.full_path))
            return view(*a, **kw)
        return wrapped

    def managers():
        return get_db().execute("SELECT id, name FROM users ORDER BY name").fetchall()

    # ---------- вход ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = get_db().execute("SELECT * FROM users WHERE login=?", (request.form.get("login", "").strip(),)).fetchone()
            if u and check_password_hash(u["password"], request.form.get("password", "")):
                csrf = session.get("csrf")
                session.clear()
                session.update(uid=u["id"], csrf=csrf)
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            flash("Неверный логин или пароль", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- дашборд ----------
    @app.route("/")
    @login_required
    def dashboard():
        db = get_db()
        today = datetime.now()
        month = today.replace(day=1, hour=0, minute=0, second=0).strftime(FMT)
        q90 = (today - timedelta(days=90)).strftime(FMT)
        kpi = {
            "new_month": db.execute("SELECT COUNT(*) FROM leads WHERE created>=?", (month,)).fetchone()[0],
            "active": db.execute("SELECT COUNT(*) FROM leads WHERE status IN ('new','work','offer')").fetchone()[0],
            "pipeline": db.execute("SELECT COALESCE(SUM(amount),0) FROM leads WHERE status IN ('work','offer')").fetchone()[0],
            "won_month": db.execute("SELECT COALESCE(SUM(amount),0) FROM leads WHERE status='won' AND updated>=?", (month,)).fetchone()[0],
        }
        won, lost = (db.execute("SELECT COUNT(*) FROM leads WHERE status=? AND created>=?", (s, q90)).fetchone()[0]
                     for s in ("won", "lost"))
        kpi["conversion"] = round(100 * won / (won + lost)) if won + lost else 0

        # заявки по неделям за 8 недель
        monday = (today - timedelta(days=today.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        weeks = [monday - timedelta(weeks=i) for i in range(7, -1, -1)]
        rows = db.execute("SELECT created, status FROM leads WHERE created>=?", (weeks[0].strftime(FMT),)).fetchall()
        weekly = {"labels": [w.strftime("%d.%m") for w in weeks], "all": [0] * 8, "won": [0] * 8}
        for r in rows:
            i = (datetime.strptime(r["created"], FMT) - weeks[0]).days // 7
            if 0 <= i < 8:
                weekly["all"][i] += 1
                weekly["won"][i] += r["status"] == "won"

        by_status = {k: 0 for k in STATUSES} | dict(db.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall())
        by_source = db.execute(
            "SELECT source, COUNT(*) n, SUM(status='won') won, COALESCE(SUM(CASE WHEN status='won' THEN amount END),0) money "
            "FROM leads GROUP BY source ORDER BY n DESC").fetchall()
        events = db.execute(
            "SELECT e.*, l.name lead_name, u.name user_name FROM events e JOIN leads l ON l.id=e.lead_id "
            "LEFT JOIN users u ON u.id=e.user_id ORDER BY e.created DESC LIMIT 8").fetchall()
        return render_template("dashboard.html", kpi=kpi, weekly=weekly, by_status=by_status,
                               by_source=by_source, events=events,
                               today=today.strftime("%d.%m.%Y"))

    # ---------- список заявок ----------
    def filtered(args):
        where, params = [], []
        if q := args.get("q", "").strip():
            where.append("(l.name LIKE ? OR l.phone LIKE ? OR l.email LIKE ? OR l.company LIKE ? OR l.note LIKE ?)")
            params += [f"%{q}%"] * 5
        if (st := args.get("status")) in STATUSES:
            where.append("l.status=?"); params.append(st)
        if (src := args.get("source")) in SOURCES:
            where.append("l.source=?"); params.append(src)
        if (m := args.get("manager", "")).isdigit():
            where.append("l.manager_id=?"); params.append(int(m))
        elif m == "none":
            where.append("l.manager_id IS NULL")
        sql = " WHERE " + " AND ".join(where) if where else ""
        return sql, params

    @app.route("/leads")
    @login_required
    def leads():
        db = get_db()
        where, params = filtered(request.args)
        sort = request.args.get("sort", "new")
        total = db.execute(f"SELECT COUNT(*) FROM leads l{where}", params).fetchone()[0]
        pages = max(1, -(-total // PER_PAGE))
        page = min(max(1, request.args.get("page", 1, type=int)), pages)
        rows = db.execute(
            f"SELECT l.*, u.name manager FROM leads l LEFT JOIN users u ON u.id=l.manager_id{where} "
            f"ORDER BY {SORTS.get(sort, SORTS['new'])} LIMIT ? OFFSET ?",
            params + [PER_PAGE, (page - 1) * PER_PAGE]).fetchall()
        args = {k: v for k, v in request.args.items() if k != "page"}
        return render_template("leads.html", rows=rows, total=total, page=page, pages=pages,
                               args=args, sort=sort, managers=managers())

    @app.route("/leads/export.csv")
    @login_required
    def export_csv():
        where, params = filtered(request.args)
        rows = get_db().execute(
            f"SELECT l.*, u.name manager FROM leads l LEFT JOIN users u ON u.id=l.manager_id{where} ORDER BY l.created DESC",
            params).fetchall()
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["№", "Создана", "Имя", "Телефон", "Email", "Компания", "Источник", "Статус", "Сумма", "Менеджер", "Комментарий"])
        for r in rows:
            w.writerow([r["id"], r["created"], r["name"], r["phone"], r["email"], r["company"], r["source"],
                        STATUSES[r["status"]][0], r["amount"], r["manager"] or "", r["note"]])
        return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=leads_{datetime.now():%Y-%m-%d}.csv"})

    # ---------- карточка ----------
    def read_form() -> tuple[dict, list[str]]:
        f = request.form
        data = {
            "name": f.get("name", "").strip(), "phone": f.get("phone", "").strip(),
            "email": f.get("email", "").strip(), "company": f.get("company", "").strip(),
            "source": f.get("source"), "status": f.get("status", "new"), "note": f.get("note", "").strip(),
            "amount": int(f.get("amount") or 0) if (f.get("amount") or "0").isdigit() else -1,
            "manager_id": int(f["manager_id"]) if f.get("manager_id", "").isdigit() else None,
        }
        errors = []
        if not data["name"]:
            errors.append("Укажите имя клиента")
        if not (data["phone"] or data["email"]):
            errors.append("Нужен телефон или email")
        if data["source"] not in SOURCES:
            errors.append("Выберите источник")
        if data["status"] not in STATUSES:
            errors.append("Неизвестный статус")
        if data["amount"] < 0:
            errors.append("Сумма — целое число без пробелов")
        return data, errors

    @app.route("/leads/new", methods=["GET", "POST"])
    @login_required
    def lead_new():
        data = {"status": "new", "source": "Сайт", "manager_id": g.user["id"]}
        if request.method == "POST":
            data, errors = read_form()
            if not errors:
                db = get_db()
                cur = db.execute(
                    "INSERT INTO leads(name, phone, email, company, source, status, amount, manager_id, note, created, updated)"
                    " VALUES (:name,:phone,:email,:company,:source,:status,:amount,:manager_id,:note,:t,:t)",
                    data | {"t": now()})
                log_event(db, cur.lastrowid, g.user["id"], "system", "Заявка создана вручную")
                db.commit()
                flash("Заявка создана", "ok")
                return redirect(url_for("lead", lead_id=cur.lastrowid))
            for e in errors:
                flash(e, "error")
        return render_template("lead.html", lead=data, events=[], managers=managers(), is_new=True)

    def get_lead(lead_id: int):
        lead = get_db().execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if lead is None:
            abort(404)
        return lead

    @app.route("/leads/<int:lead_id>", methods=["GET", "POST"])
    @login_required
    def lead(lead_id):
        old = get_lead(lead_id)
        db = get_db()
        if request.method == "POST":
            data, errors = read_form()
            if not errors:
                db.execute("UPDATE leads SET name=:name, phone=:phone, email=:email, company=:company, source=:source,"
                           " status=:status, amount=:amount, manager_id=:manager_id, note=:note, updated=:t WHERE id=:id",
                           data | {"t": now(), "id": lead_id})
                if data["status"] != old["status"]:
                    log_event(db, lead_id, g.user["id"], "status",
                              f"Статус: {STATUSES[old['status']][0]} → {STATUSES[data['status']][0]}")
                if data["amount"] != old["amount"]:
                    log_event(db, lead_id, g.user["id"], "system", f"Сумма: {rub(old['amount'])} → {rub(data['amount'])}")
                db.commit()
                flash("Сохранено", "ok")
                return redirect(url_for("lead", lead_id=lead_id))
            for e in errors:
                flash(e, "error")
            old = dict(old) | data
        events = db.execute("SELECT e.*, u.name user_name FROM events e LEFT JOIN users u ON u.id=e.user_id "
                            "WHERE lead_id=? ORDER BY e.created DESC, e.id DESC", (lead_id,)).fetchall()
        return render_template("lead.html", lead=old, events=events, managers=managers(), is_new=False)

    @app.post("/leads/<int:lead_id>/comment")
    @login_required
    def comment(lead_id):
        get_lead(lead_id)
        text = request.form.get("text", "").strip()
        if text:
            db = get_db()
            log_event(db, lead_id, g.user["id"], "comment", text[:2000])
            db.execute("UPDATE leads SET updated=? WHERE id=?", (now(), lead_id))
            db.commit()
        return redirect(url_for("lead", lead_id=lead_id) + "#history")

    @app.post("/leads/<int:lead_id>/delete")
    @login_required
    def delete(lead_id):
        if g.user["role"] != "admin":
            abort(403)
        get_lead(lead_id)
        db = get_db()
        db.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        db.commit()
        flash(f"Заявка №{lead_id} удалена", "ok")
        return redirect(url_for("leads"))

    # ---------- канбан ----------
    @app.route("/board")
    @login_required
    def board():
        rows = get_db().execute(
            "SELECT l.*, u.name manager FROM leads l LEFT JOIN users u ON u.id=l.manager_id "
            "WHERE l.status IN ('new','work','offer') OR l.updated>=? ORDER BY l.updated DESC",
            ((datetime.now() - timedelta(days=14)).strftime(FMT),)).fetchall()
        cols = {k: [r for r in rows if r["status"] == k] for k in STATUSES}
        return render_template("board.html", cols=cols)

    @app.post("/api/leads/<int:lead_id>/status")
    @login_required
    def api_status(lead_id):
        lead = get_lead(lead_id)
        new = (request.get_json(silent=True) or {}).get("status")
        if new not in STATUSES:
            return jsonify(error="bad status"), 400
        if new != lead["status"]:
            db = get_db()
            db.execute("UPDATE leads SET status=?, updated=?, manager_id=COALESCE(manager_id, ?) WHERE id=?",
                       (new, now(), g.user["id"], lead_id))
            log_event(db, lead_id, g.user["id"], "status",
                      f"Статус: {STATUSES[lead['status']][0]} → {STATUSES[new][0]}")
            db.commit()
        return jsonify(ok=True)

    # ---------- приём заявок с сайта ----------
    @app.route("/api/public/leads", methods=["POST", "OPTIONS"])
    def public_lead():
        cors = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Content-Type, X-Token",
                "Access-Control-Allow-Methods": "POST"}
        if request.method == "OPTIONS":
            return "", 204, cors
        if not secrets.compare_digest(request.headers.get("X-Token", ""), app.config["API_TOKEN"]):
            return jsonify(error="unauthorized"), 401, cors
        d = request.get_json(silent=True) or {}
        name, phone = str(d.get("name", "")).strip()[:100], str(d.get("phone", "")).strip()[:40]
        if not name or not phone:
            return jsonify(error="name and phone required"), 400, cors
        source = d.get("source") if d.get("source") in SOURCES else "Сайт"
        db = get_db()
        cur = db.execute(
            "INSERT INTO leads(name, phone, email, source, note, created, updated) VALUES (?,?,?,?,?,?,?)",
            (name, phone, str(d.get("email", ""))[:100], source, str(d.get("note", ""))[:1000], now(), now()))
        log_event(db, cur.lastrowid, None, "system", f"Заявка пришла через API ({source})")
        db.commit()
        return jsonify(ok=True, id=cur.lastrowid), 201, cors

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404, text="Такой страницы нет"), 404

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("error.html", code=403, text="Недостаточно прав"), 403

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
