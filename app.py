from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
import sqlite3, os
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from math import ceil

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(APP_ROOT, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DATABASE = os.path.join(APP_ROOT, "projects.db")
PER_PAGE = 6

app = Flask(__name__)
app.secret_key = "p_ideanet_final_secret"
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ---------------- DB helpers ----------------
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_tables():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT UNIQUE,
        password_hash TEXT
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        author TEXT NOT NULL,
        description TEXT,
        pdf_filename TEXT,
        category TEXT,
        is_public INTEGER DEFAULT 1,
        owner_id INTEGER,
        views INTEGER DEFAULT 0,
        downloads INTEGER DEFAULT 0,
        FOREIGN KEY(owner_id) REFERENCES users(id)
    )""")
    conn.commit()
    conn.close()

ensure_tables()

# ---------------- Auth ----------------
@app.route("/register", methods=["GET","POST"])
def register():
    if request.method=="POST":
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if not email or not password:
            flash("Email and password required.", "warning")
            return redirect(url_for("register"))
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (name,email,password_hash) VALUES (?,?,?)",
                         (name, email, generate_password_hash(password)))
            conn.commit()
            flash("Registration successful. Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already registered.", "danger")
        finally:
            conn.close()
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"] or user["email"]
            flash(f"Welcome, {session['user_name']}!", "success")
            return redirect(url_for("projects"))
        else:
            flash("Invalid email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("projects"))

# ---------------- Projects list ----------------
@app.route("/")
@app.route("/projects")
def projects():
    q = request.args.get("query","").strip()
    category = request.args.get("category","").strip()
    sort = request.args.get("sort","newest")
    page = int(request.args.get("page", 1) or 1)
    offset = (page-1)*PER_PAGE

    if sort == "views":
        order = "views DESC"
    elif sort == "downloads":
        order = "downloads DESC"
    else:
        order = "id DESC"

    filters = []
    params = []

# Search filter
    if q:
        filters.append("(title LIKE ? OR author LIKE ? OR description LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like, like])

# Category filter
    if category:
        filters.append("category = ?")
        params.append(category)

# Public / private filter
    if session.get("user_id"):
        # logged in: show public + own private projects
        filters.append("(is_public = 1 OR owner_id = ?)")
        params.append(session["user_id"])
    else:
    # not logged in: show only public projects
        filters.append("is_public = 1")


    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    count_sql = f"SELECT COUNT(*) FROM projects {where}"
    select_sql = f"SELECT * FROM projects {where} ORDER BY {order} LIMIT ? OFFSET ?"

    conn = get_db_connection()
    c = conn.cursor()

    total = c.execute(count_sql, params).fetchone()[0]
    projects = c.execute(select_sql, (*params, PER_PAGE, offset)).fetchall()

    # Auto categories
    cats = c.execute("SELECT DISTINCT category FROM projects WHERE category IS NOT NULL AND category != ''").fetchall()
    categories = [row["category"] for row in cats if row["category"]]

    # Featured top 3
    featured = c.execute("SELECT * FROM projects ORDER BY views DESC LIMIT 3").fetchall()
    conn.close()

    total_pages = max(1, ceil(total/PER_PAGE))
    return render_template("projects.html",
                           projects=projects,
                           featured=featured,
                           categories=categories,
                           selected_category=category,
                           query=q,
                           sort=sort,
                           page=page,
                           total_pages=total_pages)

# ---------------- View project ----------------
@app.route("/project/<int:project_id>")
def view_project(project_id):
    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        flash("Project not found.", "warning")
        return redirect(url_for("projects"))
    conn.execute("UPDATE projects SET views = views + 1 WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    return render_template("view_project.html", project=project)

# ---------------- Serve PDF ----------------
# ---------------- Serve PDF ----------------
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # Check if file exists
    if not os.path.exists(file_path):
        flash("PDF file not found!", "danger")
        return redirect(url_for("projects"))

    # Increase download count
    conn = get_db_connection()
    conn.execute("UPDATE projects SET downloads = downloads + 1 WHERE pdf_filename=?", (filename,))
    conn.commit()
    conn.close()

    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------------- Create project ----------------
@app.route("/create", methods=["GET","POST"])
def create():
    if not session.get("user_id"):
        flash("Please log in to upload a project.", "warning")
        return redirect(url_for("login"))

    if request.method=="POST":
        title = request.form.get("title","").strip()
        author = request.form.get("author","").strip()
        description = request.form.get("description","").strip()
        category = request.form.get("category","").strip()
        is_public = 1 if request.form.get("is_public") == "on" else 0

        file = request.files.get("pdf")
        filename = None
        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        conn = get_db_connection()
        conn.execute("""INSERT INTO projects 
                        (title,author,description,pdf_filename,category,is_public,owner_id)
                        VALUES (?,?,?,?,?,?,?)""",
                     (title, author, description, filename, category, is_public, session["user_id"]))
        conn.commit()
        conn.close()
        flash("Project uploaded.", "success")
        return redirect(url_for("projects"))

    conn = get_db_connection()
    cats = conn.execute("SELECT DISTINCT category FROM projects WHERE category IS NOT NULL AND category != ''").fetchall()
    categories = [r["category"] for r in cats if r["category"]]
    conn.close()
    return render_template("create.html", categories=categories)

# ---------------- Edit project ----------------
@app.route("/edit/<int:project_id>", methods=["GET","POST"])
def edit(project_id):
    if not session.get("user_id"):
        flash("Please log in to edit.", "warning")
        return redirect(url_for("login"))

    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        flash("Project not found.", "warning")
        return redirect(url_for("projects"))

    if project["owner_id"] != session["user_id"]:
        conn.close()
        flash("Not authorized.", "danger")
        return redirect(url_for("projects"))

    if request.method=="POST":
        title = request.form.get("title","").strip()
        author = request.form.get("author","").strip()
        description = request.form.get("description","").strip()
        category = request.form.get("category","").strip()
        is_public = 1 if request.form.get("is_public") == "on" else 0

        file = request.files.get("pdf")
        filename = project["pdf_filename"]
        if file and file.filename:
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        conn.execute("""UPDATE projects SET title=?, author=?, description=?, pdf_filename=?, category=?, is_public=? WHERE id=?""",
                     (title, author, description, filename, category, is_public, project_id))
        conn.commit()
        conn.close()
        flash("Project updated.", "success")
        return redirect(url_for("projects"))

    cats = conn.execute("SELECT DISTINCT category FROM projects WHERE category IS NOT NULL AND category != ''").fetchall()
    categories = [r["category"] for r in cats if r["category"]]
    conn.close()
    return render_template("edit.html", project=project, categories=categories)

# ---------------- Delete project ----------------
@app.route("/delete/<int:project_id>")
def delete(project_id):
    if not session.get("user_id"):
        flash("Please log in.", "warning")
        return redirect(url_for("login"))

    conn = get_db_connection()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        conn.close()
        flash("Project not found.", "warning")
        return redirect(url_for("projects"))
    if project["owner_id"] != session["user_id"]:
        conn.close()
        flash("Not authorized.", "danger")
        return redirect(url_for("projects"))

    if project["pdf_filename"]:
        pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], project["pdf_filename"])
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()
    conn.close()
    flash("Project deleted.", "info")
    return redirect(url_for("projects"))

# ---------------- Author profile ----------------
@app.route("/author/<int:owner_id>")
def author_profile(owner_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id=?", (owner_id,)).fetchone()
    if not user:
        conn.close()
        flash("Author not found.", "warning")
        return redirect(url_for("projects"))
    projects = conn.execute("SELECT * FROM projects WHERE owner_id=?", (owner_id,)).fetchall()
    total_views = sum(p["views"] for p in projects)
    total_downloads = sum(p["downloads"] for p in projects)
    conn.close()
    return render_template("author.html", user=user, projects=projects, total_views=total_views, total_downloads=total_downloads)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=10000)
