import sqlite3, os, uuid, re, json, hmac, hashlib, base64, urllib.request
from datetime import date
from markupsafe import Markup
from flask import send_from_directory, Flask, g, request, redirect, session, render_template, flash, abort
from werkzeug.security import generate_password_hash as gph, check_password_hash as cph

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
BASE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(BASE, "uploads"); os.makedirs(UP, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024
EXT = {"photo": {"jpg","jpeg","png","webp"}, "doc": {"pdf","jpg","jpeg","png"}}
MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"%PDF": "pdf", b"RIFF": "webp"}
def save_upload(f, kind):
    """Validate extension + file signature, store under a random name. Returns filename or None."""
    if not f or not f.filename: return None
    ext = f.filename.rsplit(".",1)[-1].lower()
    head = f.read(4); f.seek(0)
    if ext not in EXT[kind] or MAGIC.get(head[:4] if head[:4] in MAGIC else head[:3]) is None: return "bad"
    name = f"{kind}_{uuid.uuid4().hex}.{ext}"
    f.save(os.path.join(UP, name)); return name
DB = os.path.join(os.path.dirname(__file__), "sportsconnect.db")
SPORTS = ["Badminton","Cricket","Football","Basketball","Tennis","Table Tennis","Chess","Swimming","Athletics","Volleyball","Boxing","Martial Arts","Gymnastics","Hockey"]

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB); g.db.row_factory = sqlite3.Row
    return g.db
@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()
def q(sql, a=(), one=False):
    r = db().execute(sql, a).fetchall(); return (r[0] if r else None) if one else r
