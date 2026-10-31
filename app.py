"""ShareLab - trusted sharing of underused labs, equipment and spaces. Flask web layer."""
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import core
from core import (ACTION_LABELS, COND_RANK, CONDITIONS, KNOWN_QUALIFICATIONS, OUTCOME_TEXT,
                  PURPOSE_TYPES, can_manage, db, ex, log, notify, now, pt, q, q1, ts)

BASE = os.path.dirname(os.path.abspath(__file__))
ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def create_app(db_path=None):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me-" + "x" * 16)
    app.config["DB_PATH"] = db_path or os.environ.get("SHARELAB_DB", os.path.join(BASE, "instance", "sharelab.db"))
    app.config["UPLOAD_DIR"] = os.path.join(os.path.dirname(app.config["DB_PATH"]), "uploads")
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["DEMO_MODE"] = os.environ.get("DEMO_MODE", "1") == "1"
    os.makedirs(os.path.dirname(app.config["DB_PATH"]), exist_ok=True)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    with app.app_context():
        import seed
        seed.init_db()

    # ------------------------------------------------------------ plumbing
    @app.before_request
    def load():
        g.user = None
        if session.get("uid"):
            g.user = q1("SELECT * FROM users WHERE id=?", (session["uid"],))
            if g.user is None:
                session.clear()
        if request.endpoint != "static":
            core.sweep()
        if request.method == "POST":
            tok = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not tok or tok != session.get("csrf"):
                abort(400, "Invalid or missing CSRF token. Reload the page and try again.")

    @app.after_request
    def commit(resp):
        if "db" in g:
            g.db.commit()
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.teardown_appcontext
    def close(_):
        d = g.pop("db", None)
        if d is not None:
            d.close()

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        unread = 0
        if g.get("user"):
            unread = q1("SELECT COUNT(*) c FROM notifications WHERE user_id=? AND is_read=0", (g.user["id"],))["c"]
        return dict(me=g.get("user"), csrf=session["csrf"], unread=unread, now_ts=ts(now()), demo=app.config["DEMO_MODE"],
                    clock_offset=int(core.get_setting("clock_offset_min", "0") or 0), ACTION_LABELS=ACTION_LABELS,
                    can_manage=can_manage, PURPOSE_TYPES=PURPOSE_TYPES, CONDITIONS=CONDITIONS, DAYS=DAYS)

    @app.template_filter("dt")
    def fmt_dt(s):
        if not s:
            return "-"
        try:
            return pt(s[:16]).strftime("%a %d %b, %H:%M")
        except ValueError:
            return s

    @app.template_filter("d")
    def fmt_d(s):
        try:
            return pt(s[:16]).strftime("%a %d %b")
        except (ValueError, TypeError):
            return s or "-"

    @app.template_filter("num")
    def fmt_num(v):
        return f"{v:g}"

    def login_required(f):
        @wraps(f)
        def w(*a, **k):
            if not g.user:
                flash("Please sign in to continue.", "warn")
                return redirect(url_for("login", next=request.path))
            return f(*a, **k)
        return w

    def roles(*rs):
        def deco(f):
            @wraps(f)
            def w(*a, **k):
                if not g.user:
                    return redirect(url_for("login", next=request.path))
                if g.user["role"] not in rs:
                    abort(403)
                return f(*a, **k)
            return w
        return deco

    def res_or_404(rid):
        r = q1("SELECT * FROM resources WHERE id=?", (rid,))
        if not r:
            abort(404)
        return r

    def booking_ctx(bid):
        b = core.get_booking(bid)
        if not b:
            abort(404)
        res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
        if g.user["id"] != b["user_id"] and not can_manage(g.user, b["resource_id"]):
            abort(403)
        return b, res

    # ------------------------------------------------------------ public / auth
    @app.route("/")
    def home():
        stats = dict(resources=q1("SELECT COUNT(*) c FROM resources WHERE active=1")["c"],
                     bookings=q1("SELECT COUNT(*) c FROM bookings WHERE status='completed'")["c"],
                     hours=round(sum(core.hours_between(r["start_ts"], r["end_ts"]) for r in q("SELECT start_ts,end_ts FROM bookings WHERE status='completed'")), 1))
        feat = q("SELECT * FROM resources WHERE active=1 ORDER BY id LIMIT 4")
        return render_template("home.html", stats=stats, feat=feat)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = q1("SELECT * FROM users WHERE email=?", (request.form.get("email", "").strip().lower(),))
            if u and check_password_hash(u["pw_hash"], request.form.get("password", "")):
                session.clear()
                session["uid"] = u["id"]
                flash(f"Welcome back, {u['name']}.", "ok")
                nxt = request.form.get("next") or ""
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
            flash("Incorrect email or password.", "err")
        demo_users = q("SELECT id,name,email,role,department FROM users ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'owner' THEN 1 WHEN 'manager' THEN 2 ELSE 3 END, id") if app.config["DEMO_MODE"] else []
        return render_template("login.html", demo_users=demo_users, next=request.args.get("next", ""))

    @app.post("/demo-login/<int:uid>")
    def demo_login(uid):
        if not app.config["DEMO_MODE"]:
            abort(404)
        u = q1("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            abort(404)
        session.clear()
        session["uid"] = u["id"]
        flash(f"Signed in as {u['name']} ({u['role']}) - demo shortcut.", "ok")
        return redirect(url_for("dashboard"))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            f = request.form
            email = f.get("email", "").strip().lower()
            if not f.get("name", "").strip() or "@" not in email or len(f.get("password", "")) < 8:
                flash("Enter your name, a valid email and a password of at least 8 characters.", "err")
            elif q1("SELECT 1 FROM users WHERE email=?", (email,)):
                flash("That email is already registered.", "err")
            else:
                role = "owner" if f.get("want_owner") else "user"
                uid = ex("INSERT INTO users(name,email,pw_hash,role,department,college_id,verified,created_at) VALUES(?,?,?,?,?,?,0,?)",
                         (f["name"].strip(), email, generate_password_hash(f["password"]), role, f.get("department", "").strip(), f.get("college_id", "").strip(), ts(now())))
                log("user_registered", uid, None, None, f"Role requested: {role}")
                for a in q("SELECT id FROM users WHERE role='admin'"):
                    notify(a["id"], f"New {role} registration from {f['name'].strip()} awaits identity verification.", "/admin")
                session.clear()
                session["uid"] = uid
                flash("Account created. An administrator must verify your college ID before you can book or list resources.", "ok")
                return redirect(url_for("dashboard"))
        return render_template("register.html")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("Signed out.", "ok")
        return redirect(url_for("home"))

    # ------------------------------------------------------------ discovery
    @app.route("/resources")
    def resources():
        f = request.args
        text, cat = f.get("q", "").strip(), f.get("category", "")
        rows = q("SELECT * FROM resources WHERE active=1 ORDER BY name")
        cats = sorted({r["category"] for r in rows})
        want_start = want_end = None
        if f.get("date") and f.get("time"):
            try:
                want_start = datetime.strptime(f["date"] + "T" + f["time"], core.FMT)
                want_end = want_start + timedelta(hours=float(f.get("hours") or 1))
            except ValueError:
                flash("That date or time was not understood.", "err")
        out = []
        for r in rows:
            if cat and r["category"] != cat:
                continue
            sc, matched = core.match_score(r, text) if text else (0, [])
            if text and sc == 0:
                continue
            elig = core.eligibility(g.user, r, now()) if g.user else None
            ok_all = bool(elig) and all(c["ok"] for c in elig)
            if f.get("eligible") and g.user and not ok_all:
                continue
            free = core.is_free_at(r, want_start, want_end) if want_start else None
            if want_start and not free:
                continue
            missing = [c["label"].replace("Required: ", "") for c in (elig or []) if not c["ok"]]
            out.append(dict(r=r, score=sc, matched=matched, eligible=ok_all if g.user else None, missing=missing, free=free,
                            reqs=[x["qualification"] for x in q("SELECT qualification FROM resource_requirements WHERE resource_id=?", (r["id"],))]))
        if text:
            out.sort(key=lambda x: (-x["score"], not x["eligible"] if x["eligible"] is not None else 0))
        return render_template("resources.html", items=out, cats=cats, f=f)

    @app.route("/resource/<int:rid>")
    def resource(rid):
        r = res_or_404(rid)
        reqs = [x["qualification"] for x in q("SELECT qualification FROM resource_requirements WHERE resource_id=?", (rid,))]
        windows = q("SELECT * FROM resource_windows WHERE resource_id=? ORDER BY weekday", (rid,))
        owner = q1("SELECT name,department FROM users WHERE id=?", (r["owner_id"],))
        mgrs = q("SELECT u.id,u.name,u.email FROM resource_managers m JOIN users u ON u.id=m.user_id WHERE m.resource_id=?", (rid,))
        elig = core.eligibility(g.user, r, now()) if g.user else None
        grid, lo, hi = core.week_grid(r)
        mine = []
        if g.user:
            mine = q("SELECT * FROM bookings WHERE resource_id=? AND user_id=? AND status IN ('pending','approved','waitlisted','checked_in') ORDER BY start_ts", (rid, g.user["id"]))
        res_usage = None
        if g.user:
            res_usage = dict(week=core.week_hours(g.user["id"], rid, now()), open=core.open_count(g.user["id"], rid))
        return render_template("resource.html", r=r, reqs=reqs, windows=windows, owner=owner, mgrs=mgrs, elig=elig, grid=grid, lo=lo, hi=hi,
                               mine=mine, usage=res_usage, check=None, form={}, manage=can_manage(g.user, rid))

    def parse_slot(form):
        start = datetime.strptime(form["date"] + "T" + form["start"], core.FMT)
        return start, start + timedelta(hours=float(form["hours"]))

    @app.post("/resource/<int:rid>/book")
    @login_required
    def book(rid):
        r = res_or_404(rid)
        if not r["active"]:
            flash("This resource is not currently open for booking.", "err")
            return redirect(url_for("resource", rid=rid))
        form = request.form
        try:
            start, end = parse_slot(form)
        except (ValueError, KeyError):
            flash("Choose a valid date, start time and duration.", "err")
            return redirect(url_for("resource", rid=rid))
        ptype = form.get("purpose_type", "project")
        purpose = form.get("purpose", "").strip()
        if ptype not in PURPOSE_TYPES:
            ptype = "project"
        ev = core.evaluate(g.user, r, start, end)
        if form.get("action") == "check":
            flash("Dry run only - nothing has been submitted.", "ok")
            return render_resource_with(rid, ev, form)
        if len(purpose) < 8:
            flash("Please describe what you will do with the resource (at least a short sentence). Owners use this to review fairly.", "err")
            return render_resource_with(rid, ev, form)
        bid, ev = core.create_booking(g.user, r, start, end, purpose, ptype)
        if bid is None:
            flash("Your request could not be submitted. See what to fix below.", "err")
            return render_resource_with(rid, ev, form)
        b = core.get_booking(bid)
        flash({"approved": "Booked! Approved automatically.", "pending": "Request submitted for review.",
               "waitlisted": "Slot is taken - you are on the waiting list."}[b["status"]], "ok")
        return redirect(url_for("booking", bid=bid))

    def render_resource_with(rid, ev, form):
        r = res_or_404(rid)
        reqs = [x["qualification"] for x in q("SELECT qualification FROM resource_requirements WHERE resource_id=?", (rid,))]
        windows = q("SELECT * FROM resource_windows WHERE resource_id=? ORDER BY weekday", (rid,))
        owner = q1("SELECT name,department FROM users WHERE id=?", (r["owner_id"],))
        mgrs = q("SELECT u.id,u.name,u.email FROM resource_managers m JOIN users u ON u.id=m.user_id WHERE m.resource_id=?", (rid,))
        elig = core.eligibility(g.user, r, now())
        grid, lo, hi = core.week_grid(r)
        mine = q("SELECT * FROM bookings WHERE resource_id=? AND user_id=? AND status IN ('pending','approved','waitlisted','checked_in') ORDER BY start_ts", (rid, g.user["id"]))
        usage = dict(week=core.week_hours(g.user["id"], rid, now()), open=core.open_count(g.user["id"], rid))
        return render_template("resource.html", r=r, reqs=reqs, windows=windows, owner=owner, mgrs=mgrs, elig=elig, grid=grid, lo=lo, hi=hi,
                               mine=mine, usage=usage, check=ev, form=form, outcome_text=OUTCOME_TEXT, manage=can_manage(g.user, rid))

    # ------------------------------------------------------------ owner: listing
    def save_resource(form, rid=None):
        name = form.get("name", "").strip()
        if len(name) < 3 or not form.get("location", "").strip():
            return None, "Give the resource a name and a location."
        vals = (name, form.get("category", "Other").strip() or "Other", form["location"].strip(), form.get("description", "").strip(),
                form.get("rules", "").strip(), form.get("tags", "").strip(),
                "auto" if form.get("approval_mode") == "auto" else "manual", 1 if form.get("supervision") else 0,
                max(0.5, float(form.get("max_hours") or 4)), max(0.5, float(form.get("weekly_cap_hours") or 8)),
                max(1, int(form.get("max_open") or 2)), max(1, int(form.get("advance_days") or 14)), form.get("emoji", "🔧").strip()[:4] or "🔧")
        if rid is None:
            rid = ex("""INSERT INTO resources(name,category,location,description,rules,tags,approval_mode,supervision,max_hours,weekly_cap_hours,max_open,advance_days,emoji,owner_id,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals + (g.user["id"], ts(now())))
            log("resource_listed", g.user["id"], None, rid, f"{name}; approval mode {vals[6]}")
        else:
            ex("""UPDATE resources SET name=?,category=?,location=?,description=?,rules=?,tags=?,approval_mode=?,supervision=?,max_hours=?,weekly_cap_hours=?,max_open=?,advance_days=?,emoji=? WHERE id=?""", vals + (rid,))
            log("resource_updated", g.user["id"], None, rid, f"Conditions updated; approval mode {vals[6]}")
        ex("DELETE FROM resource_requirements WHERE resource_id=?", (rid,))
        for qual in form.getlist("quals"):
            ex("INSERT INTO resource_requirements(resource_id,qualification) VALUES(?,?)", (rid, qual))
        extra = form.get("extra_qual", "").strip()
        if extra:
            ex("INSERT INTO resource_requirements(resource_id,qualification) VALUES(?,?)", (rid, extra))
        ex("DELETE FROM resource_windows WHERE resource_id=?", (rid,))
        for d in range(7):
            if form.get(f"open_{d}"):
                s, e = int(form.get(f"from_{d}") or 9), int(form.get(f"to_{d}") or 17)
                if e > s:
                    ex("INSERT INTO resource_windows(resource_id,weekday,start_hour,end_hour) VALUES(?,?,?,?)", (rid, d, s, e))
        return rid, None

    def all_quals():
        extra = [r["qualification"] for r in q("SELECT DISTINCT qualification FROM resource_requirements")]
        return sorted(set(KNOWN_QUALIFICATIONS) | set(extra))

    @app.route("/resources/new", methods=["GET", "POST"])
    @roles("owner", "admin")
    def resource_new():
        if not g.user["verified"]:
            flash("Only verified owners can list resources. An administrator must verify your identity first.", "err")
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            try:
                rid, err = save_resource(request.form)
            except ValueError:
                rid, err = None, "Check the numeric fields."
            if err:
                flash(err, "err")
            else:
                flash("Resource listed.", "ok")
                return redirect(url_for("resource", rid=rid))
        return render_template("resource_form.html", r=None, quals=all_quals(), sel=[], windows={}, f=request.form)

    @app.route("/resource/<int:rid>/edit", methods=["GET", "POST"])
    @login_required
    def resource_edit(rid):
        r = res_or_404(rid)
        if g.user["role"] not in ("owner", "admin") or (g.user["role"] == "owner" and r["owner_id"] != g.user["id"]):
            abort(403)
        if request.method == "POST":
            try:
                _, err = save_resource(request.form, rid)
            except ValueError:
                err = "Check the numeric fields."
            if err:
                flash(err, "err")
            else:
                flash("Conditions updated.", "ok")
                return redirect(url_for("resource", rid=rid))
        sel = [x["qualification"] for x in q("SELECT qualification FROM resource_requirements WHERE resource_id=?", (rid,))]
        wins = {w["weekday"]: w for w in q("SELECT * FROM resource_windows WHERE resource_id=?", (rid,))}
        return render_template("resource_form.html", r=r, quals=all_quals(), sel=sel, windows=wins, f={})

    @app.post("/resource/<int:rid>/toggle")
    @login_required
    def resource_toggle(rid):
        r = res_or_404(rid)
        if not can_manage(g.user, rid) or g.user["id"] not in (r["owner_id"],) and g.user["role"] != "admin":
            abort(403)
        ex("UPDATE resources SET active=1-active WHERE id=?", (rid,))
        log("resource_updated", g.user["id"], None, rid, "Listing " + ("paused" if r["active"] else "reopened"))
        flash("Listing " + ("paused." if r["active"] else "reopened."), "ok")
        return redirect(url_for("resource", rid=rid))

    @app.post("/resource/<int:rid>/managers")
    @login_required
    def resource_managers(rid):
        r = res_or_404(rid)
        if g.user["id"] != r["owner_id"] and g.user["role"] != "admin":
            abort(403)
        email = request.form.get("email", "").strip().lower()
        if request.form.get("remove"):
            uid = int(request.form["remove"])
            ex("DELETE FROM resource_managers WHERE resource_id=? AND user_id=?", (rid, uid))
            log("manager_removed", g.user["id"], None, rid, f"user {uid}")
            flash("Manager removed.", "ok")
        else:
            u = q1("SELECT * FROM users WHERE email=? AND role IN ('manager','owner')", (email,))
            if not u or not u["verified"]:
                flash("No verified staff account with that email. Managers must hold a staff (manager) account that an admin has verified.", "err")
            else:
                ex("INSERT OR IGNORE INTO resource_managers(resource_id,user_id) VALUES(?,?)", (rid, u["id"]))
                log("manager_added", g.user["id"], None, rid, u["name"])
                notify(u["id"], f"You were added as a manager of {r['name']}.", f"/resource/{rid}")
                flash(f"{u['name']} can now review requests for this resource.", "ok")
        return redirect(url_for("resource", rid=rid))

    # ------------------------------------------------------------ user dashboard & credentials
    @app.route("/dashboard")
    @login_required
    def dashboard():
        u = g.user
        mine = q("""SELECT b.*, r.name rname, r.emoji FROM bookings b JOIN resources r ON r.id=b.resource_id
                    WHERE b.user_id=? ORDER BY b.start_ts DESC""", (u["id"],))
        active = [b for b in mine if b["status"] in ("pending", "approved", "waitlisted", "checked_in", "returned")]
        active.sort(key=lambda b: b["start_ts"])
        history = [b for b in mine if b["status"] not in ("pending", "approved", "waitlisted", "checked_in", "returned")]
        creds = q("SELECT * FROM credentials WHERE user_id=? ORDER BY id DESC", (u["id"],))
        usage = q("""SELECT r.id,r.name,r.weekly_cap_hours FROM resources r WHERE r.id IN (SELECT resource_id FROM bookings WHERE user_id=?)""", (u["id"],))
        usage = [dict(name=x["name"], id=x["id"], hrs=core.week_hours(u["id"], x["id"], now()), cap=x["weekly_cap_hours"]) for x in usage]
        mgr_counts = None
        if u["role"] in ("owner", "manager", "admin"):
            mgr_counts = manage_counts()
        owned = q("SELECT * FROM resources WHERE owner_id=?", (u["id"],)) if u["role"] in ("owner", "admin") else []
        return render_template("dashboard.html", active=active, history=history, creds=creds, usage=usage, mgr=mgr_counts, owned=owned)

    @app.route("/credentials", methods=["GET", "POST"])
    @login_required
    def credentials():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Choose the qualification you are submitting.", "err")
            else:
                path = None
                f = request.files.get("file")
                if f and f.filename:
                    ext = os.path.splitext(f.filename)[1].lower()
                    if ext not in ALLOWED_EXT:
                        flash("Upload a PDF, PNG or JPG (max 2 MB).", "err")
                        return redirect(url_for("credentials"))
                    path = uuid.uuid4().hex + "_" + secure_filename(f.filename)
                    f.save(os.path.join(app.config["UPLOAD_DIR"], path))
                exp = request.form.get("expires_on") or None
                cid = ex("INSERT INTO credentials(user_id,name,ref,file_path,expires_on,created_at) VALUES(?,?,?,?,?,?)",
                         (g.user["id"], name, request.form.get("ref", "").strip(), path, exp, ts(now())))
                log("credential_submitted", g.user["id"], None, None, f"#{cid} {name}")
                for rq in q("SELECT DISTINCT resource_id FROM resource_requirements WHERE qualification=?", (name,)):
                    core.notify_managers(rq["resource_id"], f"{g.user['name']} submitted '{name}' for verification.", "/credentials/review")
                flash("Submitted. Staff will review it; you will be notified.", "ok")
            return redirect(url_for("credentials"))
        mine = q("SELECT * FROM credentials WHERE user_id=? ORDER BY id DESC", (g.user["id"],))
        return render_template("credentials.html", mine=mine, quals=all_quals())

    @app.route("/credentials/review")
    @roles("owner", "manager", "admin")
    def credentials_review():
        if g.user["role"] == "admin":
            rows = q("SELECT c.*,u.name uname,u.department,u.college_id FROM credentials c JOIN users u ON u.id=c.user_id ORDER BY c.status='pending' DESC, c.id DESC LIMIT 60")
        else:
            rows = q("""SELECT c.*,u.name uname,u.department,u.college_id FROM credentials c JOIN users u ON u.id=c.user_id
                        WHERE c.name IN (SELECT qualification FROM resource_requirements rr WHERE rr.resource_id IN
                        (SELECT id FROM resources WHERE owner_id=? UNION SELECT resource_id FROM resource_managers WHERE user_id=?))
                        ORDER BY c.status='pending' DESC, c.id DESC LIMIT 60""", (g.user["id"], g.user["id"]))
        return render_template("credentials_review.html", rows=rows)

    def credential_reviewer_ok(c):
        if g.user["role"] == "admin":
            return True
        if g.user["role"] not in ("owner", "manager"):
            return False
        return bool(q1("""SELECT 1 FROM resource_requirements rr WHERE rr.qualification=? AND rr.resource_id IN
                        (SELECT id FROM resources WHERE owner_id=? UNION SELECT resource_id FROM resource_managers WHERE user_id=?)""",
                       (c["name"], g.user["id"], g.user["id"])))

    @app.post("/credentials/<int:cid>/review")
    @login_required
    def credential_decide(cid):
        c = q1("SELECT * FROM credentials WHERE id=?", (cid,))
        if not c or not credential_reviewer_ok(c):
            abort(403)
        decision = "approved" if request.form.get("decision") == "approve" else "rejected"
        note = request.form.get("note", "").strip()
        if decision == "rejected" and len(note) < 5:
            flash("Tell the applicant why it was rejected.", "err")
            return redirect(url_for("credentials_review"))
        ex("UPDATE credentials SET status=?,reviewer_id=?,review_note=?,reviewed_at=? WHERE id=?", (decision, g.user["id"], note, ts(now()), cid))
        log("credential_reviewed", g.user["id"], None, None, f"#{cid} {c['name']} for user {c['user_id']}: {decision}. {note}")
        notify(c["user_id"], f"Your '{c['name']}' credential was {decision}." + (f" Note: {note}" if note else ""), "/credentials")
        flash(f"Credential {decision}.", "ok")
        return redirect(url_for("credentials_review"))

    @app.route("/credential/<int:cid>/file")
    @login_required
    def credential_file(cid):
        c = q1("SELECT * FROM credentials WHERE id=?", (cid,))
        if not c or not c["file_path"]:
            abort(404)
        if g.user["id"] != c["user_id"] and not credential_reviewer_ok(c):
            abort(403)
        return send_file(os.path.join(app.config["UPLOAD_DIR"], os.path.basename(c["file_path"])))

    # ------------------------------------------------------------ manager queue
    def my_resource_ids():
        if g.user["role"] == "admin":
            return [r["id"] for r in q("SELECT id FROM resources")]
        return [r["id"] for r in q("SELECT id FROM resources WHERE owner_id=? UNION SELECT resource_id FROM resource_managers WHERE user_id=?", (g.user["id"], g.user["id"]))]

    def manage_counts():
        ids = my_resource_ids()
        if not ids:
            return dict(pending=0, returns=0, ext=0, incidents=0, overdue=0, creds=0)
        m = ",".join("?" * len(ids))
        c = lambda sql: q1(sql.format(m), ids)["c"]
        return dict(pending=c("SELECT COUNT(*) c FROM bookings WHERE status='pending' AND resource_id IN ({})"),
                    returns=c("SELECT COUNT(*) c FROM bookings WHERE status='returned' AND resource_id IN ({})"),
                    ext=c("SELECT COUNT(*) c FROM bookings WHERE ext_status='requested' AND resource_id IN ({})"),
                    incidents=c("SELECT COUNT(*) c FROM incidents WHERE status IN ('open','disputed') AND resource_id IN ({})"),
                    overdue=c("SELECT COUNT(*) c FROM bookings WHERE status='checked_in' AND end_ts<'" + ts(now()) + "' AND resource_id IN ({})"),
                    creds=0)

    @app.route("/manage")
    @roles("owner", "manager", "admin")
    def manage():
        ids = my_resource_ids()
        m = ",".join("?" * len(ids)) or "NULL"
        base = f"""SELECT b.*, r.name rname, r.emoji, r.supervision, u.name uname, u.department, u.college_id, u.penalty_points
                   FROM bookings b JOIN resources r ON r.id=b.resource_id JOIN users u ON u.id=b.user_id WHERE b.resource_id IN ({m}) """
        pending = q(base + "AND b.status='pending' ORDER BY b.start_ts", ids)
        cards = []
        for b in pending:
            sc, parts = core.score_booking(b)
            comp = [dict(b=c, score=core.score_booking(c)[0], uname=q1("SELECT name FROM users WHERE id=?", (c["user_id"],))["name"]) for c in core.competitors(b)]
            res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
            usr = q1("SELECT * FROM users WHERE id=?", (b["user_id"],))
            live = core.evaluate(usr, res, pt(b["start_ts"]), pt(b["end_ts"]), exclude_id=b["id"])
            cards.append(dict(b=b, score=sc, parts=parts, comp=comp, checks=live["checks"], holders=live["holders"]))
        waitlist = q(base + "AND b.status='waitlisted' ORDER BY b.start_ts", ids)
        returns = q(base + "AND b.status='returned' ORDER BY b.return_at", ids)
        active = q(base + "AND b.status='checked_in' ORDER BY b.end_ts", ids)
        exts = q(base + "AND b.ext_status='requested' ORDER BY b.start_ts", ids)
        upcoming = q(base + "AND b.status='approved' ORDER BY b.start_ts", ids)
        incidents = q(f"SELECT i.*, r.name rname FROM incidents i JOIN resources r ON r.id=i.resource_id WHERE i.resource_id IN ({m}) AND i.status IN ('open','disputed') ORDER BY i.id DESC", ids)
        return render_template("manage.html", cards=cards, waitlist=waitlist, returns=returns, active=active, exts=exts, upcoming=upcoming, incidents=incidents, counts=manage_counts())

    # ------------------------------------------------------------ booking detail + actions
    @app.route("/booking/<int:bid>")
    @login_required
    def booking(bid):
        b, res = booking_ctx(bid)
        owner_view = can_manage(g.user, res["id"])
        requester = q1("SELECT * FROM users WHERE id=?", (b["user_id"],))
        sc, parts = core.score_booking(b)
        checks = json.loads(b["checks_json"] or "[]")
        timeline = q("SELECT * FROM audit_log WHERE booking_id=? ORDER BY id", (bid,))
        incidents = q("SELECT * FROM incidents WHERE booking_id=? ORDER BY id", (bid,))
        comp = core.competitors(b) if owner_view and b["status"] in ("pending", "waitlisted") else []
        comp = [dict(b=c, score=core.score_booking(c)[0]) for c in comp]
        rank = None
        if b["status"] == "waitlisted":
            wl = [x for x in core.overlapping(b["resource_id"], b["start_ts"], b["end_ts"], ("waitlisted",))]
            wl.append(b)
            wl.sort(key=lambda x: (-core.score_booking(x)[0], x["created_at"], x["id"]))
            rank = [x["id"] for x in wl].index(bid) + 1
        reqs = [x["qualification"] for x in q("SELECT qualification FROM resource_requirements WHERE resource_id=?", (res["id"],))]
        return render_template("booking.html", b=b, r=res, requester=requester, owner_view=owner_view, is_mine=g.user["id"] == b["user_id"],
                               score=sc, parts=parts, checks=checks, timeline=timeline, incidents=incidents, comp=comp, rank=rank,
                               can_checkin=core.can_check_in(b), reqs=reqs, rules_text=res["rules"], is_late=(b["status"] == "checked_in" and b["end_ts"] < ts(now())))

    def back(bid):
        return redirect(url_for("booking", bid=bid))

    @app.post("/booking/<int:bid>/decide")
    @login_required
    def booking_decide(bid):
        b, res = booking_ctx(bid)
        if not can_manage(g.user, res["id"]):
            abort(403)
        ok, msg = core.decide(bid, g.user, request.form.get("action"), request.form.get("reason"))
        flash(msg, "ok" if ok else "err")
        return redirect(request.form.get("next") or url_for("booking", bid=bid)) if ok else redirect(request.form.get("next") or url_for("booking", bid=bid))

    @app.post("/booking/<int:bid>/cancel")
    @login_required
    def booking_cancel(bid):
        booking_ctx(bid)
        ok, msg = core.cancel(bid, g.user, request.form.get("reason"))
        flash(msg, "ok" if ok else "err")
        return back(bid)

    @app.post("/booking/<int:bid>/checkin")
    @login_required
    def booking_checkin(bid):
        booking_ctx(bid)
        ok, msg = core.check_in(bid, g.user, request.form.get("condition"), request.form.get("notes", "").strip(), request.form.get("ack"))
        flash(msg, "ok" if ok else "err")
        return back(bid)

    @app.post("/booking/<int:bid>/return")
    @login_required
    def booking_return(bid):
        booking_ctx(bid)
        ok, msg = core.return_booking(bid, g.user, request.form.get("condition"), request.form.get("notes", "").strip(), request.form.get("damage"))
        flash(msg, "ok" if ok else "err")
        return back(bid)

    @app.post("/booking/<int:bid>/confirm")
    @login_required
    def booking_confirm(bid):
        booking_ctx(bid)
        ok, msg = core.confirm_return(bid, g.user, request.form.get("note", "").strip())
        flash(msg, "ok" if ok else "err")
        return redirect(request.form.get("next") or url_for("booking", bid=bid))

    @app.post("/booking/<int:bid>/extend")
    @login_required
    def booking_extend(bid):
        b, _ = booking_ctx(bid)
        try:
            new_end = pt(b["end_ts"]) + timedelta(hours=float(request.form.get("hours", 1)))
        except ValueError:
            flash("Invalid extension.", "err")
            return back(bid)
        ok, msg = core.request_extension(bid, g.user, core.ts(new_end), request.form.get("reason", "").strip())
        flash(msg, "ok" if ok else "err")
        return back(bid)

    @app.post("/booking/<int:bid>/extension-decision")
    @login_required
    def booking_ext_decide(bid):
        booking_ctx(bid)
        ok, msg = core.decide_extension(bid, g.user, request.form.get("decision") == "approve", request.form.get("note", "").strip())
        flash(msg, "ok" if ok else "err")
        return redirect(request.form.get("next") or url_for("booking", bid=bid))

    @app.get("/booking/<int:bid>/record.json")
    @login_required
    def booking_record(bid):
        b, res = booking_ctx(bid)
        u = q1("SELECT name,department FROM users WHERE id=?", (b["user_id"],))
        data = dict(booking=dict(b), resource=dict(id=res["id"], name=res["name"], location=res["location"]), user=dict(u),
                    history=[dict(r) for r in q("SELECT ts,actor_name,action,detail,hash FROM audit_log WHERE booking_id=? ORDER BY id", (bid,))],
                    incidents=[dict(r) for r in q("SELECT * FROM incidents WHERE booking_id=?", (bid,))])
        data["booking"].pop("checks_json", None)
        resp = jsonify(data)
        resp.headers["Content-Disposition"] = f"attachment; filename=booking-{bid}-record.json"
        return resp

    # ------------------------------------------------------------ incidents
    @app.post("/booking/<int:bid>/incident")
    @login_required
    def incident_new(bid):
        b, res = booking_ctx(bid)
        desc = request.form.get("description", "").strip()
        kind = request.form.get("kind", "other")
        if kind not in ("damage", "delay", "conflict", "safety", "misuse", "other") or len(desc) < 10:
            flash("Choose a type and describe what happened (at least a sentence).", "err")
            return back(bid)
        sev = request.form.get("severity", "low")
        iid = core.create_incident(res["id"], bid, g.user["id"], b["user_id"] if g.user["id"] != b["user_id"] else b["user_id"], kind, sev if sev in ("low", "medium", "high") else "low", desc)
        flash("Incident recorded. The people involved have been notified.", "ok")
        return redirect(url_for("incident", iid=iid))

    @app.route("/incidents")
    @login_required
    def incidents():
        if g.user["role"] == "admin":
            rows = q("SELECT i.*, r.name rname FROM incidents i JOIN resources r ON r.id=i.resource_id ORDER BY i.id DESC")
        else:
            ids = my_resource_ids()
            m = ",".join("?" * len(ids)) or "NULL"
            rows = q(f"SELECT i.*, r.name rname FROM incidents i JOIN resources r ON r.id=i.resource_id WHERE i.resource_id IN ({m}) OR i.reporter_id=? OR i.subject_user_id=? ORDER BY i.id DESC",
                     (*ids, g.user["id"], g.user["id"]))
        return render_template("incidents.html", rows=rows)

    @app.route("/incident/<int:iid>")
    @login_required
    def incident(iid):
        inc = q1("SELECT * FROM incidents WHERE id=?", (iid,))
        if not inc:
            abort(404)
        if not core.can_view_incident(g.user, inc):
            abort(403)
        res = q1("SELECT * FROM resources WHERE id=?", (inc["resource_id"],))
        comments = q("SELECT c.*,u.name uname,u.role urole FROM incident_comments c JOIN users u ON u.id=c.user_id WHERE incident_id=? ORDER BY c.id", (iid,))
        subject = q1("SELECT id,name,penalty_points FROM users WHERE id=?", (inc["subject_user_id"],)) if inc["subject_user_id"] else None
        reporter = q1("SELECT name FROM users WHERE id=?", (inc["reporter_id"],)) if inc["reporter_id"] else None
        resolver = q1("SELECT name FROM users WHERE id=?", (inc["resolved_by"],)) if inc["resolved_by"] else None
        return render_template("incident.html", inc=inc, r=res, comments=comments, subject=subject, reporter=reporter, resolver=resolver,
                               manager=can_manage(g.user, res["id"]), is_subject=g.user["id"] == inc["subject_user_id"])

    @app.post("/incident/<int:iid>/comment")
    @login_required
    def incident_comment(iid):
        inc = q1("SELECT * FROM incidents WHERE id=?", (iid,))
        if not inc or not core.can_view_incident(g.user, inc):
            abort(403)
        body = request.form.get("body", "").strip()
        if len(body) < 2:
            flash("Write a comment first.", "err")
            return redirect(url_for("incident", iid=iid))
        ex("INSERT INTO incident_comments(incident_id,user_id,body,created_at) VALUES(?,?,?,?)", (iid, g.user["id"], body, ts(now())))
        if request.form.get("dispute") and g.user["id"] == inc["subject_user_id"] and inc["status"] in ("open", "resolved"):
            ex("UPDATE incidents SET status='disputed' WHERE id=?", (iid,))
            log("incident_disputed", g.user["id"], inc["booking_id"], inc["resource_id"], f"#{iid}: {body}")
            core.notify_managers(inc["resource_id"], f"Incident #{iid} was disputed by the user and needs another look.", f"/incident/{iid}")
            flash("Dispute recorded. A manager or administrator will review it.", "ok")
        else:
            log("incident_comment", g.user["id"], inc["booking_id"], inc["resource_id"], f"#{iid}: {body}")
            for uid in ({inc["reporter_id"], inc["subject_user_id"]} | core.manager_ids(inc["resource_id"])) - {None, g.user["id"]}:
                notify(uid, f"New comment on incident #{iid}.", f"/incident/{iid}")
            flash("Comment added.", "ok")
        return redirect(url_for("incident", iid=iid))

    @app.post("/incident/<int:iid>/resolve")
    @login_required
    def incident_resolve(iid):
        if request.form.get("reopen"):
            inc = q1("SELECT * FROM incidents WHERE id=?", (iid,))
            if inc and can_manage(g.user, inc["resource_id"]) and inc["status"] != "open":
                if inc["status"] == "resolved" and inc["penalty_points"] and inc["subject_user_id"]:
                    ex("UPDATE users SET penalty_points=MAX(0,penalty_points-?) WHERE id=?", (inc["penalty_points"], inc["subject_user_id"]))
                ex("UPDATE incidents SET status='open',penalty_points=0 WHERE id=?", (iid,))
                log("incident_comment", g.user["id"], inc["booking_id"], inc["resource_id"], f"#{iid} reopened for review; earlier penalty reversed")
                flash("Reopened.", "ok")
            return redirect(url_for("incident", iid=iid))
        try:
            pts = int(request.form.get("points") or 0)
        except ValueError:
            pts = 0
        ok, msg = core.resolve_incident(iid, g.user, request.form.get("outcome", ""), pts, dismiss=bool(request.form.get("dismiss")))
        flash(msg, "ok" if ok else "err")
        return redirect(url_for("incident", iid=iid))

    # ------------------------------------------------------------ notifications
    @app.route("/notifications", methods=["GET", "POST"])
    @login_required
    def notifications():
        if request.method == "POST":
            ex("UPDATE notifications SET is_read=1 WHERE user_id=?", (g.user["id"],))
            return redirect(url_for("notifications"))
        rows = q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 80", (g.user["id"],))
        return render_template("notifications.html", rows=rows)

    # ------------------------------------------------------------ admin
    @app.route("/admin")
    @roles("admin")
    def admin():
        users = q("SELECT * FROM users ORDER BY verified, id")
        return render_template("admin.html", users=users, ok_chain=core.verify_chain())

    @app.post("/admin/verify/<int:uid>")
    @roles("admin")
    def admin_verify(uid):
        u = q1("SELECT * FROM users WHERE id=?", (uid,))
        if not u:
            abort(404)
        act = request.form.get("act")
        if act == "verify":
            ex("UPDATE users SET verified=1,verified_by=? WHERE id=?", (g.user["id"], uid))
            log("user_verified", g.user["id"], None, None, f"{u['name']} ({u['role']}) verified against college records")
            notify(uid, "Your identity was verified. You can now book" + (" and list resources." if u["role"] == "owner" else "."), "/dashboard")
        elif act == "reset_penalty":
            ex("UPDATE users SET penalty_points=0 WHERE id=?", (uid,))
            log("penalty_reset", g.user["id"], None, None, f"{u['name']}: {u['penalty_points']} points cleared after review")
            notify(uid, "Your penalty points were reset by an administrator.", "/dashboard")
        elif act == "role" and request.form.get("role") in ("user", "owner", "manager"):
            ex("UPDATE users SET role=? WHERE id=?", (request.form["role"], uid))
        flash("Updated.", "ok")
        return redirect(url_for("admin"))

    @app.post("/admin/clock")
    def admin_clock():
        if not g.user or not app.config["DEMO_MODE"]:
            abort(403)
        off = int(core.get_setting("clock_offset_min", "0") or 0)
        act = request.form.get("act")
        target = request.form.get("target")
        if act == "reset":
            off = 0
        elif act == "to_booking" and target:
            b = core.get_booking(int(target))
            if b and (g.user["id"] == b["user_id"] or can_manage(g.user, b["resource_id"])):
                delta = (pt(b["start_ts"]) - timedelta(minutes=5)) - now()
                off += int(delta.total_seconds() // 60)
        elif act == "to_end" and target:
            b = core.get_booking(int(target))
            if b and (g.user["id"] == b["user_id"] or can_manage(g.user, b["resource_id"])):
                delta = (pt(b["end_ts"]) + timedelta(minutes=int(request.form.get("extra") or 0))) - now()
                off += int(delta.total_seconds() // 60)
        else:
            off += int(request.form.get("minutes") or 0)
        core.set_setting("clock_offset_min", off)
        log("clock_changed", g.user["id"], None, None, f"Demo clock offset now {off} min; app time {ts(now())}")
        flash(f"Demo clock set. App time is now {ts(now()).replace('T', ' ')}.", "ok")
        return redirect(request.form.get("next") or url_for("dashboard"))

    @app.post("/admin/reseed")
    @roles("admin")
    def admin_reseed():
        if not app.config["DEMO_MODE"]:
            abort(403)
        import seed
        d = g.pop("db", None)
        if d:
            d.close()
        seed.reset(app)
        session.clear()
        flash("Demo data reset to a fresh state. Please sign in again.", "ok")
        return redirect(url_for("login"))

    @app.route("/audit")
    @login_required
    def audit():
        if g.user["role"] == "admin":
            rows = q("SELECT a.*, r.name rname FROM audit_log a LEFT JOIN resources r ON r.id=a.resource_id ORDER BY a.id DESC LIMIT 300")
            scope = "All institutional activity"
        elif g.user["role"] in ("owner", "manager"):
            ids = my_resource_ids()
            m = ",".join("?" * len(ids)) or "NULL"
            rows = q(f"SELECT a.*, r.name rname FROM audit_log a LEFT JOIN resources r ON r.id=a.resource_id WHERE a.resource_id IN ({m}) ORDER BY a.id DESC LIMIT 300", ids)
            scope = "Activity on resources you manage"
        else:
            rows = q("""SELECT a.*, r.name rname FROM audit_log a LEFT JOIN resources r ON r.id=a.resource_id
                        WHERE a.actor_id=? OR a.booking_id IN (SELECT id FROM bookings WHERE user_id=?) ORDER BY a.id DESC LIMIT 300""", (g.user["id"], g.user["id"]))
            scope = "Your own activity and your bookings"
        return render_template("audit.html", rows=rows, scope=scope, chain=core.verify_chain() if g.user["role"] == "admin" else None)

    @app.route("/about")
    def about():
        return render_template("about.html")

    @app.errorhandler(403)
    def e403(e):
        return render_template("error.html", code=403, msg="You do not have permission to view this. Access is limited to the people involved and the resource's managers."), 403

    @app.errorhandler(404)
    def e404(e):
        return render_template("error.html", code=404, msg="That page or record does not exist."), 404

    @app.errorhandler(400)
    def e400(e):
        return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request.")), 400

    @app.errorhandler(413)
    def e413(e):
        return render_template("error.html", code=413, msg="That file is larger than 2 MB."), 413

    return app


app = create_app() if os.environ.get("SHARELAB_NO_AUTOCREATE") != "1" else None

if __name__ == "__main__":
    (app or create_app()).run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
