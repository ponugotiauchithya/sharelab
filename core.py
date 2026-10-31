"""ShareLab core: database, business rules, fairness engine, audit trail.

Everything that decides *who may use what, when* lives here so it can be tested
without the web layer. Routes in app.py are thin wrappers around these functions.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app, g

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # Windows without tzdata: India has no DST, so a fixed offset is exact
    IST = timezone(timedelta(hours=5, minutes=30))
FMT = "%Y-%m-%dT%H:%M"

CONDITIONS = ["excellent", "good", "fair", "damaged"]
COND_RANK = {c: i for i, c in enumerate(CONDITIONS)}
PURPOSE_TYPES = {
    "coursework": ("Coursework / assignment", 15),
    "research": ("Research / thesis", 12),
    "project": ("Student or startup project", 8),
    "personal": ("Personal / hobby", 3),
}
OCCUPYING = ("approved", "checked_in")           # statuses that hold a time slot
OPEN_STATUSES = ("pending", "approved", "checked_in")
LATE_GRACE_MIN = 15
NO_SHOW_AFTER_MIN = 30
CHECKIN_EARLY_MIN = 15
LATE_CANCEL_MIN = 120
PENALTY_REVIEW_AT = 3      # >= this many points: auto-approval is switched off for the user
PENALTY_BLOCK_AT = 6       # >= this many points: new requests are blocked until an admin reviews
WAITLIST_LIMIT = 3

KNOWN_QUALIFICATIONS = [
    "Lab Safety Induction",
    "3D Printer Certification",
    "Laser Cutter Certification",
    "CNC Machine Operator",
    "Electronics Bench Training",
    "Camera & Studio Training",
    "HPC Cluster Access Agreement",
    "Faculty Authorisation",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('user','owner','manager','admin')),
  department TEXT, college_id TEXT, verified INTEGER NOT NULL DEFAULT 0, verified_by INTEGER,
  penalty_points INTEGER NOT NULL DEFAULT 0, suspended INTEGER NOT NULL DEFAULT 0, created_at TEXT);
CREATE TABLE IF NOT EXISTS credentials(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, ref TEXT, file_path TEXT,
  status TEXT NOT NULL DEFAULT 'pending', reviewer_id INTEGER, review_note TEXT, expires_on TEXT,
  created_at TEXT, reviewed_at TEXT);
CREATE TABLE IF NOT EXISTS resources(
  id INTEGER PRIMARY KEY, owner_id INTEGER NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
  location TEXT NOT NULL, description TEXT, rules TEXT, tags TEXT DEFAULT '',
  approval_mode TEXT NOT NULL DEFAULT 'manual', supervision INTEGER NOT NULL DEFAULT 0,
  max_hours REAL NOT NULL DEFAULT 4, weekly_cap_hours REAL NOT NULL DEFAULT 8,
  max_open INTEGER NOT NULL DEFAULT 2, advance_days INTEGER NOT NULL DEFAULT 14,
  active INTEGER NOT NULL DEFAULT 1, emoji TEXT DEFAULT '🔧', created_at TEXT);
CREATE TABLE IF NOT EXISTS resource_requirements(
  id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL, qualification TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resource_windows(
  id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL, weekday INTEGER NOT NULL,
  start_hour INTEGER NOT NULL, end_hour INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS resource_managers(
  resource_id INTEGER NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(resource_id, user_id));
CREATE TABLE IF NOT EXISTS bookings(
  id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  start_ts TEXT NOT NULL, end_ts TEXT NOT NULL, orig_end_ts TEXT, purpose TEXT, purpose_type TEXT,
  status TEXT NOT NULL, decision_by INTEGER, decision_reason TEXT, decided_at TEXT, auto_decided INTEGER DEFAULT 0,
  checks_json TEXT, created_at TEXT,
  checkin_at TEXT, checkin_condition TEXT, checkin_notes TEXT,
  return_at TEXT, return_condition TEXT, return_notes TEXT, late_minutes INTEGER DEFAULT 0,
  confirmed_by INTEGER, confirmed_at TEXT, confirm_note TEXT,
  ext_end_ts TEXT, ext_reason TEXT, ext_status TEXT, ext_note TEXT,
  overdue_notified INTEGER DEFAULT 0, reminded INTEGER DEFAULT 0, cancelled_by INTEGER);
CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL, booking_id INTEGER, reporter_id INTEGER,
  subject_user_id INTEGER, kind TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'low',
  description TEXT, source TEXT NOT NULL DEFAULT 'user', status TEXT NOT NULL DEFAULT 'open',
  resolution TEXT, penalty_points INTEGER DEFAULT 0, resolved_by INTEGER, created_at TEXT, resolved_at TEXT);
CREATE TABLE IF NOT EXISTS incident_comments(
  id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL, user_id INTEGER NOT NULL, body TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, actor_id INTEGER, actor_name TEXT, action TEXT NOT NULL,
  booking_id INTEGER, resource_id INTEGER, detail TEXT, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, ts TEXT, message TEXT, link TEXT, is_read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_book_res ON bookings(resource_id, start_ts);
CREATE INDEX IF NOT EXISTS ix_book_user ON bookings(user_id);
"""

