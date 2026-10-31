# ShareLab — trusted sharing of underused labs, equipment and spaces

A working prototype for the problem statement **"Unlock Underused Equipment and Spaces"**.
Focused use case: **one college (fictional "Deccan Institute of Technology") sharing its makerspace, labs, compute and rooms with its own students and researchers.**

Everything runs from one command and one SQLite file. Demo data is fictional.

## Run it

```bash
pip install -r requirements.txt
python app.py            # http://localhost:5000  (database + demo data are created on first start)
```

Deploy: `Procfile` / `render.yaml` are included (gunicorn). Environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | session signing key | dev value (**set it in production**) |
| `DEMO_MODE` | `1` shows one-click demo logins and the simulated-clock panel | `1` |
| `SHARELAB_DB` | path of the SQLite file | `instance/sharelab.db` |

Run the automated tests (throw-away database, ~44 checks over the whole lifecycle):

```bash
python tests/e2e_test.py
python tests/crawl_test.py
```

## Judge accounts

All passwords: `Demo@123`. The login page also has one-click sign-in buttons.

| Role | Email | Notes |
|---|---|---|
| Admin | `admin@dit.demo` | verifies identities, audit-integrity check, resets demo data |
| Owner | `owner@dit.demo` (Dr. Meera Iyer) | owns 3D printer, laser cutter, CNC |
| Owner | `owner2@dit.demo` (Prof. Arjun Rao) | owns electronics bench, GPU, camera, seminar room |
| Manager | `manager@dit.demo` (Kiran) | staff for makerspace resources |
| Manager | `manager2@dit.demo` (Nisha) | staff for GPU / camera / room |
| Student | `student@dit.demo` (Aarav) | eligible for 3D printer, laser, GPU |
| Student | `student2@dit.demo` (Priya) | eligible for laser, electronics, camera |
| Student | `student3@dit.demo` (Rohan) | heavy laser user, 1 penalty point |
| Student | `newstudent@dit.demo` (Diya) | almost no credentials — shows the eligibility flow |
| Student | `unverified@dit.demo` (Sana) | identity not verified — cannot book |

## Complete journey in ~5 minutes (listing → return → history)

1. **Owner lists a resource.** Sign in as `owner2@dit.demo` → *Review queue* → **+ List a resource**. Name it e.g. "Soldering Station", require *Electronics Bench Training*, open every day 9–18, set approval to *Manager reviews each request*, max 3 h, weekly cap 4 h. Save.
2. **User discovers it and checks eligibility.** Sign out, sign in as `newstudent@dit.demo` (Diya). *Browse resources*, search "solder circuit board", open the new resource. The "Can I use this?" panel shows what she is missing.
3. **User gets verified.** *My credentials* → submit **Electronics Bench Training** (any reference text; file optional). Sign in as `owner2@dit.demo` → **Verify credentials** → Approve.
4. **User requests a booking.** Back as Diya → resource page → pick tomorrow (or any open day), 10:00, 2 h → **Check eligibility & slot (dry run)** shows every rule, then **Submit request**. Status: *Awaiting review*.
5. **System verification** is shown on the booking page (identity, qualifications, opening hours, session length, weekly cap, open-request limit, clashes).
6. **Owner reviews.** As the owner → *Review queue* → request card with priority score and "Why this score?" → **Approve** (reject/waitlist need a written reason). Diya is notified.
7. **Check-in.** As Diya → booking page. Use the **Demo tools** panel (bottom-right) or the "Jump to 5 min before start" button to move the simulated clock. **Check in**: record condition, acknowledge usage rules.
8. **Return.** Booking page → **Return**: record final condition (try a worse condition or tick "I noticed damage"). Owner → *Review queue* → **Confirm return**.
9. **Delay / damage / conflict.** Returning late or in worse condition raises an incident automatically (no automatic penalty). Owner opens it → resolves with reasoning and optional penalty points. Diya can comment or **dispute**.
10. **History.** Both parties see the full timeline on the booking page; **Download record (JSON)** exports it; *Activity log* shows the hash-chained audit trail (admin sees an integrity check).

### Fairness demo (seeded)

Sign in as `manager@dit.demo` → *Review queue*. Two students (Priya, Aarav) compete for the same **Laser Cutter** slot; Rohan already holds the neighbouring slot and has used the machine heavily. Open "Why this score?", approve the lower-ranked one → the system demands a written justification; approve the top-ranked one → the other is moved to the waiting list **with an explanation**. Cancel the approved booking → the waiting request is promoted back to review automatically.

## What is in the box

* **Roles:** user, owner, manager, admin; every route checks role and ownership.
* **Listings:** opening windows, rules, required qualifications, supervision flag, approval mode (manual / auto), per-person caps.
* **Discovery:** plain-language matching with synonyms, category, "free at this time" filter, "only what I'm eligible for".
* **Eligibility:** identity verification, per-resource qualifications with review, expiry, upload (optional).
* **Booking engine:** dry-run, approve / reject / waitlist, conflict detection, auto-waitlist, auto-promotion, cancellation, extensions.
* **Fairness:** weekly hour cap, max open bookings, waiting-list limit, explainable priority score, mandatory justification for overrides.
* **Operations:** check-in/out with before/after condition, return confirmation, no-show release, overdue detection, reminders.
* **Accountability:** incidents (damage, delay, conflict, safety, misuse), comments, disputes, human-decided penalties, hash-chained audit log, JSON record export.
* **Privacy & security:** hashed passwords, CSRF tokens, secure headers, least-privilege views (managers see masked ID, never email), restricted document access, 2 MB upload cap with type allow-list.

## Files

| Path | Purpose |
|---|---|
| `core.py` | rules engine: eligibility, fairness, lifecycle, incidents, audit, sweep |
| `app.py` | Flask routes & permissions |
| `seed.py` | fictional institution, users, resources, history |
| `templates/`, `static/` | UI |
| `tests/` | end-to-end and crawl tests |
| `docs/PROJECT_DOCUMENT.md` | submission document |
| `docs/DEMO_SCRIPT.md` | 4-minute walkthrough / pitch outline |

## Demo clock

Real systems use the real clock. For judging, **Demo tools** (bottom-right) can move the app clock forward so check-in, overdue and no-show behaviour is visible without waiting. It is logged in the audit trail and disabled with `DEMO_MODE=0`. Admin → **Reset demo data** restores the seed.

## Disclosure

Built with Flask, Werkzeug, Jinja2 and SQLite (standard libraries). No templates or pre-existing application code. AI assistance (Claude) was used for code generation and drafting documents; the team reviewed, tested and is responsible for the result. See `docs/PROJECT_DOCUMENT.md` §13.
