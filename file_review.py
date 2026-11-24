"""Index a reviewed Git tree delta and render a bounded slice of one file's patch."""
from dataclasses import dataclass
from hashlib import sha256
from html import escape
import os
import subprocess
import task_anchors
from urllib.parse import urlencode


@dataclass(frozen=True)
class FileChange:
    path: bytes
    identity: str
    status: str

    @property
    def key(self):
        return sha256(self.path).hexdigest()

    @property
    def label(self):
        return self.path.decode("utf-8", errors="backslashreplace")


def changed_files(repo, base, head):
    # Diff against the common ancestor, as in the combined review.
    prefix = ["git", f"--git-dir={repo}"]
    ancestor = subprocess.run(prefix + ["merge-base", base, head], capture_output=True, timeout=10)
    origin = ancestor.stdout.decode().strip() if ancestor.returncode == 0 else base
    raw = subprocess.check_output(prefix + ["diff", "--raw", "--no-abbrev", "--no-renames",
                                  "-z", origin, head], timeout=10)
    entries = iter(raw.split(b"\0")[:-1])
    changes = {}
    for meta, path in zip(entries, entries):
        change = FileChange(path, meta.decode("ascii"), meta.split()[-1].decode("ascii"))
        changes[change.key] = change
    return origin, changes


def prepare(db):
    db.execute("""CREATE TABLE IF NOT EXISTS reviewed_files (
        pr INTEGER NOT NULL, file_key TEXT NOT NULL, identity TEXT NOT NULL,
        PRIMARY KEY(pr, file_key))""")


def mark(db, repo, number, base, head, data):
    key = data.get("file_key", [""])[0]
    flag = data.get("reviewed", [""])[0]
    if flag not in {"0", "1"} or data.get("sha", [""])[0] != head:
        raise ValueError("Reload this file at the reviewed revision.")
    _, changes = changed_files(repo, base, head)
    if key not in changes:
        raise ValueError("File is outside this review.")
    if flag == "1":
        db.execute("INSERT OR REPLACE INTO reviewed_files VALUES(?,?,?)", (number,key,changes[key].identity))
    else:
        db.execute("DELETE FROM reviewed_files WHERE pr=? AND file_key=?", (number,key))


def carry_forward(db, repo, previous, successor, base, head):
    marks = list(db.execute("SELECT file_key,identity FROM reviewed_files WHERE pr=?", (previous,)))
    if marks:
        _, changes = changed_files(repo, base, head)
        for key, identity in marks:
            # Raw identity includes both modes, both object IDs and the change type.
            if key in changes and changes[key].identity == identity:
                db.execute("INSERT OR IGNORE INTO reviewed_files VALUES(?,?,?)", (successor,key,identity))


def file_patch(repo, origin, head, change, page):
    cmd = ["git", f"--git-dir={repo}", "--literal-pathspecs", "diff", "--no-ext-diff",
           "--no-textconv", "--no-renames", "--no-color", origin, head, "--", os.fsdecode(change.path)]
    # Bound both giant single lines and ordinary patches. Pages cover contiguous bytes.
    limit = 128 * 1024
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        try:
            for _ in range(page):
                if len(proc.stdout.read(limit)) < limit:
                    raise ValueError("Patch page unavailable.")
            data = proc.stdout.read(limit + 4)
            more = len(data) > limit
            if more:
                end = limit
                while end < len(data) and data[end] & 0xc0 == 0x80:
                    end += 1
                data = data[:end]
            if page:
                while data and data[0] & 0xc0 == 0x80:
                    data = data[1:]
            if not more and proc.wait(timeout=10):
                raise ValueError("Could not load this file's patch.")
        finally:
            if proc.poll() is None:
                proc.kill()
    lines = []
    for line in data.decode(errors="replace").removesuffix("\n").split("\n"):
        style = "added" if line.startswith("+") else "removed" if line.startswith("-") else "context"
        lines.append(f'<span class="{style}">{escape(line)}</span>')
    return f'<pre class="patch" tabindex="0" aria-label="Changes to {escape(change.label)}">' + "\n".join(lines) + '</pre>', more


def panel(repo, number, base, head, fields, marks, writable, review_url, task_draft=None, task_error=""):
    origin, changes = changed_files(repo, base, head)
    selected = fields.get("changed", [""])[0]
    if not selected and len(changes) == 1:
        selected = next(iter(changes))
    if selected and selected not in changes:
        raise ValueError("File is outside this review.")
    page = int(fields.get("chunk", ["0"])[0])
    if page < 0:
        raise ValueError("Invalid patch page.")

    def target(key, chunk=0):
        return escape(review_url + "&" + urlencode({"changed":key, "chunk":chunk})) + "#file-patch"

    rows = []
    for key, change in changes.items():
        current = ' aria-current="true"' if selected == key else ""
        status = {"A":"Added","D":"Deleted","M":"Modified","T":"Type changed"}.get(change.status,change.status)
        flag = "Reviewed" if key in marks else "Needs review"
        rows.append(f'<li><a href="{target(key)}"{current}><strong>{escape(change.label)}</strong>'
                    f'<small>{status} · {flag}</small></a></li>')
    reader = ""
    if selected:
        change = changes[selected]
        patch, more = file_patch(repo, origin, head, change, page)
        action = ""
        if writable:
            checked = selected in marks
            action = f'''<form method="post" action="{target(selected,page)}">
<input type="hidden" name="action" value="file-review"><input type="hidden" name="sha" value="{head}">
<input type="hidden" name="file_key" value="{selected}"><button name="reviewed" value="{int(not checked)}">Mark {"unreviewed" if checked else "reviewed"}</button></form>'''
        pages = []
        if page:
            pages.append(f'<a href="{target(selected,page-1)}">Previous patch section</a>')
        if more:
            pages.append(f'<a href="{target(selected,page+1)}">Next patch section</a>')
        context = task_anchors.controls(repo, change, base, head, fields, target(selected,page), writable, task_draft, task_error)
        reader = f'<section id="file-patch"><h4>{escape(change.label)}</h4>{action}{context}{patch}{" · ".join(pages)}</section>'
    return f'''<section id="changed-files"><h3>Changed files <small>{len(marks)} of {len(changes)} reviewed</small></h3>
<label class="file-filter" {"hidden" if len(changes) < 2 else ""}>Filter changed files<input type="search" id="file-filter" placeholder="Find a file by path"></label>
<ul class="file-index">{''.join(rows)}</ul><p id="file-empty" hidden>No matching files.</p>{reader}</section>'''