ACTION_LABELS = {
    "resource_listed": "Resource listed",
    "resource_updated": "Resource updated",
    "request_submitted": "Booking requested",
    "auto_approved": "Auto-approved by owner's rule",
    "approved": "Approved",
    "rejected": "Rejected",
    "waitlisted": "Placed on waiting list",
    "waitlisted_by_conflict": "Moved to waiting list (slot allocated elsewhere)",
    "promoted": "Promoted from waiting list",
    "cancelled": "Cancelled",
    "checked_in": "Checked in",
    "returned": "Returned",
    "return_confirmed": "Return confirmed by staff",
    "extension_requested": "Extension requested",
    "extension_approved": "Extension approved",
    "extension_denied": "Extension denied",
    "no_show": "Marked no-show",
    "overdue": "Flagged overdue",
    "reminder": "Reminder sent",
    "incident_reported": "Incident reported",
    "incident_resolved": "Incident resolved",
    "incident_comment": "Incident comment",
    "incident_disputed": "Incident disputed",
    "credential_submitted": "Credential submitted",
    "credential_reviewed": "Credential reviewed",
    "user_verified": "Identity verified",
    "manager_added": "Manager added",
    "manager_removed": "Manager removed",
    "penalty_reset": "Penalty points reset",
    "clock_changed": "Demo clock changed",
    "user_registered": "Account registered",
}


# ---------------------------------------------------------------- db helpers
def db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DB_PATH"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def q(sql, a=()):
    return db().execute(sql, a).fetchall()


def q1(sql, a=()):
    return db().execute(sql, a).fetchone()


def ex(sql, a=()):
    return db().execute(sql, a).lastrowid


# ---------------------------------------------------------------- time
def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def now():
    off = int(get_setting("clock_offset_min", "0") or 0)
    return datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0) + timedelta(minutes=off)


def ts(d):
    return d.strftime(FMT)


def pt(s):
    return datetime.strptime(s, FMT)


def floor30(d):
    return d.replace(minute=0 if d.minute < 30 else 30)


def hours_between(a, b):
    return (pt(b) - pt(a)).total_seconds() / 3600 if isinstance(a, str) else (b - a).total_seconds() / 3600


# ---------------------------------------------------------------- audit trail (hash-chained)
def _hash(prev, t, actor, action, bid, rid, detail):
    return hashlib.sha256(f"{prev}|{t}|{actor}|{action}|{bid}|{rid}|{detail}".encode()).hexdigest()


def log(action, actor_id=None, booking_id=None, resource_id=None, detail="", at=None):
    t = at or ts(now())
    actor = q1("SELECT name FROM users WHERE id=?", (actor_id,)) if actor_id else None
    actor_name = actor["name"] if actor else "System"
    last = q1("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1")
    prev = last["hash"] if last else "GENESIS"
    h = _hash(prev, t, actor_name, action, booking_id, resource_id, detail)
    ex("INSERT INTO audit_log(ts,actor_id,actor_name,action,booking_id,resource_id,detail,prev_hash,hash) VALUES(?,?,?,?,?,?,?,?,?)",
       (t, actor_id, actor_name, action, booking_id, resource_id, detail, prev, h))


def verify_chain():
    prev = "GENESIS"
    for r in q("SELECT * FROM audit_log ORDER BY id"):
        if r["prev_hash"] != prev or r["hash"] != _hash(prev, r["ts"], r["actor_name"], r["action"], r["booking_id"], r["resource_id"], r["detail"]):
            return False, r["id"]
        prev = r["hash"]
    return True, None


def notify(user_id, message, link=None):
    if user_id:
        ex("INSERT INTO notifications(user_id,ts,message,link) VALUES(?,?,?,?)", (user_id, ts(now()), message, link))


def manager_ids(resource_id):
    res = q1("SELECT owner_id FROM resources WHERE id=?", (resource_id,))
    ids = {res["owner_id"]} if res else set()
    ids |= {r["user_id"] for r in q("SELECT user_id FROM resource_managers WHERE resource_id=?", (resource_id,))}
    return ids


def notify_managers(resource_id, message, link=None, exclude=None):
    for uid in manager_ids(resource_id):
        if uid != exclude:
            notify(uid, message, link)


def can_manage(user, resource_id):
    if not user:
        return False
    return user["role"] == "admin" or user["id"] in manager_ids(resource_id)


# ---------------------------------------------------------------- eligibility
def credential_state(user_id, qual, at):
    """Return (ok, text) for one required qualification."""
    rows = q("SELECT * FROM credentials WHERE user_id=? AND name=? ORDER BY id DESC", (user_id, qual))
    if not rows:
        return False, "Not submitted"
    for r in rows:
        if r["status"] == "approved":
            if r["expires_on"] and r["expires_on"] < ts(at)[:10]:
                continue
            return True, "Approved" + (f" (valid to {r['expires_on']})" if r["expires_on"] else "")
    r = rows[0]
    if r["status"] == "approved":
        return False, "Expired"
    if r["status"] == "pending":
        return False, "Submitted - awaiting staff review"
    return False, "Rejected - " + (r["review_note"] or "resubmit with correct details")


def eligibility(user, res, at):
    checks = []
    checks.append(dict(label="Identity verified by institution", ok=bool(user["verified"]),
                       detail="Verified" if user["verified"] else "Your college ID is not verified yet - an administrator must verify it first.", kind="eligibility"))
    standing = not user["suspended"] and user["penalty_points"] < PENALTY_BLOCK_AT
    checks.append(dict(label="Account in good standing", ok=standing,
                       detail="No restrictions" if standing else "Account restricted after incident penalties - contact the lab manager or admin.", kind="eligibility"))
    for r in q("SELECT qualification FROM resource_requirements WHERE resource_id=?", (res["id"],)):
        ok, text = credential_state(user["id"], r["qualification"], at)
        checks.append(dict(label=f"Required: {r['qualification']}", ok=ok, detail=text, kind="eligibility", qual=r["qualification"]))
    return checks