def x(sql, a=()):
    c = db().execute(sql, a); db().commit(); return c.lastrowid

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name,email UNIQUE,pw,role);
CREATE TABLE IF NOT EXISTS academies(id INTEGER PRIMARY KEY,name,city,sports);
CREATE TABLE IF NOT EXISTS coaches(id INTEGER PRIMARY KEY,user_id,sport,city,locality,exp INT,fee INT,bio,qual,academy_id,status DEFAULT 'pending',photo,doc);
CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY,coach_id,days,time,seats INT);
CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY,student_id,coach_id,child,batch_id,status DEFAULT 'requested',created);
CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY,trial_id,day,present INT);
CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY,trial_id,note,day);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY,coach_id,student_id,rating INT,text,UNIQUE(coach_id,student_id));
CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,student_id,coach_id,batch_id,child,sessions INT,amount INT,method,status,order_id,payment_id,created);
CREATE TABLE IF NOT EXISTS inquiries(id INTEGER PRIMARY KEY,coach_id,student_id,message,created);
"""
def init():
    c = sqlite3.connect(DB); c.executescript(SCHEMA)
    for col in ("photo","doc","phone","show_phone"):
        try: c.execute(f"alter table coaches add column {col}")
        except sqlite3.OperationalError: pass
    if not c.execute("select 1 from users where role='admin'").fetchone():
        email = os.environ.get("ADMIN_EMAIL", "admin@gmail.com"); pw = os.environ.get("ADMIN_PASSWORD", "admin123"); gen = False
        if gen: pw = uuid.uuid4().hex[:12]
        c.execute("insert into users(name,email,pw,role) values('Admin',?,?, 'admin')", (email, gph(pw)))
        if gen: print(f"\n*** First run: admin login -> {email} / {pw}  (set ADMIN_EMAIL / ADMIN_PASSWORD env to choose your own) ***\n")
    c.commit(); c.close()

def me():
    return q("select * from users where id=?", (session["uid"],), True) if "uid" in session else None
def need(*roles):
    u = me()
    if not u or u["role"] not in roles: abort(403)
    return u
def coach_of(u): return q("select c.*, u.name from coaches c join users u on u.id=c.user_id where c.user_id=?", (u["id"],), True)

COACH_Q = """select c.*, u.name, a.name academy, (select round(avg(rating),1) from reviews where coach_id=c.id) rating,
 (select count(*) from reviews where coach_id=c.id) nrev,
 (select count(*) from trials where coach_id=c.id and status='accepted') students
 from coaches c join users u on u.id=c.user_id left join academies a on a.id=c.academy_id"""

@app.route("/")
def home():
    top = q(COACH_Q + " where c.status='approved' order by rating desc limit 3")
    return render_template("home.html", top=top, sports=SPORTS)

@app.route("/coaches")
def coaches():
    sport, city = request.args.get("sport",""), request.args.get("city","").strip()
    maxfee, minr = request.args.get("maxfee",""), request.args.get("minrating","")
    sort = request.args.get("sort","rating")
    sql, a = COACH_Q + " where c.status='approved'", []
    if sport: sql += " and c.sport=?"; a.append(sport)
    if city: sql += " and (c.city like ? or c.locality like ?)"; a += [f"%{city}%"]*2
    if maxfee.isdigit(): sql += " and c.fee<=?"; a.append(int(maxfee))
    if minr: sql += " and coalesce((select avg(rating) from reviews where coach_id=c.id),0)>=?"; a.append(float(minr))
    if request.args.get("minexp","").isdigit(): sql += " and c.exp>=?"; a.append(int(request.args["minexp"]))
    sql += {"fee":" order by c.fee","exp":" order by c.exp desc"}.get(sort," order by rating desc")
    return render_template("coaches.html",
    top=q(sql,a), sports=SPORTS, r=request.args)

@app.route("/coach/<int:cid>", methods=["GET"])
def coach(cid):
    c = q(COACH_Q + " where c.id=? and c.status='approved'", (cid,), True) or abort(404)
    u = me(); eligible = None
    if u and u["role"]=="student":
        eligible = q("select 1 from trials where student_id=? and coach_id=? and status in ('accepted','completed')", (u["id"],cid), True)
    return render_template("coach.html",
    c=c, batches=q("select * from batches where coach_id=?", (cid,)), revs=q("select * from reviews where coach_id=?", (cid,)), user=u, eligible=eligible)

@app.route("/uploads/photo/<name>")
def photo_file(name):
    if not name.startswith("photo_"): abort(404)
    return send_from_directory(UP, name)
@app.route("/uploads/doc/<name>")
def doc_file(name):
    u = me() or abort(403)
    if not name.startswith("doc_"): abort(404)
    c = q("select * from coaches where doc=?", (name,), True) or abort(404)
    if u["role"]!="admin" and c["user_id"]!=u["id"]: abort(403)
    return send_from_directory(UP, name)
@app.post("/coach/upload")
def coach_upload():
    c = coach_of(need("coach"))
    for k in ("photo","doc"):
        n = save_upload(request.files.get(k), k)
        if n=="bad": flash("Invalid file. Allowed: JPG/PNG/WebP photo, PDF/JPG/PNG document, max 6 MB."); return redirect("/dashboard")
        if n:
            for old in [c[k]] if c[k] else []:
                try: os.remove(os.path.join(UP, old))
                except OSError: pass
            x(f"update coaches set {k}=? where id=?", (n, c["id"]))
    flash("Files updated. Document changes need admin re-verification."); return redirect("/dashboard")
@app.errorhandler(413)
def too_big(e):
    flash("File too large (max 6 MB)."); return redirect(request.referrer or "/")

@app.post("/trial/<int:cid>")
def trial(cid):
    u = need("student")
    x("insert into trials(student_id,coach_id,child,batch_id,created) values(?,?,?,?,?)", (u["id"],cid,request.form["child"].strip(),request.form["batch"],str(date.today())))
    flash("Trial requested! The coach will respond soon."); return redirect("/dashboard")

@app.post("/review/<int:cid>")
def review(cid):
    u = need("student")
    if not q("select 1 from trials where student_id=? and coach_id=? and status in ('accepted','completed')", (u["id"],cid), True): abort(403)
    try: x("insert into reviews(coach_id,student_id,rating,text) values(?,?,?,?)", (cid,u["id"],int(request.form["rating"]),request.form["text"]))
    except sqlite3.IntegrityError: flash("You already reviewed this coach.")
    return redirect(f"/coach/{cid}")

@app.route("/register", methods=["GET","POST"])
def register():
    role = request.values.get("role","student")
    if role not in ("student","coach"): role="student"
    if request.method=="POST":
        f = request.form
        if len(f["password"])<6: flash("Password must be 6+ characters."); return redirect(f"/register?role={role}")
        ph = f.get("phone","").strip()
        if ph and not re.fullmatch(r"[0-9+\- ]{7,15}", ph): flash("Enter a valid phone number."); return redirect(f"/register?role={role}")
        files = {k: save_upload(request.files.get(k), k) for k in ("photo","doc")}
        if "bad" in files.values(): flash("Only JPG/PNG/WebP photos and PDF/JPG/PNG documents (max 6 MB) are allowed."); return redirect(f"/register?role={role}")
        try: uid = x("insert into users(name,email,pw,role) values(?,?,?,?)", (f["name"],f["email"].lower(),gph(f["password"]),role))
        except sqlite3.IntegrityError: flash("Email already registered."); return redirect(f"/register?role={role}")
        if role=="coach":
            x("insert into coaches(user_id,sport,city,locality,exp,fee,bio,qual) values(?,?,?,?,?,?,?,?)",
              (uid,f["sport"],f["city"],f["locality"],int(f["exp"] or 0),int(f["fee"] or 0),f["bio"],f["qual"]))
            x("update coaches set photo=?, doc=?, phone=?, show_phone=? where user_id=?", (files["photo"], files["doc"], ph, 1 if f.get("show_phone") else 0, uid))
            x("insert into batches(coach_id,days,time,seats) values((select id from coaches where user_id=?),?,?,10)", (uid,f["days"],f["time"]))
            flash("Registered! Admin will verify your profile before it goes live.")
        session["uid"]=uid; return redirect("/dashboard")
    return render_template("register.html", role=role, sports=SPORTS)

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        u = q("select * from users where email=?", (request.form["email"].lower(),), True)
        if u and cph(u["pw"], request.form["password"]): session["uid"]=u["id"]; return redirect("/dashboard")
        flash("Wrong email or password.")
    return render_template("login.html")
@app.route("/logout")
def logout(): session.clear(); return redirect("/")

@app.route("/dashboard")
def dashboard():
    u = me() or abort(403)
    if u["role"]=="admin":
        return render_template("dash_admin.html",
        p=q(COACH_Q+" where c.status='pending'"), s=q("select count(*) n from users where role='student'",one=True)["n"],
        co=q("select count(*) n from coaches where status='approved'",one=True)["n"], t=q("select count(*) n from trials",one=True)["n"])
    if u["role"]=="coach":
        c = coach_of(u)
        return render_template("dash_coach.html",
        c=c, ts=trials_for("t.coach_id=?", c["id"]), notes=notes_map())
    return render_template("dash_student.html",
    ts=trials_for("t.student_id=?", u["id"]), notes=notes_map(), att=att_map())

def trials_for(where, v):
    return q(f"select t.*, s.name sname, cu.name cname from trials t join users s on s.id=t.student_id join coaches c on c.id=t.coach_id join users cu on cu.id=c.user_id where {where} order by t.id desc", (v,))
def notes_map():
    m = {}
    for n in q("select * from notes order by id"): m.setdefault(n["trial_id"],[]).append(n)
    return type("D",(dict,),{"__missing__":lambda s,k:[]})(m)
def att_map():
    m = {}
    for r in q("select trial_id, count(*) n, sum(present) p from attendance group by trial_id"): m[r["trial_id"]]=r
    return type("D",(dict,),{"__missing__":lambda s,k:{"n":0,"p":0}})(m)

def own_trial(tid):
    u = need("coach"); t = q("select * from trials where id=? and coach_id=?", (tid,coach_of(u)["id"]), True)
    return t or abort(403)
@app.post("/trial/<int:tid>/<st>")
def trial_status(tid, st):
    own_trial(tid)
    if st not in ("accepted","rejected","completed"): abort(400)
    x("update trials set status=? where id=?", (st,tid)); return redirect("/dashboard")
@app.post("/attendance/<int:tid>")
def attendance(tid):
    own_trial(tid); x("insert into attendance(trial_id,day,present) values(?,?,?)", (tid,str(date.today()),int(request.form["present"]))); return redirect("/dashboard")
@app.post("/note/<int:tid>")
def note(tid):
    own_trial(tid); x("insert into notes(trial_id,note,day) values(?,?,?)", (tid,request.form["note"],str(date.today()))); return redirect("/dashboard")
@app.post("/admin/<int:cid>/<st>")
def admin(cid, st):
    need("admin")
    if st not in ("approved","rejected"): abort(400)
    x("update coaches set status=? where id=?", (st,cid)); return redirect("/dashboard")



_P={"pin":'<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0116 0z"/><circle cx="12" cy="10" r="3"/>',
"award":'<circle cx="12" cy="8" r="6"/><path d="M15.5 13.5 17 22l-5-3-5 3 1.5-8.5"/>',
"star":'<path d="m12 2 3.1 6.3 6.9 1-5 4.8 1.2 6.9-6.2-3.2-6.2 3.2L7 14.1 2 9.3l6.9-1z"/>',
"shield":'<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
"calendar":'<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
"file":'<path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><path d="M14 2v6h6"/>',
"note":'<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/>',
"sliders":'<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
"trophy":'<path d="M6 9H4.5a2.5 2.5 0 010-5H6M18 9h1.5a2.5 2.5 0 000-5H18M4 22h16M10 14.7V17c0 .6-.5 1-1.2 1.2C7.8 18.8 7 20.2 7 22M14 14.7V17c0 .6.5 1 1.2 1.2 1 .6 1.8 2 1.8 3.8M18 2H6v7a6 6 0 0012 0z"/>',
"ticket":'<path d="M2 9a3 3 0 010 6v2a2 2 0 002 2h16a2 2 0 002-2v-2a3 3 0 010-6V7a2 2 0 00-2-2H4a2 2 0 00-2 2z"/><path d="M13 5v2M13 17v2M13 11v2"/>',
"users":'<path d="M16 21v-2a4 4 0 00-4-4H6a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 00-3-3.9M16 3.1a4 4 0 010 7.8"/>',
"search":'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
"activity":'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
"x":'<path d="M18 6 6 18M6 6l12 12"/>',"check":'<path d="M20 6 9 17l-5-5"/>',
"rupee":'<path d="M6 3h12M6 8h12M6 13l8.5 8M6 13h3a4 4 0 000-8"/>'}

_P.update({
"s-football":'<circle cx="12" cy="12" r="10"/><path d="m12 7 4 3-1.5 5h-5L8 10z"/><path d="M12 7V2M16 10l5-1M14.5 15l3 4M9.5 15l-3 4M8 10 3 9"/>',
"s-basketball":'<circle cx="12" cy="12" r="10"/><path d="M12 2v20M2 12h20M5 4.5c4 4 4 11 0 15M19 4.5c-4 4-4 11 0 15"/>',
"s-tennis":'<circle cx="12" cy="12" r="10"/><path d="M5 4.5c5 4 5 11 0 15M19 4.5c-5 4-5 11 0 15"/>',
"s-volleyball":'<circle cx="12" cy="12" r="10"/><path d="M12 2c0 6-3 9-9 10M12 12c3-1 7 0 9 5M12 12c-1 4-4 7-7 8"/>',
"s-cricket":'<path d="m14 3 7 7-9 9-4-1-1-4z"/><path d="M4 21l3-3"/><circle cx="19" cy="19" r="2"/>',
"s-badminton":'<path d="M8 3h8M9 3l1.5 8h3L15 3M10.5 11l-.5 4h4l-.5-4"/><circle cx="12" cy="19" r="2.5"/>',
"s-table-tennis":'<circle cx="10" cy="10" r="7"/><path d="m15 15 5 5"/><circle cx="19" cy="5" r="1.5"/>',
"s-chess":'<circle cx="12" cy="6" r="3"/><path d="M9 12h6M10.5 12c-1 3-1.5 5-1.5 9M13.5 12c1 3 1.5 5 1.5 9M8 21h8"/>',
"s-swimming":'<circle cx="16" cy="5" r="2"/><path d="M2 20c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2 2-2 4-2M2 14c2 0 2-2 4-2s2 2 4 2 2-2 4-2 2 2 4 2 2-2 4-2"/>',
"s-athletics":'<circle cx="12" cy="14" r="8"/><path d="M12 14V9M9 2h6M12 2v4"/>',
"s-boxing":'<path d="M7 10V6a4 4 0 018 0v4h2a3 3 0 013 3v3a5 5 0 01-5 5H9a3 3 0 01-3-3z"/><path d="M6 18h12"/>',
"s-martial-arts":'<path d="M3 9h18v5H3z"/><path d="m12 14-3 7M12 14l3 7"/>',
"s-gymnastics":'<circle cx="7" cy="14" r="4"/><circle cx="17" cy="14" r="4"/><path d="M7 10V2M17 10V2"/>',
"s-hockey":'<path d="M5 3l6 14c.5 1.5 2 2 4 2h3"/><ellipse cx="19" cy="20" rx="2.5" ry="1.2"/>'})
def icon(n, size=16):
    return Markup(f'<svg class="i" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{_P.get(n, _P['trophy'])}</svg>')
app.jinja_env.globals["i"] = icon
app.jinja_env.globals["img_exists"] = lambda p: os.path.exists(os.path.join(BASE, "static", p))
RZP_ID, RZP_SECRET = os.environ.get("RAZORPAY_KEY_ID"), os.environ.get("RAZORPAY_KEY_SECRET")
def rzp_on(): return bool(RZP_ID and RZP_SECRET)
app.jinja_env.globals["rzp_on"] = rzp_on
def rzp_order(amount, receipt):
    req = urllib.request.Request("https://api.razorpay.com/v1/orders", data=json.dumps({"amount": amount*100, "currency": "INR", "receipt": receipt}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(f"{RZP_ID}:{RZP_SECRET}".encode()).decode()})
    return json.load(urllib.request.urlopen(req, timeout=15))["id"]
def home_extra():
    n = lambda sql: q(sql, one=True)["n"]
    return {"coaches": n("select count(*) n from coaches where status='approved'"), "students": n("select count(*) n from users where role='student'"),
            "reviews": n("select count(*) n from reviews"), "avg": q("select round(avg(rating),1) a from reviews", one=True)["a"],
            "quotes": q("select r.rating, r.text, c.id cid, u.name cname, c.sport from reviews r join coaches c on c.id=r.coach_id join users u on u.id=c.user_id where c.status='approved' order by r.id desc limit 3")}

ICON = {"Cricket":"🏏","Football":"⚽","Badminton":"🏸","Basketball":"🏀","Tennis":"🎾","Table Tennis":"🏓","Chess":"♟️","Swimming":"🏊","Athletics":"🏃","Volleyball":"🏐","Boxing":"🥊","Martial Arts":"🥋","Gymnastics":"🤸","Hockey":"🏑"}
def student_extra(uid):
    return {"total": q("select count(*) n from attendance a join trials t on t.id=a.trial_id where t.student_id=?", (uid,), True)["n"], "attended": q("select coalesce(sum(a.present),0) n from attendance a join trials t on t.id=a.trial_id where t.student_id=?", (uid,), True)["n"],
            "bookings": q("select b.*, u.name cname from bookings b join coaches c on c.id=b.coach_id join users u on u.id=c.user_id where b.student_id=? order by b.id desc", (uid,)),
            "reviews": q("select r.*, u.name cname from reviews r join coaches c on c.id=r.coach_id join users u on u.id=c.user_id where r.student_id=?", (uid,))}
def coach_extra(c):
    revs = q("select * from reviews where coach_id=?", (c["id"],))
    chk = {"photo": c["photo"], "certificate": c["doc"], "bio": c["bio"], "qualifications": c["qual"], "fee": c["fee"]}
    bk = q("select b.*, u.name sname from bookings b join users u on u.id=b.student_id where b.coach_id=? order by b.id desc", (c["id"],))
    return {"bookings": bk, "earned": sum(b["amount"] for b in bk if b["status"]=="paid"),
            "inquiries": q("select i.*, u.name sname from inquiries i join users u on u.id=i.student_id where i.coach_id=? order by i.id desc limit 20", (c["id"],)),
            "batches": q("select * from batches where coach_id=?", (c["id"],)), "reviews": revs,
            "avg": round(sum(r["rating"] for r in revs)/len(revs),1) if revs else None,
            "pct": int(100*sum(1 for v in chk.values() if v)/len(chk)), "missing": [k for k,v in chk.items() if not v]}
def admin_extra():
    n = lambda sql: q(sql, one=True)["n"]
    by = q("select sport, count(*) n from coaches where status='approved' group by sport order by n desc")
    return {"users": n("select count(*) n from users"), "students": n("select count(*) n from users where role='student'"),
            "coaches": n("select count(*) n from coaches"), "pending": n("select count(*) n from coaches where status='pending'"),
            "approved": n("select count(*) n from coaches where status='approved'"), "trials": n("select count(*) n from trials"),
            "by_sport": by, "max_sport": max([r["n"] for r in by] or [1]), "academies": q("select * from academies"),
            "recent": q("select name,email,role from users order by id desc limit 8"), "users_all": q("select id,name,email,role from users order by id desc limit 50"),
            "tstat": q("select status, count(*) n from trials group by status"), "active_acad": n("select count(*) n from academies"), "bookings": n("select count(*) n from bookings")}
@app.context_processor
def ctx():
    return dict(student_extra=student_extra, coach_extra=coach_extra, admin_extra=admin_extra, ICON=ICON, session_user=me, home_extra=home_extra)

@app.post("/account")
def account():
    u = need("student"); n = request.form["name"].strip()
    if n: x("update users set name=? where id=?", (n, u["id"])); flash("Account updated.")
    return redirect("/dashboard")
@app.post("/coach/edit")
def coach_edit():
    c = coach_of(need("coach")); f = request.form
    ph = f.get("phone","").strip()
    if ph and not re.fullmatch(r"[0-9+\- ]{7,15}", ph): flash("Enter a valid phone number."); return redirect("/dashboard")
    x("update coaches set qual=?, fee=?, exp=?, bio=?, phone=?, show_phone=? where id=?", (f["qual"], int(f["fee"] or 0), int(f["exp"] or 0), f["bio"], ph, 1 if f.get("show_phone") else 0, c["id"]))
    flash("Profile saved."); return redirect("/dashboard")
@app.post("/coach/batch")
def coach_batch():
    c = coach_of(need("coach")); f = request.form
    x("insert into batches(coach_id,days,time,seats) values(?,?,?,?)", (c["id"], f["days"], f["time"], int(f["seats"] or 10)))
    flash("Batch added."); return redirect("/dashboard")
@app.post("/coach/batch/<int:bid>")
def batch_edit(bid):
    c = coach_of(need("coach")); f = request.form
    if not q("select 1 from batches where id=? and coach_id=?", (bid, c["id"]), True): abort(403)
    x("update batches set days=?, time=?, seats=? where id=?", (f["days"], f["time"], max(0,int(f["seats"] or 0)), bid)); flash("Batch updated."); return redirect("/dashboard")

@app.post("/enquiry/<int:cid>")
def enquiry(cid):
    u = need("student"); m = request.form["message"].strip()[:1000]
    if not q("select 1 from coaches where id=? and status='approved'", (cid,), True): abort(404)
    if not m: flash("Write a message first."); return redirect(f"/coach/{cid}")
    x("insert into inquiries(coach_id,student_id,message,created) values(?,?,?,?)", (cid, u["id"], m, str(date.today())))
    flash("Enquiry sent to the coach."); return redirect(f"/coach/{cid}")

@app.post("/book/<int:cid>")
def book(cid):
    u = need("student"); f = request.form
    co = q("select * from coaches where id=? and status='approved'", (cid,), True) or abort(404)
    b = q("select * from batches where id=? and coach_id=?", (f.get("batch"), cid), True) or abort(400)
    n = int(f.get("sessions", 0)) if f.get("sessions", "").isdigit() else 0
    method = f.get("method")
    if n not in (1, 4, 8, 12) or method not in ("online", "offline") or not f.get("child", "").strip(): abort(400)
    if b["seats"] <= 0: flash("This batch is full."); return redirect(f"/coach/{cid}")
    if method == "online" and not rzp_on(): flash("Online payment isn't enabled yet. Choose pay-at-session."); return redirect(f"/coach/{cid}")
    bid = x("insert into bookings(student_id,coach_id,batch_id,child,sessions,amount,method,status,created) values(?,?,?,?,?,?,?,?,?)",
            (u["id"], cid, b["id"], f["child"].strip(), n, co["fee"]*n, method, "created" if method == "online" else "pay_at_session", str(date.today())))
    if method == "online": return redirect(f"/pay/{bid}")
    flash("Booking confirmed. Pay the coach at your first session."); return redirect("/dashboard")

@app.route("/pay/<int:bid>")
def pay(bid):
    u = need("student"); b = q("select * from bookings where id=? and student_id=?", (bid, u["id"]), True) or abort(404)
    if b["status"] != "created" or b["method"] != "online" or not rzp_on(): return redirect("/dashboard")
    oid = b["order_id"]
    if not oid:
        try: oid = rzp_order(b["amount"], f"bk{bid}")
        except Exception: flash("Could not start the payment. Please try again."); return redirect("/dashboard")
        x("update bookings set order_id=? where id=?", (oid, bid))
    return render_template("pay.html", b=b, oid=oid, key=RZP_ID, user=u)

@app.post("/pay/verify")
def pay_verify():
    u = need("student"); f = request.form
    b = q("select * from bookings where order_id=? and student_id=?", (f.get("razorpay_order_id", ""), u["id"]), True) or abort(404)
    want = hmac.new(RZP_SECRET.encode(), f"{b['order_id']}|{f.get('razorpay_payment_id','')}".encode(), hashlib.sha256).hexdigest()
    if hmac.compare_digest(want, f.get("razorpay_signature", "")):
        x("update bookings set status='paid', payment_id=? where id=?", (f["razorpay_payment_id"], b["id"])); flash("Payment successful. Booking confirmed.")
    else: flash("Payment could not be verified. If money was deducted, contact support.")
    return redirect("/dashboard")

@app.post("/booking/<int:bid>/<act>")
def booking_act(bid, act):
    u = me() or abort(403)
    b = q("select * from bookings where id=?", (bid,), True) or abort(404)
    if act == "paid" and u["role"] == "coach" and coach_of(u)["id"] == b["coach_id"] and b["status"] == "pay_at_session":
        x("update bookings set status='paid' where id=?", (bid,)); flash("Marked as paid.")
    elif act == "cancel" and u["role"] == "student" and b["student_id"] == u["id"] and b["status"] in ("created", "pay_at_session"):
        x("update bookings set status='cancelled' where id=?", (bid,)); flash("Booking cancelled.")
    else: abort(403)
    return redirect("/dashboard")

@app.post("/admin/academy")
def add_academy():
    need("admin"); f = request.form
    x("insert into academies(name,city,sports) values(?,?,?)", (f["name"], f["city"], f["sports"])); flash("Academy added."); return redirect("/dashboard")

init()
if __name__ == "__main__": app.run(debug=True)