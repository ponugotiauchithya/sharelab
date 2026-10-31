# Demo script / pitch outline (≈4 minutes)

**Hook (20 s).** "A laser cutter sits idle every evening while a student two buildings away can't afford one. Booking calendars show *when* — they don't answer *who may use it, who is responsible, and was it fair*. ShareLab does."

## Live walkthrough (all in the product, no slides)

| Time | Screen | Say / do |
|---|---|---|
| 0:20 | Login page | One-click demo accounts. "Four roles: user, owner, manager, admin." |
| 0:40 | **Owner** `owner2@dit.demo` → *List a resource* | "Owner defines conditions: opening hours, required training, supervision, caps, auto vs manual approval." Save "Soldering Station" (requires Electronics Bench Training). |
| 1:10 | **Diya** `newstudent@dit.demo` → search "solder circuit" | Plain-language matching. Open resource: "Can I use this?" shows she is missing the qualification. |
| 1:30 | Diya → *My credentials* | Submit credential. Switch to owner → *Verify credentials* → approve. |
| 1:50 | Diya → resource → **dry run** | "Before asking, she sees every rule pass or fail." Submit request → *Awaiting review*. |
| 2:10 | Owner → *Review queue* | Checks, priority score, "Why this score?" → **Approve**. "Rejections and waitlisting require a reason." |
| 2:30 | Diya → booking → **check in** (demo clock) | Condition + rule acknowledgement. Then **return** with slightly worse condition. |
| 3:00 | Owner → confirm return; open auto-raised **incident** | "System flags, human decides. No automatic penalties." Resolve with reasoning; Diya can dispute. |
| 3:20 | Booking page → timeline | "Both parties see the full history. Download JSON. Admin sees the hash-chain integrity check." |
| 3:40 | **Manager** `manager@dit.demo` → seeded Laser Cutter conflict | Two students, one slot: show scores, approve the lower one → justification required; approve the top one → other auto-waitlisted *with explanation*; cancel → promoted. |

## Close (20 s)

"Owners stay in control, users always know *why*, and institutions get accountability. Rule-based and explainable today; AI can later recommend, but a person always decides."

## Judging-criteria map

| Criterion | Where to look |
|---|---|
| Problem understanding | Specific college use case; trust/safety/fairness/accountability each have a feature |
| Core solution & end-to-end | Journey above; `tests/e2e_test.py` proves it |
| Technical depth | Rules engine separated from UI, hash-chained audit, sweep for no-show/overdue, 44 automated checks |
| Trust, safety, accountability | Verification, supervision flag, condition reports, incidents, disputes, human penalties |
| Fair allocation | Caps, waitlist, explainable score, mandatory override justification, promotion |
| Originality | Dry-run eligibility, explainable priority, incident-not-penalty design, audit chain |