# ---------------------------------------------------------------- fairness
def recent_hours(user_id, resource_id, days=30):
    since = ts(now() - timedelta(days=days))
    rows = q("SELECT start_ts,end_ts FROM bookings WHERE user_id=? AND resource_id=? AND status IN ('completed','returned','checked_in') AND start_ts>=?",
             (user_id, resource_id, since))
    return sum(hours_between(r["start_ts"], r["end_ts"]) for r in rows)


def score_booking(b):
    """Explainable priority score. Higher = served first. Humans may override, but must justify."""
    parts = [("Base score", 50)]
    label, bonus = PURPOSE_TYPES.get(b["purpose_type"], ("Other", 0))
    parts.append((f"Purpose: {label}", bonus))
    rh = recent_hours(b["user_id"], b["resource_id"])
    pen = -min(30, round(rh * 2))
    parts.append((f"Recent use of this resource: {rh:g} h in last 30 days (-2 per hour, max -30)", pen))
    done = q1("SELECT COUNT(*) c FROM bookings WHERE user_id=? AND resource_id=? AND status='completed'", (b["user_id"], b["resource_id"]))["c"]
    parts.append(("First-time user of this resource" if done == 0 else "Has used this resource before", 8 if done == 0 else 0))
    u = q1("SELECT penalty_points FROM users WHERE id=?", (b["user_id"],))
    pp = -min(30, 10 * (u["penalty_points"] if u else 0))
    parts.append((f"Incident penalty points: {u['penalty_points'] if u else 0} (-10 each, max -30)", pp))
    waited = max(0, (now() - pt(b["created_at"])).total_seconds() / 3600)
    wb = min(10, int(waited))
    parts.append((f"Waiting time: {int(waited)} h (+1 per hour, max +10)", wb))
    return sum(p[1] for p in parts), parts


def week_bounds(d):
    start = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0)
    return ts(start), ts(start + timedelta(days=7))


def week_hours(user_id, resource_id, d, exclude_id=None):
    a, b = week_bounds(d)
    rows = q("SELECT id,start_ts,end_ts FROM bookings WHERE user_id=? AND resource_id=? AND start_ts>=? AND start_ts<? AND status IN ('pending','approved','checked_in','returned','completed')",
             (user_id, resource_id, a, b))
    return sum(hours_between(r["start_ts"], r["end_ts"]) for r in rows if r["id"] != exclude_id)


def open_count(user_id, resource_id, exclude_id=None):
    n = ts(now())
    rows = q("SELECT id FROM bookings WHERE user_id=? AND resource_id=? AND status IN ('pending','approved','checked_in') AND end_ts>?", (user_id, resource_id, n))
    return len([r for r in rows if r["id"] != exclude_id])


# ---------------------------------------------------------------- slot rules
def in_window(resource_id, start, end):
    if start.date() != end.date():
        return False
    sm, em = start.hour * 60 + start.minute, end.hour * 60 + end.minute
    for w in q("SELECT * FROM resource_windows WHERE resource_id=? AND weekday=?", (resource_id, start.weekday())):
        if w["start_hour"] * 60 <= sm and em <= w["end_hour"] * 60:
            return True
    return False


def overlapping(resource_id, start_s, end_s, statuses, exclude_id=None):
    marks = ",".join("?" * len(statuses))
    rows = q(f"SELECT * FROM bookings WHERE resource_id=? AND status IN ({marks}) AND start_ts<? AND end_ts>?",
             (resource_id, *statuses, end_s, start_s))
    return [r for r in rows if r["id"] != exclude_id]


def evaluate(user, res, start, end, exclude_id=None):
    """Dry-run every rule. Returns dict with checks, outcome and (if waitlisted) the blocking bookings."""
    checks = eligibility(user, res, start)
    n = now()
    dur = (end - start).total_seconds() / 3600
    s_s, e_s = ts(start), ts(end)

    def add(label, ok, detail):
        checks.append(dict(label=label, ok=ok, detail=detail, kind="slot"))

    add("Valid time range", dur > 0, f"{dur:g} h requested" if dur > 0 else "End must be after start")
    add("Within opening hours", dur > 0 and in_window(res["id"], start, end), "Inside a published availability window" if dur > 0 and in_window(res["id"], start, end) else "Outside the resource's availability windows (see weekly calendar)")
    add("Starts now or later", start >= floor30(n), "OK" if start >= floor30(n) else "Start time is in the past")
    add("Within booking horizon", start <= n + timedelta(days=res["advance_days"]), f"Bookable up to {res['advance_days']} days ahead")
    add("Within max session length", dur <= res["max_hours"], f"Max {res['max_hours']:g} h per session")
    mine = [r for r in overlapping_user(user["id"], s_s, e_s, exclude_id)]
    add("No clash with your other bookings", not mine, "No clash" if not mine else f"Overlaps your booking #{mine[0]['id']}")
    wh = week_hours(user["id"], res["id"], start, exclude_id)
    add("Fair-use weekly cap", wh + dur <= res["weekly_cap_hours"], f"{wh:g} h already booked this week + {dur:g} h = {wh + dur:g} h (cap {res['weekly_cap_hours']:g} h per person per week)")
    oc = open_count(user["id"], res["id"], exclude_id)
    add("Fair-use open-request limit", oc < res["max_open"], f"{oc} open booking(s); limit {res['max_open']} at a time")

    hard_fail = [c for c in checks if not c["ok"]]
    holders = overlapping(res["id"], s_s, e_s, OCCUPYING, exclude_id)
    outcome = "blocked"
    if not hard_fail:
        if holders:
            wl = q1("SELECT COUNT(*) c FROM bookings WHERE user_id=? AND resource_id=? AND status='waitlisted'", (user["id"], res["id"]))["c"]
            if wl >= WAITLIST_LIMIT:
                checks.append(dict(label="Waiting-list limit", ok=False, detail=f"You already hold {wl} waiting-list places on this resource", kind="slot"))
                hard_fail = [c for c in checks if not c["ok"]]
            else:
                outcome = "waitlisted"
        elif res["approval_mode"] == "auto" and user["penalty_points"] < PENALTY_REVIEW_AT and not res["supervision"]:
            outcome = "approved"
        else:
            outcome = "pending"
    return dict(checks=checks, outcome=outcome, holders=holders, failed=hard_fail)


