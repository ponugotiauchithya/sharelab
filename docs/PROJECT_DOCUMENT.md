# ShareLab — Project Document

**Problem statement:** Unlock Underused Equipment and Spaces
**One line:** A trusted platform where a college's labs, tools and rooms are shared safely — owners stay in control, users know exactly why they were approved or not, and every action is auditable.


## 1. Selected use case

**A single institution — the college — sharing its makerspace, labs, compute and rooms with its own students and researchers.** (Fictional demo institution: "Deccan Institute of Technology".)

Why single-institution: eligibility (training, supervision, ID) is verifiable through one authority, trust is simpler, and a real college could adopt it without cross-organisation legal agreements. The data model is organisation-ready (resources, owners, managers) so multi-institution sharing is a configuration extension, not a rewrite.

Resource types in the prototype: 3D printers, laser cutter, CNC router, electronics bench, GPU workstation, camera kit (loan item), seminar room — covering **equipment, shared technical resources, loaned items and spaces**.

## 2. Target users and resource owners

| Persona | Need | What the product gives them |
|---|---|---|
| **Student / early-stage team** | Access to equipment they cannot afford; clarity on what is needed | Search in plain words, see eligibility before applying, dry-run a booking, understand every decision |
| **Resource owner (faculty / lab head)** | Keep control and protect equipment while letting idle time be used | Owner-defined conditions, approval mode, caps, supervision flag, manager delegation, condition records |
| **Manager (lab technician / staff)** | Fast, fair, defensible decisions | One review queue with checks, scores and competing requests; return confirmation; incident handling |
| **Administrator** | Institutional accountability | Identity verification, full audit history with integrity check |

## 3. Why the problem matters (reasoning / evidence)

