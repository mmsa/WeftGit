"""Local reviewer routing and per-person records of examined Git snapshots."""
from html import escape, unescape
from urllib.parse import urlencode
import re
import subprocess


def handle(value):
    normalized = value.strip().lower()
    if not re.fullmatch("[a-z0-9][a-z0-9._-]{0,47}", normalized):
        raise ValueError("Use 1–48 letters, numbers, dots, underscores or hyphens for a local handle.")
    return normalized


def prepare(db):
    db.execute("CREATE TABLE IF NOT EXISTS pr_authors(pr INTEGER PRIMARY KEY, person TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS pr_reviewers(pr INTEGER, person TEXT, PRIMARY KEY(pr,person))")
    db.execute("""CREATE TABLE IF NOT EXISTS pr_signoffs(pr INTEGER, person TEXT, head TEXT, base TEXT,
               created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(pr,person,head,base))""")


def inherit(db, previous, successor, active_users=None):
    db.execute("INSERT OR IGNORE INTO pr_authors SELECT ?,person FROM pr_authors WHERE pr=?", (successor, previous))
    people = db.execute("SELECT person FROM pr_reviewers WHERE pr=?", (previous,)).fetchall()
    db.executemany("INSERT OR IGNORE INTO pr_reviewers VALUES(?,?)",
                   ((successor,p) for p, in people if active_users is None or p in active_users))


def branch_tips(repo):
    result = subprocess.run(["git", f"--git-dir={repo}", "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads"],
                            capture_output=True, timeout=10, check=True)
    return dict(line.split(" ", 1) for line in result.stdout.decode().splitlines())


def record_action(db, repo, pr, review, action, data, actor, active_users=None):
    if not actor or (active_users is not None and actor not in active_users):
        return "", "Choose a local reviewer identity first."
    if data.get("actor", [""])[0] != actor:
        return "", "Your local identity changed. Reload before updating this review."
    if action in ("assign", "unassign"):
        try:
            person = handle(data.get("person", [""])[0])
        except ValueError as error:
            return "", str(error)
        if action == "assign":
            if active_users is not None and person not in active_users:
                return "", "Choose an active user from the user directory."
            db.execute("INSERT OR IGNORE INTO pr_reviewers VALUES(?,?)", (pr, person))
            return f"Assigned @{person}.", ""
        db.execute("DELETE FROM pr_reviewers WHERE pr=? AND person=?", (pr, person))
        return f"Removed @{person} from reviewers.", ""
    source, target, head, base = review[:4]
    if action == "undo-signoff":
        db.execute("DELETE FROM pr_signoffs WHERE pr=? AND person=? AND head=? AND base=?", (pr, actor, head, base))
        return "Personal review completion removed.", ""
    tips = branch_tips(repo)
    if (tips.get("refs/heads/" + source), tips.get("refs/heads/" + target)) != (head, base):
        return "", "The branches changed. Review the latest snapshot before recording completion."
    if (data.get("head", [""])[0], data.get("base", [""])[0]) != (head, base):
        return "", "Reload this snapshot before recording completion."
    db.execute("INSERT OR IGNORE INTO pr_signoffs(pr,person,head,base) VALUES(?,?,?,?)", (pr, actor, head, base))
    return f"Review recorded for @{actor} on {head[:8]}.", ""


def panel(db, pr, actor, open_review, link, head, base, current, active_users=None):
    author = db.execute("SELECT person FROM pr_authors WHERE pr=?", (pr,)).fetchone()
    assigned = [person for person, in db.execute("SELECT person FROM pr_reviewers WHERE pr=? ORDER BY person", (pr,))]
    completions = dict(db.execute("SELECT person,created FROM pr_signoffs WHERE pr=? AND head=? AND base=?",
                                  (pr, head, base)))
    rows = []
    for person in sorted(set(assigned) | set(completions)):
        completed = person in completions
        status = "Reviewed this snapshot" if completed else "Awaiting review"
        if completed and not current and open_review:
            status = "Reviewed earlier snapshot"
        remove = ""
        if actor and open_review and person in assigned:
            remove = f'<form method="post" action="{link}"><input type="hidden" name="actor" value="{escape(actor)}"><input type="hidden" name="person" value="{escape(person)}"><button name="action" value="unassign" aria-label="Remove @{escape(person)}">Remove</button></form>'
        stamp = f'<small>{escape(completions[person])} UTC · {escape(head[:8])} against {escape(base[:8])}</small>' if completed else ""
        rows.append(f'<li class="review-row"><div><span>@{escape(person)} · {status}</span>{stamp}</div>{remove}</li>')
    controls = ""
    if open_review and actor:
        action = "undo-signoff" if actor in completions else "signoff"
        label = "Undo my review" if actor in completions else "Complete my review"
        chooser = '<input name="person" required aria-label="Reviewer handle" placeholder="Reviewer handle">'
        if active_users is not None:
            options = "".join(f'<option value="{escape(p)}">{escape(n)} (@{escape(p)})</option>' for p,n in active_users.items())
            chooser = '<select name="person" required aria-label="Reviewer handle"><option value="">Choose reviewer</option>' + options + '</select>'
        controls = f'''<form method="post" action="{link}"><input type="hidden" name="actor" value="{escape(actor)}">{chooser}<button name="action" value="assign">Assign reviewer</button></form>
<form method="post" action="{link}"><input type="hidden" name="actor" value="{escape(actor)}"><input type="hidden" name="head" value="{head}"><input type="hidden" name="base" value="{base}"><button name="action" value="{action}" {"" if current or action == "undo-signoff" else "disabled"}>{label}</button></form>'''
    elif open_review and active_users is not None:
        return_url = "/users?" + urlencode({"return": unescape(link) + "#review-details"})
        controls = f'<a href="{escape(return_url)}">Sign in to assign or record a review</a>'
    elif open_review:
        controls = '<a href="#local-identity">Choose identity to assign or record a review</a>'
    people = '<ul class="record-list">' + ''.join(rows) + '</ul>' if rows else '<p>No reviewers assigned.</p>'
    return f'''<section class="review-people" aria-label="Reviewers"><h3>Reviewers</h3><p>Author · {("@" + escape(author[0])) if author else "Unspecified"}</p>{people}{controls}</section>'''


def inbox_metadata(db, repo, actor, rows):
    """Personalize in bulk while supporting read-only, pre-identity databases."""
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    authors = dict(db.execute("SELECT pr,person FROM pr_authors")) if "pr_authors" in tables else {}
    assigned = {pr for pr, in db.execute("SELECT pr FROM pr_reviewers WHERE person=?", (actor,))} if "pr_reviewers" in tables else set()
    signatures = set(db.execute("SELECT pr,head,base FROM pr_signoffs WHERE person=?", (actor,))) if "pr_signoffs" in tables else set()
    tips = {}
    if signatures:
        tips = branch_tips(repo)
    for row in rows:
        seen = (row["id"], row.get("head"), row.get("base")) in signatures
        outdated = seen and not row["superseded"] and row["state"] == "open" and (tips.get("refs/heads/"+row["source"]), tips.get("refs/heads/"+row["target"])) != (row.get("head"),row.get("base"))
        row["authored"] = authors.get(row["id"]) == actor
        row["reviewed"] = seen and not outdated
        row["needsReview"] = row["state"] == "open" and row["id"] in assigned and not row["reviewed"]