def overlapping_user(user_id, s_s, e_s, exclude_id=None):
    rows = q("SELECT * FROM bookings WHERE user_id=? AND status IN ('pending','approved','checked_in') AND start_ts<? AND end_ts>?", (user_id, e_s, s_s))
    return [r for r in rows if r["id"] != exclude_id]


OUTCOME_TEXT = {
    "approved": "This resource uses owner-defined auto-approval. All checks pass, so you would be approved instantly (and the decision is logged).",
    "pending": "All checks pass. Your request would go to the resource manager for human review.",
    "waitlisted": "All checks pass, but the slot is already held. You would join the waiting list and be reconsidered automatically if it frees up.",
    "blocked": "The request cannot be submitted yet - fix the items marked below.",
}


# ---------------------------------------------------------------- booking lifecycle
def create_booking(user, res, start, end, purpose, ptype):
    ev = evaluate(user, res, start, end)
    if ev["outcome"] == "blocked":
        return None, ev
    bid = ex("""INSERT INTO bookings(resource_id,user_id,start_ts,end_ts,purpose,purpose_type,status,checks_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
             (res["id"], user["id"], ts(start), ts(end), purpose, ptype, "pending", json.dumps(ev["checks"]), ts(now())))
    log("request_submitted", user["id"], bid, res["id"], f"{ts(start)} to {ts(end)}; purpose type: {ptype}")
    link = f"/booking/{bid}"
    if ev["outcome"] == "approved":
        reason = ("Auto-approved: eligibility, opening hours, session length and fair-use limits all passed, and the owner enabled "
                  "auto-approval for this resource. Owners/managers can still cancel it.")
        ex("UPDATE bookings SET status='approved',decision_reason=?,decided_at=?,auto_decided=1 WHERE id=?", (reason, ts(now()), bid))
        log("auto_approved", None, bid, res["id"], reason)
        notify(user["id"], f"Booking #{bid} for {res['name']} was approved automatically.", link)
        notify_managers(res["id"], f"{user['name']} auto-booked {res['name']} ({ts(start)}).", link)
    elif ev["outcome"] == "waitlisted":
        h = ev["holders"][0]
        reason = (f"The slot is already held by approved booking #{h['id']}. You are on the waiting list; if it is cancelled or missed, "
                  "waiting-list requests are ranked by the published priority rules and the top one is sent for review.")
        ex("UPDATE bookings SET status='waitlisted',decision_reason=?,decided_at=? WHERE id=?", (reason, ts(now()), bid))
        log("waitlisted", None, bid, res["id"], reason)
        notify(user["id"], f"Booking #{bid} is on the waiting list for {res['name']}.", link)
    else:
        notify_managers(res["id"], f"New request from {user['name']} for {res['name']} ({ts(start)}) needs review.", "/manage")
        notify(user["id"], f"Request #{bid} submitted. A manager will review it.", link)
    return bid, ev


def get_booking(bid):
    return q1("SELECT * FROM bookings WHERE id=?", (bid,))


def competitors(b):
    return overlapping(b["resource_id"], b["start_ts"], b["end_ts"], ("pending", "waitlisted"), b["id"])


def decide(bid, actor, action, reason):
    """Human decision on a pending/waitlisted request. Returns (ok, message)."""
    b = get_booking(bid)
    res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
    reason = (reason or "").strip()
    if b["status"] not in ("pending", "waitlisted"):
        return False, "This request is no longer awaiting a decision."
    link = f"/booking/{bid}"
    n = ts(now())
    if action == "approve":
        if b["end_ts"] <= n:
            return False, "This slot is already in the past."
        if overlapping(b["resource_id"], b["start_ts"], b["end_ts"], OCCUPYING, bid):
            return False, "Another approved booking already holds this slot. Cancel it first, or waitlist this request."
        my_score = score_booking(b)[0]
        higher = [c for c in competitors(b) if score_booking(c)[0] > my_score]
        if higher and len(reason) < 8:
            return False, (f"{len(higher)} competing request(s) have a higher fair-use priority score. "
                           "Approving this one first is allowed, but you must write a justification (it is shown to everyone affected and logged).")
        text = reason or f"Approved by {actor['name']}. Eligibility and fair-use checks passed; no higher-priority competing request."
        ex("UPDATE bookings SET status='approved',decision_by=?,decision_reason=?,decided_at=?,auto_decided=0 WHERE id=?", (actor["id"], text, n, bid))
        log("approved", actor["id"], bid, b["resource_id"], f"{text} [priority score {my_score}]")
        notify(b["user_id"], f"Booking #{bid} for {res['name']} was approved.", link)
        for c in competitors(b):
            msg = (f"Another request (#{bid}) was approved for this slot" + (f": {reason}" if reason else "") +
                   ". You stay on the waiting list and will be reconsidered if it is cancelled.")
            ex("UPDATE bookings SET status='waitlisted',decision_reason=?,decided_at=?,decision_by=NULL WHERE id=?", (msg, n, c["id"]))
            log("waitlisted_by_conflict", actor["id"], c["id"], c["resource_id"], msg)
            notify(c["user_id"], f"Your request #{c['id']} moved to the waiting list - slot allocated to another request.", f"/booking/{c['id']}")
        return True, "Approved. The requester has been notified."
    if action == "reject":
        if len(reason) < 5:
            return False, "A reason is required when rejecting, so the requester understands the decision."
        ex("UPDATE bookings SET status='rejected',decision_by=?,decision_reason=?,decided_at=? WHERE id=?", (actor["id"], reason, n, bid))
        log("rejected", actor["id"], bid, b["resource_id"], reason)
        notify(b["user_id"], f"Booking #{bid} for {res['name']} was rejected: {reason}", link)
        return True, "Rejected. The reason has been sent to the requester."
    if action == "waitlist":
        if len(reason) < 5:
            return False, "Please explain why this request is waitlisted."
        ex("UPDATE bookings SET status='waitlisted',decision_by=?,decision_reason=?,decided_at=? WHERE id=?", (actor["id"], reason, n, bid))
        log("waitlisted", actor["id"], bid, b["resource_id"], reason)
        notify(b["user_id"], f"Booking #{bid} was placed on the waiting list: {reason}", link)
        return True, "Placed on the waiting list."
    return False, "Unknown action."


def promote_waitlist(resource_id, actor_id=None):
    """When a slot frees up, the highest-scoring waitlisted request that fits is sent for human review (or auto-approved)."""
    res = q1("SELECT * FROM resources WHERE id=?", (resource_id,))
    n = ts(floor30(now()))
    cands = [b for b in q("SELECT * FROM bookings WHERE resource_id=? AND status='waitlisted' AND start_ts>=?", (resource_id, n))]
    cands.sort(key=lambda b: (-score_booking(b)[0], b["created_at"], b["id"]))
    taken = []
    for b in cands:
        if overlapping(resource_id, b["start_ts"], b["end_ts"], OCCUPYING + ("pending",), b["id"]):
            continue
        if any(b["start_ts"] < t["end_ts"] and b["end_ts"] > t["start_ts"] for t in taken):
            continue
        user = q1("SELECT * FROM users WHERE id=?", (b["user_id"],))
        if user["penalty_points"] >= PENALTY_BLOCK_AT:
            continue
        sc, parts = score_booking(b)
        why = f"Slot reopened. Highest-priority waiting request (score {sc}): " + "; ".join(f"{l} {v:+d}" for l, v in parts)
        if res["approval_mode"] == "auto" and user["penalty_points"] < PENALTY_REVIEW_AT and not res["supervision"]:
            ex("UPDATE bookings SET status='approved',decision_reason=?,decided_at=?,auto_decided=1 WHERE id=?", (why, ts(now()), b["id"]))
            log("promoted", actor_id, b["id"], resource_id, "Auto-approved after promotion. " + why)
            notify(b["user_id"], f"Good news - the slot for {res['name']} opened up and booking #{b['id']} is now approved.", f"/booking/{b['id']}")
        else:
            ex("UPDATE bookings SET status='pending',decision_reason=?,decided_at=? WHERE id=?", (why, ts(now()), b["id"]))
            log("promoted", actor_id, b["id"], resource_id, why)
            notify(b["user_id"], f"The slot for {res['name']} opened up. Booking #{b['id']} moved from the waiting list to manager review.", f"/booking/{b['id']}")
            notify_managers(resource_id, f"Waitlisted request #{b['id']} promoted to review (slot reopened).", "/manage")
        taken.append(b)


def cancel(bid, actor, reason):
    b = get_booking(bid)
    res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
    reason = (reason or "").strip()
    if b["status"] not in ("pending", "approved", "waitlisted"):
        return False, "This booking can no longer be cancelled (use return if you already checked in)."
    by_owner = actor["id"] == b["user_id"]
    if not by_owner and not can_manage(actor, b["resource_id"]):
        return False, "You are not allowed to cancel this booking."
    if not by_owner and len(reason) < 5:
        return False, "Staff cancellations need a reason; it is shown to the user."
    was = b["status"]
    ex("UPDATE bookings SET status='cancelled',cancelled_by=?,decision_reason=COALESCE(NULLIF(?,''),decision_reason) WHERE id=?", (actor["id"], reason, bid))
    log("cancelled", actor["id"], bid, b["resource_id"], reason or "No reason given")
    if by_owner:
        notify_managers(b["resource_id"], f"{actor['name']} cancelled booking #{bid} ({res['name']}).", f"/booking/{bid}")
        if was == "approved" and (pt(b["start_ts"]) - now()).total_seconds() / 60 < LATE_CANCEL_MIN:
            create_incident(b["resource_id"], bid, None, b["user_id"], "late_cancellation", "low",
                            f"Approved booking cancelled less than {LATE_CANCEL_MIN // 60} hours before start. Flagged for staff review; no automatic penalty.", "system")
    else:
        notify(b["user_id"], f"Booking #{bid} for {res['name']} was cancelled by staff: {reason}", f"/booking/{bid}")
    if was == "approved":
        promote_waitlist(b["resource_id"], actor["id"])
    return True, "Booking cancelled."


def can_check_in(b):
    n = now()
    return b["status"] == "approved" and pt(b["start_ts"]) - timedelta(minutes=CHECKIN_EARLY_MIN) <= n <= pt(b["end_ts"])


def check_in(bid, actor, condition, notes, ack):
    b = get_booking(bid)
    res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
    if b["user_id"] != actor["id"]:
        return False, "Only the person who booked can check in."
    if b["status"] != "approved":
        return False, "Only approved bookings can be checked in."
    if not can_check_in(b):
        return False, f"Check-in opens {CHECKIN_EARLY_MIN} minutes before the start time ({b['start_ts'].replace('T', ' ')})."
    if condition not in COND_RANK:
        return False, "Please record the condition you found the resource in."
    if not ack:
        return False, "You must acknowledge the usage rules before check-in."
    ex("UPDATE bookings SET status='checked_in',checkin_at=?,checkin_condition=?,checkin_notes=? WHERE id=?", (ts(now()), condition, notes, bid))
    log("checked_in", actor["id"], bid, b["resource_id"], f"Initial condition: {condition}. {notes or ''}".strip())
    notify_managers(b["resource_id"], f"{actor['name']} checked in to {res['name']} (condition: {condition}).", f"/booking/{bid}")
    if COND_RANK[condition] >= 2:
        create_incident(b["resource_id"], bid, actor["id"], None, "damage", "low",
                        f"Pre-existing condition '{condition}' reported at check-in. Recorded so the user is not held responsible. Notes: {notes or 'none'}", "system")
    return True, "Checked in. Your initial condition report is recorded."


def return_booking(bid, actor, condition, notes, damage_flag):
    b = get_booking(bid)
    res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
    if b["user_id"] != actor["id"]:
        return False, "Only the person who checked in can return it."
    if b["status"] != "checked_in":
        return False, "This booking is not checked in."
    if condition not in COND_RANK:
        return False, "Please record the final condition."
    n = now()
    late = max(0, int((n - pt(b["end_ts"])).total_seconds() // 60))
    ex("UPDATE bookings SET status='returned',return_at=?,return_condition=?,return_notes=?,late_minutes=? WHERE id=?",
       (ts(n), condition, notes, late, bid))
    log("returned", actor["id"], bid, b["resource_id"], f"Final condition: {condition}. Late by {late} min. {notes or ''}".strip())
    notify_managers(b["resource_id"], f"{actor['name']} returned {res['name']} (condition: {condition}). Please confirm the return.", "/manage")
    if late > LATE_GRACE_MIN:
        create_incident(b["resource_id"], bid, None, b["user_id"], "delay", "low" if late < 60 else "medium",
                        f"Returned {late} minutes after the booked end time. Flagged for staff review; no automatic penalty.", "system")
    if damage_flag or COND_RANK[condition] > COND_RANK[b["checkin_condition"] or "good"]:
        sev = "high" if COND_RANK[condition] >= 3 else "medium"
        create_incident(b["resource_id"], bid, None, b["user_id"], "damage", sev,
                        f"Condition changed from '{b['checkin_condition']}' at check-in to '{condition}' at return"
                        + (" (user flagged an issue)" if damage_flag else "") + f". User notes: {notes or 'none'}", "system")
    return True, "Returned. Staff will confirm the return and condition."


def confirm_return(bid, actor, note):
    b = get_booking(bid)
    if b["status"] != "returned":
        return False, "Nothing to confirm."
    if not can_manage(actor, b["resource_id"]):
        return False, "Only managers can confirm a return."
    ex("UPDATE bookings SET status='completed',confirmed_by=?,confirmed_at=?,confirm_note=? WHERE id=?", (actor["id"], ts(now()), note, bid))
    log("return_confirmed", actor["id"], bid, b["resource_id"], note or "Return inspected and confirmed")
    notify(b["user_id"], f"Your return for booking #{bid} was confirmed. Thank you!", f"/booking/{bid}")
    return True, "Return confirmed. Transaction complete."


def request_extension(bid, actor, new_end_s, reason):
    b = get_booking(bid)
    res = q1("SELECT * FROM resources WHERE id=?", (b["resource_id"],))
    if b["user_id"] != actor["id"] or b["status"] not in ("approved", "checked_in"):
        return False, "Extensions can only be requested by the booker for an approved or active booking."
    if b["ext_status"] == "requested":
        return False, "An extension request is already waiting for a decision."
    try:
        new_end = pt(new_end_s)
    except ValueError:
        return False, "Invalid time."
    if new_end <= pt(b["end_ts"]):
        return False, "New end time must be after the current end time."
    dur = hours_between(b["start_ts"], ts(new_end))
    if dur > res["max_hours"]:
        return False, f"Total session would be {dur:g} h; the maximum is {res['max_hours']:g} h."
    if not in_window(res["id"], pt(b["start_ts"]), new_end):
        return False, "The extended time falls outside opening hours."
    if week_hours(actor["id"], res["id"], pt(b["start_ts"]), bid) + dur > res["weekly_cap_hours"]:
        return False, "The extension would exceed your weekly fair-use cap for this resource."
    if overlapping(res["id"], b["end_ts"], ts(new_end), OCCUPYING + ("pending",), bid):
        return False, "Another booking or pending request needs the resource at that time."
    ex("UPDATE bookings SET ext_end_ts=?,ext_reason=?,ext_status='requested',ext_note=NULL WHERE id=?", (ts(new_end), reason, bid))
    log("extension_requested", actor["id"], bid, res["id"], f"Until {ts(new_end)}. Reason: {reason}")
    notify_managers(res["id"], f"{actor['name']} requests an extension on booking #{bid} until {ts(new_end)}.", "/manage")
    return True, "Extension requested. A manager will decide."


def decide_extension(bid, actor, approve, note):
    b = get_booking(bid)
    if b["ext_status"] != "requested" or not can_manage(actor, b["resource_id"]):
        return False, "No extension to decide, or you lack permission."
    if approve:
        if overlapping(b["resource_id"], b["end_ts"], b["ext_end_ts"], OCCUPYING + ("pending",), bid):
            return False, "A conflicting booking now exists in that time."
        ex("UPDATE bookings SET orig_end_ts=COALESCE(orig_end_ts,end_ts),end_ts=ext_end_ts,ext_status='approved',ext_note=? WHERE id=?", (note, bid))
        log("extension_approved", actor["id"], bid, b["resource_id"], f"New end {b['ext_end_ts']}. {note or ''}".strip())
        notify(b["user_id"], f"Extension for booking #{bid} approved until {b['ext_end_ts']}.", f"/booking/{bid}")
        return True, "Extension approved."
    if len((note or "").strip()) < 5:
        return False, "Please give a reason for denying the extension."
    ex("UPDATE bookings SET ext_status='denied',ext_note=? WHERE id=?", (note, bid))
    log("extension_denied", actor["id"], bid, b["resource_id"], note)
    notify(b["user_id"], f"Extension for booking #{bid} was denied: {note}", f"/booking/{bid}")
    return True, "Extension denied."


# ---------------------------------------------------------------- incidents
def create_incident(resource_id, booking_id, reporter_id, subject_user_id, kind, severity, description, source="user"):
    if booking_id and not subject_user_id:
        b = get_booking(booking_id)
        subject_user_id = b["user_id"] if b else None
    iid = ex("""INSERT INTO incidents(resource_id,booking_id,reporter_id,subject_user_id,kind,severity,description,source,status,created_at)
                VALUES(?,?,?,?,?,?,?,?, 'open', ?)""",
             (resource_id, booking_id, reporter_id, subject_user_id, kind, severity, description, source, ts(now())))
    log("incident_reported", reporter_id, booking_id, resource_id, f"#{iid} {kind} ({severity}, {source}): {description}")
    res = q1("SELECT name FROM resources WHERE id=?", (resource_id,))
    notify_managers(resource_id, f"Incident #{iid} ({kind}) reported for {res['name']}. Needs review.", f"/incident/{iid}", exclude=reporter_id)
    if subject_user_id and source == "user" and subject_user_id != reporter_id:
        notify(subject_user_id, f"An incident (#{iid}, {kind}) was reported on your booking. You can respond or dispute it.", f"/incident/{iid}")
    return iid


def can_view_incident(user, inc):
    return bool(user) and (user["role"] == "admin" or can_manage(user, inc["resource_id"]) or user["id"] in (inc["reporter_id"], inc["subject_user_id"]))


def resolve_incident(iid, actor, outcome, points, dismiss=False):
    inc = q1("SELECT * FROM incidents WHERE id=?", (iid,))
    if not inc or not can_manage(actor, inc["resource_id"]):
        return False, "You do not have permission to resolve this incident."
    if inc["status"] in ("resolved", "dismissed"):
        return False, "Already closed."
    if len((outcome or "").strip()) < 5:
        return False, "Write the outcome so both parties understand the decision."
    points = 0 if dismiss else max(0, min(5, int(points or 0)))
    status = "dismissed" if dismiss else "resolved"
    ex("UPDATE incidents SET status=?,resolution=?,penalty_points=?,resolved_by=?,resolved_at=? WHERE id=?",
       (status, outcome, points, actor["id"], ts(now()), iid))
    if points and inc["subject_user_id"]:
        ex("UPDATE users SET penalty_points=penalty_points+? WHERE id=?", (points, inc["subject_user_id"]))
    log("incident_resolved", actor["id"], inc["booking_id"], inc["resource_id"], f"#{iid} {status}; penalty points {points}. {outcome}")
    for uid in {inc["subject_user_id"], inc["reporter_id"]} - {None, actor["id"]}:
        notify(uid, f"Incident #{iid} was {status}" + (f" ({points} penalty point(s))" if points else "") + f": {outcome}", f"/incident/{iid}")
    return True, "Incident closed and recorded."


# ---------------------------------------------------------------- background-style sweep
def sweep():
    """Lazy scheduler: reminders, overdue flags, no-shows. Called on requests and from the admin page."""
    n = now()
    n_s = ts(n)
    for b in q("SELECT * FROM bookings WHERE status='approved' AND reminded=0 AND start_ts<=? AND start_ts>?", (ts(n + timedelta(minutes=60)), n_s)):
        res = q1("SELECT name FROM resources WHERE id=?", (b["resource_id"],))
        notify(b["user_id"], f"Reminder: your booking #{b['id']} for {res['name']} starts at {b['start_ts'][11:]}. Check in when you arrive.", f"/booking/{b['id']}")
        ex("UPDATE bookings SET reminded=1 WHERE id=?", (b["id"],))
        log("reminder", None, b["id"], b["resource_id"], "Start-time reminder sent")
    for b in q("SELECT * FROM bookings WHERE status='approved' AND start_ts<=?", (ts(n - timedelta(minutes=NO_SHOW_AFTER_MIN)),)):
        ex("UPDATE bookings SET status='no_show' WHERE id=?", (b["id"],))
        log("no_show", None, b["id"], b["resource_id"], f"No check-in within {NO_SHOW_AFTER_MIN} minutes of start; slot released")
        create_incident(b["resource_id"], b["id"], None, b["user_id"], "no_show", "low",
                        "Approved booking was not checked in. Slot released to the waiting list. Flagged for staff review; no automatic penalty.", "system")
        notify(b["user_id"], f"Booking #{b['id']} was marked as a no-show and the slot was released.", f"/booking/{b['id']}")
        promote_waitlist(b["resource_id"])
    for b in q("SELECT * FROM bookings WHERE status='checked_in' AND overdue_notified=0 AND end_ts<?", (n_s,)):
        res = q1("SELECT name FROM resources WHERE id=?", (b["resource_id"],))
        ex("UPDATE bookings SET overdue_notified=1 WHERE id=?", (b["id"],))
        log("overdue", None, b["id"], b["resource_id"], "Booked end time passed without return")
        notify(b["user_id"], f"{res['name']} is overdue (booking #{b['id']}). Please return it now or request an extension.", f"/booking/{b['id']}")
        notify_managers(b["resource_id"], f"OVERDUE: booking #{b['id']} ({res['name']}) has not been returned.", f"/booking/{b['id']}")
    for b in q("SELECT * FROM bookings WHERE status IN ('pending','waitlisted') AND end_ts<?", (n_s,)):
        ex("UPDATE bookings SET status='cancelled',decision_reason='Expired: the requested time passed before a decision.' WHERE id=?", (b["id"],))
        log("cancelled", None, b["id"], b["resource_id"], "Request expired (time slot passed)")
        notify(b["user_id"], f"Request #{b['id']} expired because the requested time passed.", f"/booking/{b['id']}")


# ---------------------------------------------------------------- discovery
SYNONYMS = {
    "print": ["3d", "printer", "printing", "prototype", "filament"],
    "3d": ["printer", "print", "prototype"],
    "prototype": ["3d", "printer", "laser", "cnc", "fabrication"],
    "cut": ["laser", "cnc", "router", "acrylic", "wood"],
    "laser": ["cut", "engrave", "acrylic"],
    "wood": ["cnc", "router", "laser"],
    "film": ["camera", "video", "studio", "shoot"],
    "video": ["camera", "film", "studio"],
    "photo": ["camera", "studio"],
    "camera": ["photo", "video", "film"],
    "train": ["gpu", "machine learning", "ml", "compute", "deep"],
    "ml": ["gpu", "compute", "train"],
    "gpu": ["compute", "train", "ml", "deep learning"],
    "compute": ["gpu", "workstation"],
    "circuit": ["electronics", "oscilloscope", "solder", "bench"],
    "solder": ["electronics", "bench", "circuit"],
    "meeting": ["room", "seminar", "projector", "present"],
    "present": ["room", "projector", "seminar"],
    "room": ["seminar", "meeting", "projector"],
}


def match_score(res, text):
    toks = [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(t) > 1]
    if not toks:
        return 0, []
    hay_name, hay_tags = res["name"].lower(), (res["tags"] or "").lower()
    hay_cat, hay_desc = res["category"].lower(), (res["description"] or "").lower()
    score, matched = 0, []
    for t in toks:
        pts = 0
        for word, w in ((t, 1.0),) + tuple((s, 0.6) for s in SYNONYMS.get(t, [])):
            pts = max(pts, (3 if word in hay_name else 0) * w, (2 if word in hay_tags else 0) * w,
                      (2 if word in hay_cat else 0) * w, (1 if word in hay_desc else 0) * w)
        if pts:
            matched.append(t)
        score += pts
    return score, matched


def is_free_at(res, start, end):
    return in_window(res["id"], start, end) and not overlapping(res["id"], ts(start), ts(end), OCCUPYING)


def week_grid(res, days=7):
    """Availability calendar: for each day, list of (hour, state) with state in closed/free/held/pending."""
    n = now()
    out = []
    ws = q("SELECT * FROM resource_windows WHERE resource_id=?", (res["id"],))
    lo = min([w["start_hour"] for w in ws] or [8])
    hi = max([w["end_hour"] for w in ws] or [18])
    for i in range(days):
        d = (n + timedelta(days=i)).replace(hour=0, minute=0)
        cells = []
        for h in range(lo, hi):
            s, e = d.replace(hour=h), d.replace(hour=h) + timedelta(hours=1)
            if not in_window(res["id"], s, e):
                state = "closed"
            elif e <= n:
                state = "past"
            elif overlapping(res["id"], ts(s), ts(e), OCCUPYING):
                state = "held"
            elif overlapping(res["id"], ts(s), ts(e), ("pending",)):
                state = "pending"
            else:
                state = "free"
            cells.append((h, state))
        out.append((d, cells))
    return out, lo, hi