"""Fictional demo data for 'Deccan Institute of Technology (DIT)'. No real people or records."""
import json
import os
from datetime import timedelta

from flask import g
from werkzeug.security import generate_password_hash

import core
from core import ex, log, now, q1, ts

PASSWORD = "Demo@123"
ALL_DAYS = range(7)


def init_db():
    core.db().executescript(core.SCHEMA)
    if not q1("SELECT 1 FROM users LIMIT 1"):
        seed_data()
    core.db().commit()
    d = g.pop("db", None)
    if d:
        d.close()


def reset(app):
    path = app.config["DB_PATH"]
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    up = app.config["UPLOAD_DIR"]
    for f in os.listdir(up):
        os.remove(os.path.join(up, f))
    init_db()


def open_day(n):
    """n-th upcoming day (1 = tomorrow) that is not a Sunday."""
    d, count = now().replace(hour=0, minute=0), 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() != 6:
            count += 1
    return d


def past_day(n):
    d, count = now().replace(hour=0, minute=0), 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() != 6:
            count += 1
    return d


def seed_data():
    n = now()
    pw = generate_password_hash(PASSWORD)

    def user(name, email, role, dept, cid, verified=1, pen=0):
        return ex("INSERT INTO users(name,email,pw_hash,role,department,college_id,verified,verified_by,penalty_points,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (name, email, pw, role, dept, cid, verified, 1 if verified else None, pen, ts(n - timedelta(days=60))))

    admin = user("Admin Sharma", "admin@dit.demo", "admin", "Registrar's Office", "STAFF-0001")
    meera = user("Dr. Meera Iyer", "owner@dit.demo", "owner", "Mechanical Engineering", "FAC-1042")
    arjun = user("Prof. Arjun Rao", "owner2@dit.demo", "owner", "Electronics & Communication", "FAC-0877")
    kiran = user("Kiran Patel", "manager@dit.demo", "manager", "Makerspace Technical Staff", "STAFF-2210")
    nisha = user("Nisha Verma", "manager2@dit.demo", "manager", "IT & Media Services", "STAFF-2301")
    aarav = user("Aarav Menon", "student@dit.demo", "user", "Computer Science", "CS21B0412")
    priya = user("Priya Nair", "student2@dit.demo", "user", "Electronics & Communication", "EC22B0230")
    rohan = user("Rohan Das", "student3@dit.demo", "user", "Mechanical Engineering", "ME20B0099", pen=1)
    diya = user("Diya Kapoor", "newstudent@dit.demo", "user", "Industrial Design", "ID23B0150")
    user("Sana Qureshi", "unverified@dit.demo", "user", "Civil Engineering", "CE23B0777", verified=0)

    def resource(owner, name, cat, loc, emoji, desc, rules, tags, mode, sup, maxh, cap, quals, windows, openl=2, adv=14):
        rid = ex("""INSERT INTO resources(owner_id,name,category,location,emoji,description,rules,tags,approval_mode,supervision,max_hours,weekly_cap_hours,max_open,advance_days,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (owner, name, cat, loc, emoji, desc, rules, tags, mode, sup, maxh, cap, openl, adv, ts(n - timedelta(days=45))))
        for qq in quals:
            ex("INSERT INTO resource_requirements(resource_id,qualification) VALUES(?,?)", (rid, qq))
        for days, s, e in windows:
            for d in days:
                ex("INSERT INTO resource_windows(resource_id,weekday,start_hour,end_hour) VALUES(?,?,?,?)", (rid, d, s, e))
        log("resource_listed", owner, None, rid, f"{name}; approval mode {mode}", at=ts(n - timedelta(days=45)))
        return rid

    printer = resource(meera, "Prusa MK4 3D Printer Bay", "Fabrication", "Makerspace, Block C - Room 12", "🖨️",
                       "Four Prusa MK4 printers (PLA/PETG). Bed size 250 x 210 x 220 mm. Slicer PCs provided. Idle most evenings and weekends.",
                       "Use only filament from the shelf or your own PLA/PETG. Never leave a running print unattended for more than 15 minutes. Remove your print and clean the bed before leaving.",
                       "3d printing prototype plastic pla petg additive fabrication", "auto", 0, 4, 6, ["3D Printer Certification"], [(range(0, 6), 9, 20)])
    laser = resource(meera, "60W CO2 Laser Cutter", "Fabrication", "Makerspace, Block C - Room 14", "🔦",
                     "600 x 400 mm bed. Cuts acrylic, plywood and card; engraves many materials. Staff member supervises every session. High demand before project deadlines.",
                     "Approved materials only (no PVC, no vinyl). Extraction fan must be on. Never leave the machine running unattended. Report any flame immediately.",
                     "laser cut engrave acrylic plywood wood prototype fabrication", "manual", 1, 3, 5, ["Laser Cutter Certification", "Lab Safety Induction"],
                     [(range(0, 5), 10, 17), ([5], 10, 14)])
    cnc = resource(meera, "3-Axis CNC Router", "Fabrication", "Workshop, Block C - Room 3", "⚙️",
                   "1200 x 800 mm bed router for wood, foam and plastics. Operator training and staff supervision required.",
                   "Safety glasses and ear defence mandatory. Secure workpiece before starting. Staff must approve toolpaths before cutting.",
                   "cnc router wood milling foam fabrication cut", "manual", 1, 4, 6, ["CNC Machine Operator", "Lab Safety Induction"], [(range(0, 5), 10, 16)])
    bench = resource(arjun, "Electronics Bench & Oscilloscope Room", "Electronics", "ECE Lab 2, Block A", "🔬",
                     "Six benches with scopes, bench supplies, function generators, soldering stations and a logic analyser.",
                     "ESD wrist strap required. Do not connect mains-powered prototypes without staff sign-off. Switch off all instruments when leaving.",
                     "electronics circuit solder oscilloscope signal pcb bench", "auto", 0, 4, 8, ["Electronics Bench Training", "Lab Safety Induction"], [(range(0, 6), 9, 21)])
    gpu = resource(arjun, "GPU Workstation (RTX 4090, 64 GB)", "Compute", "Compute Room, Block D - Level 2", "🖥️",
                   "Dedicated workstation for model training and rendering. Sessions are time-boxed so everyone gets a turn.",
                   "No crypto-mining. Do not store personal or sensitive datasets after your session. Log out and clear scratch space on exit.",
                   "gpu machine learning ml deep learning training compute rendering", "manual", 0, 6, 12, ["HPC Cluster Access Agreement"], [(ALL_DAYS, 8, 22)])
    cam = resource(arjun, "Mirrorless Camera Kit (Sony A7 + lenses)", "Media", "Media Lab, Block B - Room 101", "📷",
                   "Full-frame camera, 24-70 mm and 70-200 mm lenses, tripod and two batteries. Loan item - collected from and returned to the Media Lab desk.",
                   "Keep the kit in the supplied case when travelling. No use near water or at unsupervised outdoor events. Report any lens scratch at return.",
                   "camera photo video film shoot media equipment loan", "manual", 0, 8, 10, ["Camera & Studio Training"], [(range(0, 5), 9, 18)])
    room = resource(arjun, "Seminar Room B-204", "Spaces", "Block B - Second Floor", "🪑",
                    "Seats 30, projector, whiteboards and video-conferencing camera. Great for study groups, club meetings and project reviews.",
                    "Leave the room as you found it. Food is not allowed. Lights, AC and projector off when leaving.",
                    "room meeting seminar space presentation projector study group", "auto", 0, 3, 6, [], [(range(0, 6), 8, 20)], openl=3)
    for rid, mgr in ((printer, kiran), (laser, kiran), (cnc, kiran), (bench, kiran), (gpu, nisha), (cam, nisha), (room, nisha)):
        ex("INSERT INTO resource_managers(resource_id,user_id) VALUES(?,?)", (rid, mgr))

    def cred(uid, name, status="approved", reviewer=kiran, days_ago=30, note="", ref=None):
        ex("INSERT INTO credentials(user_id,name,ref,status,reviewer_id,review_note,created_at,reviewed_at) VALUES(?,?,?,?,?,?,?,?)",
           (uid, name, ref or f"CERT-{uid}{abs(hash(name)) % 900 + 100}", status, reviewer if status != "pending" else None, note,
            ts(n - timedelta(days=days_ago)), ts(n - timedelta(days=days_ago - 1)) if status != "pending" else None))

    for u in (aarav, priya, rohan, diya):
        cred(u, "Lab Safety Induction", days_ago=50)
    cred(aarav, "3D Printer Certification")
    cred(aarav, "Laser Cutter Certification")
    cred(aarav, "HPC Cluster Access Agreement", reviewer=nisha)
    cred(priya, "Laser Cutter Certification")
    cred(priya, "Electronics Bench Training")
    cred(priya, "Camera & Studio Training", reviewer=nisha)
    cred(rohan, "Laser Cutter Certification")
    cred(rohan, "CNC Machine Operator")
    cred(rohan, "3D Printer Certification")
    cred(diya, "3D Printer Certification", status="pending", days_ago=1)
    cred(diya, "Laser Cutter Certification", status="rejected", note="Certificate image unreadable - please resubmit a clear copy", days_ago=3)

    def history(rid, uid, day_n, sh, eh, status="completed", ptype="project", purpose="", cond_in="good", cond_out="good", late=0, mgr=kiran):
        d = past_day(day_n)
        s, e = d.replace(hour=sh), d.replace(hour=eh)
        created = s - timedelta(days=2)
        bid = ex("""INSERT INTO bookings(resource_id,user_id,start_ts,end_ts,purpose,purpose_type,status,decision_by,decision_reason,decided_at,created_at,
                    checkin_at,checkin_condition,checkin_notes,return_at,return_condition,return_notes,late_minutes,confirmed_by,confirmed_at,confirm_note,checks_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (rid, uid, ts(s), ts(e), purpose, ptype, status, mgr, "Approved by staff. Eligibility and fair-use checks passed.", ts(created + timedelta(hours=3)), ts(created),
                  ts(s), cond_in, "Machine clean, all accessories present.", ts(e + timedelta(minutes=late)), cond_out,
                  "Cleaned up and powered off." if cond_out != "damaged" else "Lens cap cracked during transport.", late, mgr, ts(e + timedelta(minutes=late + 20)),
                  "Inspected, OK" if cond_out in ("good", "excellent") else "Damage noted and logged", "[]"))
        log("request_submitted", uid, bid, rid, f"{ts(s)} to {ts(e)}; purpose type: {ptype}", at=ts(created))
        log("approved", mgr, bid, rid, "Approved by staff. Eligibility and fair-use checks passed.", at=ts(created + timedelta(hours=3)))
        log("checked_in", uid, bid, rid, f"Initial condition: {cond_in}.", at=ts(s))
        log("returned", uid, bid, rid, f"Final condition: {cond_out}. Late by {late} min.", at=ts(e + timedelta(minutes=late)))
        log("return_confirmed", mgr, bid, rid, "Return inspected and confirmed", at=ts(e + timedelta(minutes=late + 20)))
        return bid

    history(printer, aarav, 5, 10, 13, ptype="coursework", purpose="Printing enclosure for IoT sensor project (coursework CS331)")
    history(laser, rohan, 12, 10, 12, ptype="project", purpose="Acrylic panels for robotics club chassis")
    history(laser, rohan, 8, 11, 14, ptype="project", purpose="Second iteration of chassis panels")
    bad = history(laser, rohan, 4, 10, 12, ptype="project", purpose="Plywood jig for competition entry", cond_out="fair", late=40)
    history(cam, priya, 6, 10, 15, ptype="coursework", purpose="Documentary short for Media Studies elective")
    cam_bad = history(cam, aarav, 3, 9, 13, ptype="personal", purpose="Hackathon team photo shoot", cond_out="damaged")
    iid = ex("""INSERT INTO incidents(resource_id,booking_id,reporter_id,subject_user_id,kind,severity,description,source,status,resolution,penalty_points,resolved_by,created_at,resolved_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
             (laser, bad, None, rohan, "delay", "low", "Returned 40 minutes after the booked end time. Flagged for staff review; no automatic penalty.", "system", "resolved",
              "Delay caused a clash with the next class. Rohan apologised; 1 penalty point recorded as a first warning.", 1, kiran, ts(past_day(4).replace(hour=13)), ts(past_day(3).replace(hour=9))))
    log("incident_reported", None, bad, laser, f"#{iid} delay (low, system): Returned 40 minutes after the booked end time.", at=ts(past_day(4).replace(hour=13)))
    log("incident_resolved", kiran, bad, laser, f"#{iid} resolved; penalty points 1. Delay caused a clash with the next class.", at=ts(past_day(3).replace(hour=9)))
    iid2 = ex("""INSERT INTO incidents(resource_id,booking_id,reporter_id,subject_user_id,kind,severity,description,source,status,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (cam, cam_bad, None, aarav, "damage", "high", "Condition changed from 'good' at check-in to 'damaged' at return (user flagged an issue). User notes: Lens cap cracked during transport.",
               "system", "open", ts(past_day(3).replace(hour=13))))
    log("incident_reported", None, cam_bad, cam, f"#{iid2} damage (high, system): lens cap cracked during transport", at=ts(past_day(3).replace(hour=13)))

    def future(rid, uid, day_n, sh, eh, status, ptype, purpose, created_hours_ago, reason=None, auto=0):
        d = open_day(day_n)
        s, e = d.replace(hour=sh), d.replace(hour=eh)
        created = n - timedelta(hours=created_hours_ago)
        bid = ex("""INSERT INTO bookings(resource_id,user_id,start_ts,end_ts,purpose,purpose_type,status,decision_reason,decided_at,decision_by,auto_decided,created_at,checks_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (rid, uid, ts(s), ts(e), purpose, ptype, status, reason, ts(created) if reason else None, kiran if status == "approved" and not auto else None, auto, ts(created), "[]"))
        log("request_submitted", uid, bid, rid, f"{ts(s)} to {ts(e)}; purpose type: {ptype}", at=ts(created))
        if status == "approved":
            log("approved", kiran, bid, rid, reason, at=ts(created + timedelta(minutes=30)))
        return bid

    # Rohan already holds Laser Cutter slot 10-12; Priya and Aarav are competing for the 14-16 slot (manager must decide)
    future(laser, rohan, 2, 10, 12, "approved", "project", "Cutting acrylic panels for robotics club entry", 20,
           "Approved by Kiran Patel. Eligibility and fair-use checks passed; no competing request at the time.")
    future(laser, priya, 3, 14, 16, "pending", "coursework", "Laser-cut enclosure for Embedded Systems course project demo", 6)
    future(laser, aarav, 3, 14, 16, "pending", "project", "Cutting layered acrylic sign for a startup stall at the college fest", 2)
    future(cnc, rohan, 4, 11, 14, "pending", "research", "Milling foam moulds for composite layup experiments (thesis work)", 1)
    future(printer, aarav, 2, 15, 17, "approved", "coursework", "Printing mounting brackets for drone frame", 5,
           "Auto-approved: eligibility, opening hours, session length and fair-use limits all passed, and the owner enabled auto-approval for this resource.", auto=1)
    core.set_setting("clock_offset_min", 0)
    core.db().commit()
