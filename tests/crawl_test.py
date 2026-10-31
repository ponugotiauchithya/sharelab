"""Smoke test: every page renders (no 500s) for every demo role."""
import os, re, sys, tempfile
os.environ["SHARELAB_NO_AUTOCREATE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app as appmod
application = appmod.create_app(os.path.join(tempfile.mkdtemp(), "c.db"))
bad = 0
pages = ["/", "/login", "/register", "/about", "/resources", "/resources?q=cut+acrylic", "/resources?category=Spaces", "/resources?date=2030-01-07&time=10:00&hours=2"]
pages += [f"/resource/{i}" for i in range(1, 8)] + ["/dashboard", "/manage", "/credentials", "/credentials/review", "/incidents", "/audit", "/notifications", "/admin", "/resources/new"]
pages += [f"/booking/{i}" for i in range(1, 12)] + [f"/incident/{i}" for i in range(1, 4)] + ["/resource/2/edit"]
for email in ["admin@dit.demo", "owner@dit.demo", "owner2@dit.demo", "manager@dit.demo", "manager2@dit.demo", "student@dit.demo", "student2@dit.demo", "student3@dit.demo", "newstudent@dit.demo", "unverified@dit.demo", None]:
    c = application.test_client()
    if email:
        t = re.search(r'name="_csrf" value="(\w+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data=dict(email=email, password="Demo@123", _csrf=t))
    for p in pages:
        r = c.get(p, follow_redirects=True)
        if r.status_code >= 500:
            bad += 1; print("500", email, p)
print("pages with server errors:", bad)
sys.exit(1 if bad else 0)
