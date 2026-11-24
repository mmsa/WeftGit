"""Capture review task context from the exact Git blobs on either side of a diff."""
from html import escape
from io import BytesIO
from itertools import chain
from urllib.parse import urlencode
from uuid import uuid4
import re
import subprocess


def prepare(db):
    db.execute("""CREATE TABLE IF NOT EXISTS task_anchors (
        task INTEGER PRIMARY KEY, origin_pr INTEGER NOT NULL, file_key TEXT NOT NULL,
        path BLOB NOT NULL, side TEXT NOT NULL, revision TEXT NOT NULL,
        line INTEGER, excerpt TEXT NOT NULL)""")


def side_blobs(change):
    old_mode, new_mode, old_oid, new_oid, _ = change.identity.lstrip(":").split()
    return {name: oid for name, mode, oid in (("before", old_mode, old_oid), ("after", new_mode, new_oid))
            if mode.startswith("100") or mode == "120000"}


def source_line(repo, oid, wanted):
    """Read through the requested line with bounded memory, including giant lines."""
    if not oid:
        return None
    with subprocess.Popen(["git", f"--git-dir={repo}", "cat-file", "blob", oid],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        try:
            prefix = proc.stdout.read(8192)
            if b"\0" in prefix:
                return None
            number = 1
            excerpt = b""
            pieces = chain(BytesIO(prefix), iter(lambda: proc.stdout.readline(65536), b""))
            for data in pieces:
                if number == wanted:
                    excerpt += data.removesuffix(b"\n")
                    if len(excerpt) > 512:
                        return excerpt[:512].decode(errors="replace") + "…"
                if data.endswith(b"\n"):
                    if number == wanted:
                        return excerpt.rstrip(b"\r").decode(errors="replace")
                    number += 1
            return excerpt.decode(errors="replace") if number == wanted and excerpt else None
        finally:
            if proc.poll() is None:
                proc.kill()


def validate(repo, pr, base, head, data, selected, changed_files):
    key = data.get("file_key", [""])[0]
    if selected != key or data.get("sha", [""])[0] != head or data.get("base", [""])[0] != base:
        raise ValueError("Reload the selected file at the reviewed revision.")
    origin, changes = changed_files(repo, base, head)
    change = changes.get(key)
    if not change:
        raise ValueError("Choose a file from this review.")
    side = data.get("side", [""])[0]
    if side == "file":
        return (pr, key, change.path, side, origin if change.status == "D" else head, None, "")
    raw_line = data.get("line", [""])[0]
    if side not in ("before", "after") or not re.fullmatch("[1-9][0-9]{0,8}", raw_line):
        raise ValueError("Choose Before or After and enter a positive source line number.")
    line = int(raw_line)
    excerpt = source_line(repo, side_blobs(change).get(side), line)
    if excerpt is None:
        raise ValueError("That text line does not exist on the selected side. Choose another line or the whole file.")
    return (pr, key, change.path, side, origin if side == "before" else head, line, excerpt)


def copy(db, old_task, new_task):
    db.execute("""INSERT INTO task_anchors SELECT ?,origin_pr,file_key,path,side,revision,line,excerpt
                  FROM task_anchors WHERE task=?""", (new_task, old_task))


def link(anchor, review_url, current_pr):
    if not anchor:
        return ""
    origin_pr, key, path, side, revision, line, excerpt = anchor
    params = {"pr":origin_pr, "changed":key}
    if line:
        params.update(anchor_side=side, anchor_line=line)
    target = escape(review_url + "&" + urlencode(params))
    label = escape(path.decode(errors="backslashreplace"))
    location = f"{side.title()} · {label}:{line}" if line else label
    prefix = f"Earlier review #{origin_pr} · " if str(origin_pr) != str(current_pr) else ""
    return (f'<small><a href="{target}#{"line-context" if line else "file-patch"}">{prefix}{location}</a>'
            f' · <code>{revision[:8]}</code></small>' + (f'<code>{escape(excerpt)}</code>' if line else ""))


def controls(repo, change, base, head, fields, target, writable, draft=None, error=""):
    blobs = side_blobs(change)
    sides = [side for side, oid in blobs.items() if source_line(repo, oid, 1) is not None]
    initial_side = fields.get("anchor_side", ["file"])[0]
    raw_line = fields.get("anchor_line", [""])[0]
    context = ""
    if initial_side in sides and re.fullmatch("[1-9][0-9]{0,8}", raw_line):
        snippet = source_line(repo, blobs[initial_side], int(raw_line))
        if snippet is not None:
            context = f'<div id="line-context"><strong>{initial_side.title()} · line {raw_line}</strong><pre tabindex="0" aria-label="Selected source line">{escape(snippet)}</pre></div>'
    if not writable:
        return context
    note = ""
    if draft:
        initial_side = draft.get("side", ["file"])[0]
        raw_line = draft.get("line", [""])[0]
        note = draft.get("note", [""])[0]
    options = '<option value="file">Whole file</option>' + "".join(
        f'<option value="{side}"{" selected" if side == initial_side else ""}>{side.title()}</option>' for side in sides)
    hint = "" if sides else "<small>This change supports whole-file tasks.</small>"
    return context + f'''<details id="file-task" {"open" if draft else ""}><summary>Add file or line task</summary>
{f'<p class="form-error" role="alert">{escape(error)}</p>' if draft else ''}
<form method="post" action="{target}"><input type="hidden" name="action" value="add-anchored-task">
<input type="hidden" name="sha" value="{head}"><input type="hidden" name="base" value="{base}">
<input type="hidden" name="file_key" value="{change.key}"><input type="hidden" name="token" value="{uuid4().hex}">
<label>Task location<select name="side">{options}</select></label>
<label {"hidden" if not sides else ""}>Source line<input type="number" name="line" min="1" max="999999999" value="{escape(raw_line)}"></label>
<label>File task<input name="note" maxlength="500" required value="{escape(note)}" placeholder="What needs a follow-up?"></label>
<button>Add file task</button></form>{hint}</details>'''
