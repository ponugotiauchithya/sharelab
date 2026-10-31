"""End-to-end test of the full sharing lifecycle through the real web layer.
Run:  python tests/e2e_test.py       (uses a throw-away database)
"""
import os, re, sys, tempfile
os.environ["SHARELAB_NO_AUTOCREATE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as appmod
import core
from datetime import timedelta

tmp = tempfile.mkdtemp()
application = appmod.create_app(os.path.join(tmp, "t.db"))
PASSED = []

def check(cond, label):
    if not cond:
        print("FAIL:", label); sys.exit(1)
    PASSED.append(label); print("  ok -", label)

class Client:
    def __init__(self, email):
        self.c = application.test_client()
        r = self.c.get("/login")
        self.c.post("/login", data=dict(email=email, password="Demo@123", _csrf=self.tok(r)))
    def tok(self, r=None):
        r = r or self.c.get("/about")
        return re.search(r'name="_csrf" value="([0-9a-f]+)"', r.get_data(as_text=True)).group(1)
    def get(self, url):
        return self.c.get(url, follow_redirects=True)
    def post(self, url, **data):
        data["_csrf"] = self.tok()
        return self.c.post(url, data=data, follow_redirects=True)
    def text(self, r): return r.get_data(as_text=True)

def db_row(sql, a=()):
    with application.app_context():
        r = core.db().execute(sql, a).fetchone()
        return dict(r) if r else None

def db_all(sql, a=()):
    with application.app_context():
        return [dict(r) for r in core.db().execute(sql, a).fetchall()]

def next_day(n=1):
    with application.app_context():
        from seed import open_day
        return open_day(n)

print("1. Owner lists a resource")
owner = Client("owner2@dit.demo")
d = {"name": "Soldering & Rework Station", "category": "Electronics", "location": "ECE Lab 3", "emoji": "🔥",
     "description": "Hot-air rework + fine-pitch soldering", "rules": "ESD strap on. Fume extractor on.", "tags": "solder rework smd electronics",
     "approval_mode": "manual", "max_hours": "3", "weekly_cap_hours": "4", "max_open": "2", "advance_days": "14", "quals": ["Electronics Bench Training"]}
for i in range(7):
    d[f"open_{i}"] = "1"; d[f"from_{i}"] = "9"; d[f"to_{i}"] = "18"
r = owner.post("/resources/new", **d)
check("Soldering &amp; Rework Station" in owner.text(r) or "Soldering" in owner.text(r), "owner listed resource")
rid = db_row("SELECT id FROM resources WHERE name LIKE 'Soldering%'")["id"]

print("2. User discovers + eligibility")
priya = Client("student2@dit.demo")
r = priya.get("/resources?q=solder+smd+board&eligible=1")
check("Soldering" in priya.text(r), "smart search finds resource & shows eligible")
diya = Client("newstudent@dit.demo")
day = next_day(1).strftime("%Y-%m-%d")
r = diya.post(f"/resource/{rid}/book", date=day, start="10:00", hours="2", purpose_type="project", purpose="Soldering a sensor board", action="check")
check("cannot be submitted" in diya.text(r) and "Not submitted" in diya.text(r), "ineligible user is blocked with a clear reason")
check(db_row("SELECT COUNT(*) c FROM bookings WHERE resource_id=?", (rid,))["c"] == 0, "no booking created for ineligible user")

print("3. Booking request (fair-use caps)")
r = priya.post(f"/resource/{rid}/book", date=day, start="10:00", hours="3.5", purpose_type="project", purpose="Soldering a sensor board", action="submit")
check(db_row("SELECT COUNT(*) c FROM bookings WHERE resource_id=?", (rid,))["c"] == 0, "session longer than max rejected")
r = priya.post(f"/resource/{rid}/book", date=day, start="10:00", hours="2.5", purpose_type="coursework", purpose="Soldering sensor board for coursework", action="submit")
bid = db_row("SELECT id,status FROM bookings WHERE resource_id=?", (rid,))
check(bid["status"] == "pending", "request is pending owner review")
bid = bid["id"]
r = priya.post(f"/resource/{rid}/book", date=day, start="14:00", hours="2", purpose_type="coursework", purpose="Second long soldering session", action="submit")
check(db_row("SELECT COUNT(*) c FROM bookings WHERE resource_id=?", (rid,))["c"] == 1, "weekly cap (4h) blocks the second 3h request")

print("4. Owner reviews: reject needs reason, approve works")
r = owner.post(f"/booking/{bid}/decide", action="reject", reason="")
check(db_row("SELECT status FROM bookings WHERE id=?", (bid,))["status"] == "pending", "reject without reason refused")
r = owner.post(f"/booking/{bid}/decide", action="approve", reason="")
check(db_row("SELECT status FROM bookings WHERE id=?", (bid,))["status"] == "approved", "owner approved")

print("5. Conflict -> waiting list")
priya2 = Client("student@dit.demo")  # Aarav lacks electronics training; give him eligibility via approved credential
with application.app_context():
    core.ex("INSERT INTO credentials(user_id,name,status,reviewer_id,created_at) VALUES((SELECT id FROM users WHERE email='student@dit.demo'),'Electronics Bench Training','approved',1,?)", (core.ts(core.now()),))
    core.db().commit()
r = priya2.post(f"/resource/{rid}/book", date=day, start="11:00", hours="1", purpose_type="project", purpose="Fix a drone ESC board", action="submit")
w = db_row("SELECT id,status FROM bookings WHERE resource_id=? AND user_id!=(SELECT user_id FROM bookings WHERE id=?)", (rid, bid))
check(w["status"] == "waitlisted", "overlapping request lands on the waiting list")

print("6. Check-in only at the right time, then jump the demo clock")
r = priya.post(f"/booking/{bid}/checkin", condition="good", notes="", ack="1")
check(db_row("SELECT status FROM bookings WHERE id=?", (bid,))["status"] == "approved", "early check-in refused")
priya.post("/admin/clock", act="to_booking", target=str(bid), next="/dashboard")
r = priya.post(f"/booking/{bid}/checkin", condition="good", notes="Tip slightly worn", ack="")
check(db_row("SELECT status FROM bookings WHERE id=?", (bid,))["status"] == "approved", "check-in requires rule acknowledgement")
r = priya.post(f"/booking/{bid}/checkin", condition="good", notes="Tip slightly worn", ack="1")
check(db_row("SELECT status FROM bookings WHERE id=?", (bid,))["status"] == "checked_in", "check-in recorded with condition")

print("7. Extension, overdue, late return, damage -> incidents")
priya.post(f"/booking/{bid}/extend", hours="0.5", reason="Need to reflow second board")
check(db_row("SELECT ext_status FROM bookings WHERE id=?", (bid,))["ext_status"] == "requested", "extension requested")
owner.post(f"/booking/{bid}/extension-decision", decision="deny", note="")
check(db_row("SELECT ext_status FROM bookings WHERE id=?", (bid,))["ext_status"] == "requested", "denial needs reason")
owner.post(f"/booking/{bid}/extension-decision", decision="deny", note="Another student is waiting on the waiting list")
check(db_row("SELECT ext_status FROM bookings WHERE id=?", (bid,))["ext_status"] == "denied", "extension denied with reason")
priya.post("/admin/clock", act="to_end", target=str(bid), extra="45", next="/dashboard")
check(db_row("SELECT overdue_notified FROM bookings WHERE id=?", (bid,))["overdue_notified"] == 1, "overdue detected + notified")
check(db_row("SELECT COUNT(*) c FROM notifications WHERE message LIKE '%overdue%'")["c"] >= 2, "overdue notifications to user and owner")
r = priya.post(f"/booking/{bid}/return", condition="fair", notes="Tip bent", damage="1")
b = db_row("SELECT * FROM bookings WHERE id=?", (bid,))
check(b["status"] == "returned" and b["late_minutes"] >= 40, "return recorded with lateness")
inc = db_all("SELECT kind FROM incidents WHERE booking_id=?", (bid,))
check({"delay", "damage"} <= {i["kind"] for i in inc}, "delay and damage incidents raised automatically")

print("8. Owner confirms return, resolves incident (human decision), user disputes")
owner.post(f"/booking/{bid}/confirm", note="Tip replaced; no further damage")
check(db_row("SELECT status FROM bookings WHERE id=?", (bid,))["status"] == "completed", "return confirmed -> completed")
iid = db_row("SELECT id FROM incidents WHERE booking_id=? AND kind='damage'", (bid,))["id"]
check(db_row("SELECT penalty_points p FROM users WHERE email='student2@dit.demo'")["p"] == 0, "no automatic penalty applied")
r = priya.get(f"/incident/{iid}"); check(r.status_code == 200, "subject can view incident")
check(Client("student3@dit.demo").get(f"/incident/{iid}").status_code == 403, "unrelated user cannot view incident")
owner.post(f"/incident/{iid}/resolve", outcome="Soldering tip bent, replacement cost small. Warning issued.", points="1")
check(db_row("SELECT penalty_points p FROM users WHERE email='student2@dit.demo'")["p"] == 1, "penalty applied by human decision")
priya.post(f"/incident/{iid}/comment", body="I reported this myself right away; please reconsider.", dispute="1")
check(db_row("SELECT status FROM incidents WHERE id=?", (iid,))["status"] == "disputed", "user can dispute")

print("9. History visible to both parties, tamper-evident")
for who in (priya, owner):
    t = who.text(who.get(f"/booking/{bid}"))
    check("Checked in" in t and "Return confirmed" in t and "Extension denied" in t, "full history visible to both parties")
r = owner.get(f"/booking/{bid}/record.json"); check(r.status_code == 200 and b"history" in r.data, "record export works")
with application.app_context():
    check(core.verify_chain()[0], "audit chain intact")

print("10. Permissions & privacy")
check(Client("student3@dit.demo").get(f"/booking/{bid}").status_code == 403, "other student cannot open booking")
check(Client("student@dit.demo").get("/manage").status_code == 403, "student cannot open review queue")
check(Client("student@dit.demo").get("/admin").status_code == 403, "student cannot open admin")
c = application.test_client(); r = c.post("/login", data=dict(email="x", password="y")); check(r.status_code == 400, "CSRF enforced on POST")
unv = Client("unverified@dit.demo")
r = unv.post(f"/resource/{rid}/book", date=day, start="15:00", hours="1", purpose_type="project", purpose="Trying to book while unverified", action="submit")
check(db_row("SELECT COUNT(*) c FROM bookings WHERE user_id=(SELECT id FROM users WHERE email='unverified@dit.demo')")["c"] == 0, "unverified user cannot book")

print("11. Competing requests, explainable priority, justified override, waitlist promotion (seed data)")
kiran = Client("manager@dit.demo")
r = kiran.get("/manage"); t = kiran.text(r)
check("Priority score" in t and "competing request" in t, "queue shows competing requests with scores")
laser_pend = db_all("SELECT b.id,u.email FROM bookings b JOIN users u ON u.id=b.user_id WHERE b.resource_id=2 AND b.status='pending'")
with application.app_context():
    sc = {x["email"]: core.score_booking(core.get_booking(x["id"]))[0] for x in laser_pend}
low = min(laser_pend, key=lambda x: sc[x["email"]]); high = max(laser_pend, key=lambda x: sc[x["email"]])
check(sc[low["email"]] < sc[high["email"]], f"scores differ ({sc})")
kiran.post(f"/booking/{low['id']}/decide", action="approve", reason="")
check(db_row("SELECT status FROM bookings WHERE id=?", (low["id"],))["status"] == "pending", "approving lower priority without justification is refused")
kiran.post(f"/booking/{high['id']}/decide", action="approve", reason="")
check(db_row("SELECT status FROM bookings WHERE id=?", (high["id"],))["status"] == "approved", "highest priority approved")
check(db_row("SELECT status FROM bookings WHERE id=?", (low["id"],))["status"] == "waitlisted", "competitor auto-waitlisted with explanation")
check("Another request" in (db_row("SELECT decision_reason r FROM bookings WHERE id=?", (low["id"],))["r"]), "waitlist reason explains why")
holder = Client(high["email"])
holder.post(f"/booking/{high['id']}/cancel", reason="Project plans changed")
check(db_row("SELECT status FROM bookings WHERE id=?", (low["id"],))["status"] == "pending", "waitlisted request promoted to review when slot frees")
print("12. No-show releases slot")
with application.app_context():
    core.set_setting("clock_offset_min", 60 * 24 * 10); core.db().commit()
Client("admin@dit.demo").get("/dashboard")
check(db_row("SELECT COUNT(*) c FROM bookings WHERE status='no_show'")["c"] >= 1, "approved-but-unused bookings become no-shows")
check(db_row("SELECT COUNT(*) c FROM incidents WHERE kind='no_show'")["c"] >= 1, "no-show raises reviewable incident (no auto penalty)")
print(f"\nALL {len(PASSED)} CHECKS PASSED")
