import os, sqlite3, secrets, shutil, csv, io
from datetime import date, timedelta, datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort, Response
from werkzeug.security import generate_password_hash, check_password_hash

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "library.sqlite3")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("LIBRARY_SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

def connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 15000")
    return con

def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, full_name TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','librarian','viewer')),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS members(
          id INTEGER PRIMARY KEY, member_code TEXT UNIQUE NOT NULL,
          full_name TEXT NOT NULL, member_type TEXT NOT NULL CHECK(member_type IN ('Student','Teacher','Staff')),
          class_or_department TEXT DEFAULT '', email TEXT DEFAULT '', phone TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS books(
          id INTEGER PRIMARY KEY, isbn TEXT DEFAULT '', title TEXT NOT NULL,
          author TEXT DEFAULT '', category TEXT DEFAULT '', publisher TEXT DEFAULT '',
          shelf TEXT DEFAULT '', published_year INTEGER, total_copies INTEGER NOT NULL DEFAULT 1 CHECK(total_copies > 0),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS loans(
          id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL REFERENCES books(id),
          member_id INTEGER NOT NULL REFERENCES members(id), quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity > 0),
          borrowed_on TEXT NOT NULL, due_on TEXT NOT NULL, returned_on TEXT,
          status TEXT NOT NULL DEFAULT 'Borrowed' CHECK(status IN ('Borrowed','Returned')),
          fine_amount REAL NOT NULL DEFAULT 0, notes TEXT DEFAULT '',
          created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL,
          entity TEXT NOT NULL, entity_id INTEGER, details TEXT DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_loans_status_due ON loans(status,due_on);
        CREATE INDEX IF NOT EXISTS idx_loans_member ON loans(member_id);
        CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
        """)
        if not db.execute("SELECT id FROM users LIMIT 1").fetchone():
            db.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
                       ("admin", generate_password_hash("ChangeMe123!"), "System Administrator", "admin"))
        if not db.execute("SELECT id FROM books LIMIT 1").fetchone():
            db.executemany("INSERT INTO books(isbn,title,author,category,shelf,total_copies) VALUES(?,?,?,?,?,?)", [
                ("9780000000011","Introduction to Biology","A. N. Author","Science","SCI-A1",5),
                ("9780000000028","Mathematics for Schools","J. Writer","Mathematics","MAT-B2",8),
                ("9780000000035","English Language Skills","M. Editor","Languages","LAN-C1",4)])
        if not db.execute("SELECT id FROM members LIMIT 1").fetchone():
            db.executemany("INSERT INTO members(member_code,full_name,member_type,class_or_department) VALUES(?,?,?,?)", [
                ("STU-0001","Sample Student","Student","S4"),
                ("TCH-0001","Sample Teacher","Teacher","Languages")])

def current_user():
    if "user_id" not in session: return None
    with connect() as db:
        return db.execute("SELECT * FROM users WHERE id=? AND active=1", (session["user_id"],)).fetchone()

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapped

def roles(*allowed):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            u=current_user()
            if not u: return redirect(url_for("login"))
            if u["role"] not in allowed:
                flash("Your account does not have permission to do that.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return wrapped
    return decorator

def audit(db, action, entity, entity_id=None, details=""):
    db.execute("INSERT INTO audit_log(user_id,action,entity,entity_id,details) VALUES(?,?,?,?,?)",
               (session.get("user_id"), action, entity, entity_id, details[:500]))

@app.context_processor
def inject_globals():
    return {"user": current_user(), "today": date.today().isoformat(), "default_due": (date.today()+timedelta(days=14)).isoformat()}

@app.route("/")
def home():
    return redirect(url_for("dashboard") if current_user() else url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username=request.form.get("username","").strip()
        password=request.form.get("password","")
        with connect() as db:
            u=db.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if u and check_password_hash(u["password_hash"], password):
            session.clear(); session["user_id"]=u["id"]
            flash("Welcome back, "+u["full_name"]+".", "success")
            return redirect(url_for("dashboard"))
        flash("The username or password was not recognised.", "error")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    with connect() as db:
        stats = {
          "titles": db.execute("SELECT COUNT(*) FROM books WHERE active=1").fetchone()[0],
          "copies": db.execute("SELECT COALESCE(SUM(total_copies),0) FROM books WHERE active=1").fetchone()[0],
          "members": db.execute("SELECT COUNT(*) FROM members WHERE active=1").fetchone()[0],
          "loans": db.execute("SELECT COUNT(*) FROM loans WHERE status='Borrowed'").fetchone()[0],
          "overdue": db.execute("SELECT COUNT(*) FROM loans WHERE status='Borrowed' AND due_on < ?", (date.today().isoformat(),)).fetchone()[0],
          "available": db.execute("""SELECT COALESCE(SUM(b.total_copies),0)-COALESCE((SELECT SUM(l.quantity) FROM loans l WHERE l.book_id=b.id AND l.status='Borrowed'),0) FROM books b WHERE b.active=1""").fetchone()[0],
        }
        recent=db.execute("""SELECT l.*,b.title,m.full_name,m.member_code FROM loans l JOIN books b ON b.id=l.book_id JOIN members m ON m.id=l.member_id ORDER BY l.id DESC LIMIT 8""").fetchall()
        popular=db.execute("""SELECT b.title, COALESCE(SUM(l.quantity),0) n FROM books b LEFT JOIN loans l ON l.book_id=b.id GROUP BY b.id ORDER BY n DESC,b.title LIMIT 5""").fetchall()
    return render_template("dashboard.html", stats=stats, recent=recent, popular=popular)

@app.route("/books", methods=["GET","POST"])
@login_required
def books():
    if request.method == "POST":
        if current_user()["role"] not in ("admin","librarian"): abort(403)
        f=request.form
        try: copies=max(1,int(f.get("total_copies","1")))
        except ValueError: copies=1
        with connect() as db:
            cur=db.execute("""INSERT INTO books(isbn,title,author,category,publisher,shelf,published_year,total_copies)
              VALUES(?,?,?,?,?,?,?,?)""",(f.get("isbn","").strip(),f.get("title","").strip(),f.get("author","").strip(),f.get("category","").strip(),f.get("publisher","").strip(),f.get("shelf","").strip(),int(f["published_year"]) if f.get("published_year","").isdigit() else None,copies))
            audit(db,"CREATE","book",cur.lastrowid,f.get("title",""))
        flash("Book added to the catalogue.", "success"); return redirect(url_for("books"))
    q=request.args.get("q","").strip()
    with connect() as db:
        sql="""SELECT b.*, b.total_copies-COALESCE((SELECT SUM(l.quantity) FROM loans l WHERE l.book_id=b.id AND l.status='Borrowed'),0) available FROM books b WHERE b.active=1"""
        args=[]
        if q: sql+=" AND (b.title LIKE ? OR b.author LIKE ? OR b.isbn LIKE ? OR b.category LIKE ?)"; args=[f"%{q}%"]*4
        rows=db.execute(sql+" ORDER BY b.title",args).fetchall()
    return render_template("books.html", books=rows, q=q)

@app.route("/books/<int:book_id>/edit", methods=["POST"])
@roles("admin","librarian")
def edit_book(book_id):
    f=request.form
    with connect() as db:
        book=db.execute("SELECT * FROM books WHERE id=? AND active=1",(book_id,)).fetchone()
        if not book: abort(404)
        try: copies=max(1,int(f.get("total_copies","1")))
        except ValueError: copies=book["total_copies"]
        active_loans=db.execute("SELECT COALESCE(SUM(quantity),0) FROM loans WHERE book_id=? AND status='Borrowed'",(book_id,)).fetchone()[0]
        if copies < active_loans:
            flash(f"Copies cannot be set below {active_loans}, currently on loan.", "error")
            return redirect(url_for("books"))
        db.execute("""UPDATE books SET isbn=?,title=?,author=?,category=?,publisher=?,shelf=?,total_copies=? WHERE id=?""",
                   (f.get("isbn",""),f.get("title","").strip(),f.get("author",""),f.get("category",""),f.get("publisher",""),f.get("shelf",""),copies,book_id))
        audit(db,"UPDATE","book",book_id,f.get("title",""))
    flash("Book details updated.", "success"); return redirect(url_for("books"))

@app.route("/books/<int:book_id>/archive", methods=["POST"])
@roles("admin","librarian")
def archive_book(book_id):
    with connect() as db:
        active=db.execute("SELECT COUNT(*) FROM loans WHERE book_id=? AND status='Borrowed'",(book_id,)).fetchone()[0]
        if active:
            flash("This title has active loans and cannot be archived yet.", "error")
        else:
            db.execute("UPDATE books SET active=0 WHERE id=?",(book_id,)); audit(db,"ARCHIVE","book",book_id)
            flash("Book archived. Historical loan records are preserved.", "success")
    return redirect(url_for("books"))

@app.route("/members", methods=["GET","POST"])
@login_required
def members():
    if request.method=="POST":
        if current_user()["role"] not in ("admin","librarian"): abort(403)
        f=request.form
        with connect() as db:
            code=f.get("member_code","").strip().upper()
            if not code:
                prefix={"Student":"STU","Teacher":"TCH","Staff":"STF"}.get(f.get("member_type"),"MEM")
                last=db.execute("SELECT id FROM members ORDER BY id DESC LIMIT 1").fetchone()
                code=f"{prefix}-{(last['id'] if last else 0)+1:04d}"
            try:
                cur=db.execute("""INSERT INTO members(member_code,full_name,member_type,class_or_department,email,phone) VALUES(?,?,?,?,?,?)""",
                  (code,f.get("full_name","").strip(),f.get("member_type","Student"),f.get("class_or_department",""),f.get("email",""),f.get("phone","")))
                audit(db,"CREATE","member",cur.lastrowid,code)
                flash("Member added. Assigned code: "+code,"success")
            except sqlite3.IntegrityError: flash("That member code already exists. Use a different code.","error")
        return redirect(url_for("members"))
    q=request.args.get("q","").strip()
    with connect() as db:
        sql="SELECT * FROM members WHERE active=1"; args=[]
        if q: sql+=" AND (full_name LIKE ? OR member_code LIKE ? OR class_or_department LIKE ?)"; args=[f"%{q}%"]*3
        rows=db.execute(sql+" ORDER BY full_name",args).fetchall()
    return render_template("members.html", members=rows, q=q)

@app.route("/members/<int:member_id>/archive", methods=["POST"])
@roles("admin","librarian")
def archive_member(member_id):
    with connect() as db:
        active=db.execute("SELECT COUNT(*) FROM loans WHERE member_id=? AND status='Borrowed'",(member_id,)).fetchone()[0]
        if active: flash("This member has books checked out. Return them before archiving.", "error")
        else:
            db.execute("UPDATE members SET active=0 WHERE id=?",(member_id,)); audit(db,"ARCHIVE","member",member_id)
            flash("Member archived; loan history retained.","success")
    return redirect(url_for("members"))

@app.route("/loans", methods=["GET","POST"])
@roles("admin","librarian")
def loans():
    if request.method=="POST":
        f=request.form
        try:
            book_id=int(f.get("book_id","0")); member_id=int(f.get("member_id","0")); qty=max(1,int(f.get("quantity","1")))
            borrowed=f.get("borrowed_on") or date.today().isoformat()
            due=f.get("due_on") or (date.today()+timedelta(days=14)).isoformat()
            if due < borrowed: raise ValueError("Due date must not be before the borrowing date.")
        except ValueError as ex:
            flash(str(ex) or "Check the loan form values.","error"); return redirect(url_for("loans"))
        with connect() as db:
            b=db.execute("SELECT * FROM books WHERE id=? AND active=1",(book_id,)).fetchone()
            m=db.execute("SELECT * FROM members WHERE id=? AND active=1",(member_id,)).fetchone()
            onloan=db.execute("SELECT COALESCE(SUM(quantity),0) FROM loans WHERE book_id=? AND status='Borrowed'",(book_id,)).fetchone()[0]
            if not b or not m: flash("Choose an active book and member.","error")
            elif qty > b["total_copies"]-onloan: flash("Not enough copies are available.","error")
            else:
                cur=db.execute("""INSERT INTO loans(book_id,member_id,quantity,borrowed_on,due_on,notes,created_by) VALUES(?,?,?,?,?,?,?)""",
                    (book_id,member_id,qty,borrowed,due,f.get("notes",""),session["user_id"]))
                audit(db,"CHECKOUT","loan",cur.lastrowid,f"{m['member_code']} borrowed {qty} × {b['title']}")
                flash("Checkout saved successfully.","success")
        return redirect(url_for("loans"))
    with connect() as db:
        books=db.execute("""SELECT b.id,b.title,b.author,b.total_copies-COALESCE((SELECT SUM(quantity) FROM loans l WHERE l.book_id=b.id AND l.status='Borrowed'),0) available FROM books b WHERE b.active=1 ORDER BY b.title""").fetchall()
        members=db.execute("SELECT id,member_code,full_name,member_type FROM members WHERE active=1 ORDER BY full_name").fetchall()
        q=request.args.get("q","").strip()
        sql="""SELECT l.*,b.title,m.full_name,m.member_code FROM loans l JOIN books b ON b.id=l.book_id JOIN members m ON m.id=l.member_id WHERE l.status='Borrowed'"""
        args=[]
        if q: sql+=" AND (b.title LIKE ? OR m.full_name LIKE ? OR m.member_code LIKE ?)"; args=[f"%{q}%"]*3
        active=db.execute(sql+" ORDER BY l.due_on",args).fetchall()
    return render_template("loans.html", books=books, members=members, loans=active, q=q)

@app.route("/loans/<int:loan_id>/return", methods=["POST"])
@roles("admin","librarian")
def return_loan(loan_id):
    with connect() as db:
        l=db.execute("SELECT * FROM loans WHERE id=? AND status='Borrowed'",(loan_id,)).fetchone()
        if not l:
            flash("Loan not found or already returned.","error")
        else:
            returned=request.form.get("returned_on") or date.today().isoformat()
            late=max(0,(date.fromisoformat(returned)-date.fromisoformat(l["due_on"])).days)
            fine=late*float(request.form.get("fine_per_day","0"))
            db.execute("UPDATE loans SET status='Returned',returned_on=?,fine_amount=? WHERE id=?",(returned,fine,loan_id))
            audit(db,"RETURN","loan",loan_id,f"returned {returned}; late days {late}; fine {fine:.2f}")
            flash(f"Return recorded. Days late: {late}; fine: {fine:.2f}.","success")
    return redirect(url_for("loans"))

@app.route("/history")
@login_required
def history():
    q=request.args.get("q","").strip()
    with connect() as db:
        sql="""SELECT l.*,b.title,m.full_name,m.member_code FROM loans l JOIN books b ON b.id=l.book_id JOIN members m ON m.id=l.member_id WHERE 1=1"""
        args=[]
        if q: sql+=" AND (b.title LIKE ? OR m.full_name LIKE ? OR m.member_code LIKE ?)"; args=[f"%{q}%"]*3
        rows=db.execute(sql+" ORDER BY l.id DESC LIMIT 500",args).fetchall()
    return render_template("history.html", loans=rows, q=q)

@app.route("/reports")
@login_required
def reports():
    with connect() as db:
        overdue=db.execute("""SELECT l.*,b.title,m.full_name,m.member_code FROM loans l JOIN books b ON b.id=l.book_id JOIN members m ON m.id=l.member_id WHERE l.status='Borrowed' AND l.due_on < ? ORDER BY l.due_on""",(date.today().isoformat(),)).fetchall()
        fines=db.execute("SELECT COALESCE(SUM(fine_amount),0) FROM loans").fetchone()[0]
        by_category=db.execute("""SELECT COALESCE(NULLIF(category,''),'Uncategorised') category, COUNT(*) titles, SUM(total_copies) copies FROM books WHERE active=1 GROUP BY category ORDER BY titles DESC""").fetchall()
        by_member=db.execute("""SELECT m.member_code,m.full_name,COUNT(l.id) loans FROM members m LEFT JOIN loans l ON l.member_id=m.id GROUP BY m.id ORDER BY loans DESC LIMIT 10""").fetchall()
    overdue_rows=[]
    for row in overdue:
        item=dict(row)
        item["days_late"]=(date.today()-date.fromisoformat(row["due_on"])).days
        overdue_rows.append(item)
    return render_template("reports.html", overdue=overdue_rows, fines=fines, by_category=by_category, by_member=by_member)

@app.route("/export/<kind>.csv")
@roles("admin","librarian")
def export_csv(kind):
    queries={
      "books":("SELECT id,isbn,title,author,category,publisher,shelf,total_copies,active,created_at FROM books ORDER BY id",["id","isbn","title","author","category","publisher","shelf","total_copies","active","created_at"]),
      "members":("SELECT id,member_code,full_name,member_type,class_or_department,email,phone,active,created_at FROM members ORDER BY id",["id","member_code","full_name","member_type","class_or_department","email","phone","active","created_at"]),
      "loans":("""SELECT l.id,b.title,m.member_code,m.full_name,l.quantity,l.borrowed_on,l.due_on,l.returned_on,l.status,l.fine_amount,l.notes FROM loans l JOIN books b ON b.id=l.book_id JOIN members m ON m.id=l.member_id ORDER BY l.id""",["id","title","member_code","full_name","quantity","borrowed_on","due_on","returned_on","status","fine_amount","notes"])
    }
    if kind not in queries: abort(404)
    query,headers=queries[kind]
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(headers)
    with connect() as db:
        for row in db.execute(query): writer.writerow([row[h] for h in headers])
    audit_db("EXPORT",kind,details="CSV export")
    return Response("\\ufeff"+out.getvalue(),mimetype="text/csv; charset=utf-8",headers={"Content-Disposition":f"attachment; filename=library_{kind}.csv"})

def audit_db(action,entity,details=""):
    with connect() as db: audit(db,action,entity,None,details)

@app.route("/backup", methods=["GET","POST"])
@roles("admin")
def backup():
    if request.method=="POST":
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        target=os.path.join(BACKUP_DIR,f"library_backup_{stamp}.sqlite3")
        with connect() as db:
            dest=sqlite3.connect(target)
            db.backup(dest)
            dest.close()
            audit(db,"BACKUP","database",None,os.path.basename(target))
        flash("A consistent SQLite backup was created.","success")
        return redirect(url_for("backup"))
    backups=[]
    for name in sorted(os.listdir(BACKUP_DIR),reverse=True):
        path=os.path.join(BACKUP_DIR,name)
        if name.endswith(".sqlite3") and os.path.isfile(path):
            backups.append({"name":name,"size":os.path.getsize(path),"date":datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")})
    return render_template("backup.html", backups=backups)

@app.route("/backup/<path:name>")
@roles("admin")
def download_backup(name):
    if "/" in name or "\\\\" in name or not name.endswith(".sqlite3"): abort(404)
    path=os.path.join(BACKUP_DIR,name)
    if not os.path.isfile(path): abort(404)
    return send_file(path,as_attachment=True,download_name=name)

@app.route("/users", methods=["GET","POST"])
@roles("admin")
def users():
    if request.method=="POST":
        f=request.form; password=f.get("password","")
        if len(password)<10:
            flash("Use a password of at least 10 characters.","error")
        else:
            try:
                with connect() as db:
                    cur=db.execute("INSERT INTO users(username,password_hash,full_name,role) VALUES(?,?,?,?)",
                      (f.get("username","").strip(),generate_password_hash(password),f.get("full_name","").strip(),f.get("role","librarian")))
                    audit(db,"CREATE","user",cur.lastrowid,f.get("username",""))
                flash("User account created.","success")
            except sqlite3.IntegrityError: flash("That username already exists.","error")
        return redirect(url_for("users"))
    with connect() as db: rows=db.execute("SELECT id,username,full_name,role,active,created_at FROM users ORDER BY id").fetchall()
    return render_template("users.html", users=rows)

@app.route("/users/<int:user_id>/toggle", methods=["POST"])
@roles("admin")
def toggle_user(user_id):
    if user_id==session.get("user_id"):
        flash("You cannot deactivate your own signed-in account.","error")
    else:
        with connect() as db:
            db.execute("UPDATE users SET active=1-active WHERE id=?",(user_id,)); audit(db,"TOGGLE","user",user_id)
        flash("User account status updated.","success")
    return redirect(url_for("users"))

@app.route("/account", methods=["GET","POST"])
@login_required
def account():
    if request.method=="POST":
        old=request.form.get("old_password",""); new=request.form.get("new_password","")
        u=current_user()
        if not check_password_hash(u["password_hash"],old): flash("Current password is incorrect.","error")
        elif len(new)<10: flash("New password must be at least 10 characters.","error")
        else:
            with connect() as db:
                db.execute("UPDATE users SET password_hash=? WHERE id=?",(generate_password_hash(new),u["id"]))
                audit(db,"PASSWORD_CHANGE","user",u["id"])
            flash("Password changed.","success")
    return render_template("account.html")

@app.errorhandler(403)
def forbidden(e): return render_template("error.html", code=403, message="You do not have permission to access this page."),403
@app.errorhandler(404)
def not_found(e): return render_template("error.html", code=404, message="The requested record or page was not found."),404

init_db()
if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG")=="1", host="127.0.0.1", port=int(os.environ.get("PORT","5000")))