* Specialised equipment is expensive but typically scheduled around classes, so evenings, weekends and exam-free weeks sit idle while students outside the owning department or lab cannot access it. [Add: 1–2 citations or a short survey/interview of your own college's lab usage.]
* Ordinary booking calendars solve *availability* only. Sharing institutional property also raises **eligibility** (safety training), **accountability** (who had it, in what condition), **fairness** (scarce machines before deadlines) and **dispute handling**. Owners refuse to share when these are missing — so resources stay locked.
* Owners' main fear is *loss of control*; users' main frustration is *opaque decisions*. The product addresses both: owners decide (or opt into auto-approval per resource), and every outcome carries a stated reason.

## 4. Proposed solution

A workflow platform, not a calendar:

1. Owner lists a resource with opening windows, rules, required qualifications, supervision, caps and approval mode.
2. Users discover resources (rule-based matching with synonyms, time filter, eligibility filter).
3. Users see a live checklist of what they lack and can submit credentials for staff review.
4. A **dry-run** shows exactly which rules pass or fail before a request is made.
5. The engine verifies eligibility, hours, session length, fair-use limits and clashes; outcome is *approved (if the owner allows auto)*, *pending human review*, or *waitlisted*.
6. Managers decide with an explainable priority score; overrides require justification.
7. Check-in records condition and rule acknowledgement; return records final condition; staff confirm.
8. Problems create **incidents** a human resolves; users can respond and dispute.
9. Everything lands in a tamper-evident history visible to both parties.

## 5. Complete product workflow

```
Owner lists ─► User discovers ─► Eligibility check / credential review
        │                                   │
        │                           Dry-run & request
        ▼                                   ▼
 Manager review ◄──── automatic checks ───► Auto-approve (if owner enabled)
   │approve │reject(reason)  │waitlist(reason)          │
   ▼        ▼                ▼                          ▼
 Approved  Rejected   Waiting list ──slot frees──► promoted to review
   │
   ├─ cancel (late cancel flagged) ─► waiting list promoted
   ├─ no check-in in 30 min ─► no-show, slot released, incident (no auto penalty)
   ▼
 Check-in (condition + rule acknowledgement) ─► extension request (manager decides)
   │ overdue ─► reminders to user & manager
   ▼
 Return (condition) ─► Staff confirm ─► Completed
   │ late / worse condition ─► incident ─► human resolution ─► (points) ─► dispute / appeal
   ▼
 Full history for both parties (+ JSON export)
```

## 6. Features developed during the hackathon

All of the following are implemented and covered by `tests/e2e_test.py` (44 checks):

* Resource listing & editing; opening windows; required qualifications; supervision; approval mode; caps
* Search with synonyms; category, time-slot and "eligible only" filters; 7-day availability calendar
* Registration, admin identity verification; credentials with reference, expiry, optional upload, staff review
* Dry-run eligibility/slot check; booking requests; approval / rejection / waiting list with mandatory reasons
* Conflict detection, competing requests shown side by side, auto-waitlisting, auto-promotion on cancellation/no-show
* Fair-use: weekly cap, open-booking limit, waitlist limit, explainable priority score
* Check-in/out with condition before/after, rule acknowledgement, return confirmation
* Extensions, cancellations, late-cancellation flag, no-show release, reminders, overdue alerts
* Incidents (damage, delay, conflict, safety, misuse), comments, dispute, reopen, human-assigned penalty points
* In-app notifications; hash-chained audit trail; per-booking timeline; JSON record export; admin integrity check
* Role-based access for user / owner / manager / admin; demo clock; seed data; automated tests

## 7. Technology stack and architecture

* **Python 3.11+ / Flask**, Jinja2 server-rendered UI (works without JavaScript, accessible, fast to review)
* **SQLite** (single file; 12 tables: users, credentials, resources, requirements, windows, managers, bookings, incidents, comments, audit_log, notifications, settings)
* **Gunicorn** for deployment

```
Browser ─► Flask routes (app.py: auth, CSRF, role checks, rendering)
              │
              ▼
          core.py  (rules engine — no web code)
          ├─ eligibility()  evaluate()  score_booking()
          ├─ create_booking() decide() cancel() promote_waitlist()
          ├─ check_in() return_booking() confirm_return() extensions
          ├─ incidents + penalties
          ├─ log()  → hash-chained audit_log
          └─ sweep() → reminders, overdue, no-show, expiry (runs on each request; swap for a scheduler in production)
              │
              ▼
           SQLite
```

Design choice: **all decision logic lives in `core.py`**, so rules are testable and explainable; routes only enforce permissions.

## 8. AI tools, APIs and external services

* **No external APIs and no runtime AI dependency.** Matching and ranking are deterministic, rule-based and explainable by design.
* AI-assisted development: Claude (Anthropic) was used to help write code, tests and documents. The team reviewed and tested the output. [Edit to reflect your actual use.]
* Where AI could add value later (not enabled): semantic matching of free-text needs, OCR/reading of certificate scans, anomaly detection for suspicious usage, summarising incident reports. In every case AI would only *recommend*; a person decides.

## 9. Fairness, privacy and security considerations

**Fairness**
* Hard limits per person per resource: weekly hours, simultaneous open bookings, waitlist places, session length, booking horizon.
* Competing requests are ranked by a published score: base 50 · purpose (coursework +15, research +12, project +8, personal +3) · −2 per hour used on this resource in last 30 days (cap −30) · +8 first-time user · −10 per penalty point (cap −30) · +1 per hour waiting (cap +10).
* The score is a recommendation. A manager may override but **must write a justification**, shown to those affected and logged.
* Every outcome shows its reason; waitlisted users see their queue position.
* Penalties are never automatic: delays, damage and no-shows create incidents; only a human assigns points; users can dispute; admin can reset.

**Privacy**
* Data minimised: name, department, college ID, purpose, credentials. Managers see only the last 4 characters of an ID, never an email or contact detail.
* Credential documents downloadable only by the owner and reviewers of that qualification.
* Users see only their own bookings, incidents and activity.
* Demo data is fictional.

**Security**
* Werkzeug password hashing; server-side sessions with HttpOnly/SameSite cookies; CSRF token on every POST; secure response headers; upload allow-list and size cap; role and ownership checks on every route; parameterised SQL.
* Audit log entries are chained with SHA-256; admin can verify integrity.

## 10. Known limitations

* Single institution; no SSO — identity is verified manually by an admin.
* SQLite and a request-time sweep are fine for a campus pilot; production would use PostgreSQL and a scheduler.
* Notifications are in-app only (no email/SMS).
* No payments or credits: bookings are free/institutionally sponsored.
* Document review is manual; there is no malware scan on uploads.
* The demo clock is a judging aid and must be disabled in production (`DEMO_MODE=0`).
* Hash-chained audit log detects tampering but is not immutable storage.

## 11. Future development

Multi-institution federation with inter-college agreements; SSO / LDAP; email & WhatsApp reminders; QR-code check-in and tag/NFC verification; credit-based or sponsored paid bookings with deposits; AI-assisted document checking and anomaly detection (recommendations only); maintenance schedules and automatic blocking after damage; calendar sync; analytics on idle capacity.

## 12. Team

| Name | Role | Contribution |
|---|---|---|
| Ponugoti Auchithya |Lead Developer|Backend, database, booking workflows and deployment|
| Chalamalla Abhiram Reddy |QA & Product Developer |Fair allocation, testing, bug fixing and documentation|

## 13. Disclosures

* Libraries: Flask, Werkzeug, Jinja2 (BSD), SQLite (public domain), Gunicorn (MIT).
* No pre-existing templates, UI kits or application code were used. Styling is hand-written CSS.
* AI assistance: see §8.
* Demo data is fictional; no confidential institutional records or real personal data were used.
* Testing instructions and credentials: see `README.md`.
