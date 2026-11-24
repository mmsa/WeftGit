"""Persistent local accounts and verified browser sessions for reviewer attribution."""
from contextlib import closing, contextmanager
from hashlib import pbkdf2_hmac, sha256
from html import escape
from secrets import compare_digest, token_bytes, token_urlsafe
from time import time
from urllib.parse import urlsplit
import sqlite3

from reviewers import handle

SESSION_SECONDS = 7 * 24 * 3600
PASSWORD_ROUNDS = 600_000
ROLES = ("member", "admin", "owner")


class Denied(ValueError):
    pass


@contextmanager
def connect(root):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".users.sqlite3"
    if path.is_symlink():
        raise OSError("User database unavailable")
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS users(handle TEXT PRIMARY KEY, name TEXT NOT NULL,
                   role TEXT NOT NULL, active INTEGER NOT NULL, secret TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, handle TEXT NOT NULL,
                   expires INTEGER NOT NULL)""")
        yield db



def password_hash(password, salt=None):
    if not 8 <= len(password) <= 256:
        raise ValueError("Use a password of 8–256 characters.")
    salt = salt if salt is not None else token_bytes(16).hex()
    digest = pbkdf2_hmac("sha256",password.encode(),bytes.fromhex(salt),PASSWORD_ROUNDS).hex()
    return salt + "." + digest


def profile(db, token):
    if not token:
        return None
    return db.execute("""SELECT u.handle,u.name,u.role FROM sessions s JOIN users u ON u.handle=s.handle
                        WHERE s.token=? AND s.expires>? AND u.active=1""",
                      (sha256(token.encode()).hexdigest(), int(time()))).fetchone()


def state(root, token=""):
    if not (root / ".users.sqlite3").is_file():
        return False, None
    with connect(root) as db:
        configured = bool(db.execute("SELECT 1 FROM users WHERE role='owner' LIMIT 1").fetchone())
        user = profile(db, token)
        return configured, dict(user) if user else None


def issue(db, actor):
    token = token_urlsafe(32)
    db.execute("DELETE FROM sessions WHERE expires<=?", (int(time()),))
    db.execute("INSERT INTO sessions VALUES(?,?,?)",
               (sha256(token.encode()).hexdigest(),actor,int(time())+SESSION_SECONDS))
    return token


def import_handles(root, db):
    """Keep historical names visible for an owner to activate with credentials."""
    for path in root.glob("*/*.git/freethegit.sqlite3"):
        if any(p.is_symlink() for p in (path,path.parent,path.parent.parent)):
            continue
        historical = set()
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as repo:
                for table in ("pr_authors","pr_reviewers","pr_signoffs"):
                    columns = {row[1] for row in repo.execute(f"PRAGMA table_info({table})")}
                    if "person" in columns:
                        historical.update(person for person, in repo.execute(f"SELECT DISTINCT person FROM {table}"))
        except (sqlite3.Error,OSError):
            continue
        for person in historical:
            try:
                valid = handle(person) if isinstance(person,str) else ""
            except ValueError:
                continue
            if valid:
                db.execute("INSERT OR IGNORE INTO users VALUES(?,?,'member',0,NULL)",(valid,valid))


def change(root, token, data):
    """Apply one form action and return a new session token, an empty token to clear, or None."""
    action = data.get("action","")
    with connect(root) as db, db:
        db.execute("BEGIN IMMEDIATE")
        actor = profile(db, token)
        if action == "logout":
            db.execute("DELETE FROM sessions WHERE token=?", (sha256(token.encode()).hexdigest(),))
            return ""
        if action == "login":
            account = db.execute("SELECT * FROM users WHERE handle=?", (handle(data.get("handle","")),)).fetchone()
            secret = account["secret"] if account and account["active"] else None
            valid = False
            try:
                computed = password_hash(data.get("password",""), secret.split(".")[0] if secret else "00"*16)
                valid = bool(secret and compare_digest(computed,secret))
            except ValueError:
                pass
            if not valid:
                raise Denied("The handle or password is incorrect, or the account is inactive.")
            if token:
                db.execute("DELETE FROM sessions WHERE token=?", (sha256(token.encode()).hexdigest(),))
            return issue(db, account["handle"])

        setup = action == "setup"
        if setup:
            if db.execute("SELECT 1 FROM users WHERE role='owner'").fetchone():
                raise Denied("An owner has already been set up. Sign in to manage users.")
        elif not actor or actor["role"] not in ("owner","admin"):
            raise Denied("Sign in as an owner or admin to manage users.")
        person = handle(data.get("handle",""))
        target = db.execute("SELECT * FROM users WHERE handle=?", (person,)).fetchone()
        if not setup and actor["role"] == "admin" and target and target["role"] != "member" and actor["handle"] != person:
            raise Denied("An owner manages other administrators.")
        if setup or action in ("add","edit"):
            name = data.get("name","").strip()
            if not 1 <= len(name) <= 100:
                raise ValueError("Enter a display name of 1–100 characters.")
            role = "owner" if setup else data.get("role","member")
            if role not in ROLES:
                raise ValueError("Choose a valid role.")
            if not setup and actor["role"] != "owner" and role != (target["role"] if target else "member"):
                raise Denied("Only owners can appoint administrators or owners.")
            password = data.get("password","")
            if action == "edit":
                if not target:
                    raise ValueError("User not found.")
                protect_owner(db,target,role,target["active"])
                secret = password_hash(password) if password else target["secret"]
                db.execute("UPDATE users SET name=?,role=?,secret=? WHERE handle=?", (name,role,secret,person))
                if password or target["role"] != role:
                    db.execute("DELETE FROM sessions WHERE handle=?", (person,))
                    if actor["handle"] == person:
                        return issue(db,person)
                return None
            if target:
                raise ValueError("That handle already exists. Edit the existing user.")
            db.execute("INSERT INTO users VALUES(?,?,?,1,?)", (person,name,role,password_hash(password)))
            if setup:
                import_handles(root, db)
                return issue(db,person)
            return None
        if action in ("deactivate","activate") and target:
            active = int(action == "activate")
            if active and not target["secret"]:
                raise ValueError("Set a password before activating this historical account.")
            protect_owner(db,target,target["role"],active)
            db.execute("UPDATE users SET active=? WHERE handle=?", (active,person))
            db.execute("DELETE FROM sessions WHERE handle=?", (person,))
            return "" if actor["handle"] == person else None
        raise ValueError("Unknown user or action.")


def protect_owner(db, target, role, active):
    if target["role"] == "owner" and target["active"] and not (role=="owner" and active):
        if db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='owner'").fetchone()[0] <= 1:
            raise ValueError("Keep at least one active owner.")


def safe_return(value):
    try:
        url = urlsplit(value)
    except ValueError:
        return "/users"
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value and not url.scheme and not url.netloc and url.path in ("/","/users","/hosted","/guide") and not any(ord(c)<32 for c in value) else "/users"


@contextmanager
def reviewer_context(root, token, writing=False):
    if not (root / ".users.sqlite3").is_file():
        yield None, None
        return
    with connect(root) as db:
        db.execute("BEGIN")
        configured = db.execute("SELECT 1 FROM users WHERE role='owner' LIMIT 1").fetchone()
        actor = profile(db,token)
        names = {row["handle"]:row["name"] for row in db.execute(
            "SELECT handle,name FROM users WHERE active=1 ORDER BY name,handle")} if configured else None
        if not writing:
            db.commit()
        yield actor["handle"] if actor else "", names


def page(root, token, return_to="/users", error="", draft=None):
    draft = draft or {}
    with connect(root) as db:
        current = profile(db,token)
        accounts = db.execute("SELECT handle,name,role,active FROM users ORDER BY active DESC,name,handle").fetchall()
    manager = current and current["role"] in ("admin","owner")
    active_owners = sum(row["role"]=="owner" and row["active"] for row in accounts)
    return_field = f'<input type="hidden" name="return" value="{escape(safe_return(return_to))}">'

    def account_form(action, row=None):
        person = row["handle"] if row else ""
        name = row["name"] if row else ""
        role = row["role"] if row else "member"
        failed = bool(error and draft.get("action")==action and (not row or draft.get("handle")==person))
        if failed:
            person, name, role = draft.get("handle",""), draft.get("name",""), draft.get("role",role)
        if action == "login":
            return f'''<form method="post" action="/users"><input type="hidden" name="action" value="login">{return_field}
<label>Handle<input name="handle" value="{escape(person)}" autocomplete="username" required></label>
<label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form>'''
        roles = ROLES if current and current["role"]=="owner" else (role,)
        role_options = "".join(f'<option value="{r}"{" selected" if r==role else ""}>{r.title()}</option>' for r in roles if r in ROLES)
        role_field = f'<label>Role<select name="role">{role_options}</select></label>' if action!="setup" else ""
        password_label = "New password (optional)" if row else "Password"
        return f'''<form method="post" action="/users"><input type="hidden" name="action" value="{action}">{return_field}
<label>Display name<input name="name" value="{escape(name)}" maxlength="100" required></label>
<label>Handle<input name="handle" value="{escape(person)}" maxlength="48" autocomplete="off" {"readonly" if row else "required"}></label>
{role_field}<label>{password_label}<input name="password" type="password" autocomplete="new-password" minlength="8" maxlength="256" {"required" if not row else ""}></label>
<button>{ {"setup":"Create owner account","add":"Add user","edit":"Save changes"}[action] }</button></form>'''

    if not accounts:
        content = '<section class="user-card"><h2>Set up the first owner</h2><p>The owner manages local accounts and reviewer access.</p>'+account_form("setup")+'</section>'
    elif not current:
        content = '<section class="user-card"><h2>Sign in</h2>'+account_form("login")+'</section>'
    else:
        rows = []
        for row in accounts:
            person = escape(row["handle"])
            can_edit = manager and (current["role"]=="owner" or row["role"]=="member" or current["handle"]==row["handle"])
            controls = ""
            if can_edit:
                toggle = "deactivate" if row["active"] else "activate"
                fixed = row["role"]=="owner" and row["active"] and active_owners==1
                expanded = error and draft.get("handle")==row["handle"] and draft.get("action")=="edit"
                controls = f'''<details {"open" if expanded else ""}><summary>Edit @{person}</summary>{account_form("edit",row)}</details>
<form method="post" action="/users"><input type="hidden" name="action" value="{toggle}"><input type="hidden" name="handle" value="{person}">
<button class="quiet" {"disabled" if fixed else ""}>{toggle.title()} @{person}</button></form>'''
            rows.append(f'''<li class="user-row"><div><strong>{escape(row["name"])}</strong><small>@{person} · {row["role"].title()} · {"Active" if row["active"] else "Inactive"}</small></div>
<div class="user-actions">{controls}</div></li>''')
        add = (f'<details class="add-user" {"open" if error and draft.get("action")=="add" else ""}><summary>Add user</summary>'
               +account_form("add")+'</details>') if manager else ""
        content = f'''<section class="user-card"><div class="user-heading"><h2>Local users <small>{len(rows)}</small></h2>{add}</div>
<ul>{"".join(rows)}</ul></section>'''
    actor_bar = (f'<form method="post" action="/users"><span>Signed in as @{escape(current["handle"])}</span>'
                 '<button class="quiet" name="action" value="logout">Sign out</button></form>') if current else ""
    notice = f'<p class="form-error" role="alert">{escape(error)}</p>' if error else ""
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Users · FreeTheGit</title><link rel="stylesheet" href="/reader.css"><link rel="stylesheet" href="/pipelines.css"><link rel="stylesheet" href="/users.css">
<body class="product"><header class="app-bar"><a class="brand" href="/">freethegit</a><nav aria-label="Main navigation"><a href="/#reviews">Pull requests</a><a href="/#repositories">Repositories</a><a href="/#local-deployments">Deployments</a><a href="/#pipelines">Pipelines</a><a href="/users" aria-current="page">Users</a></nav></header>
<main class="users-page"><div class="user-header"><h1>Users</h1>{actor_bar}</div>{notice}{content}</main></body></html>'''
