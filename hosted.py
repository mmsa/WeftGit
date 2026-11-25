"""Local hosted-repository operations for the browser."""
from repository_store import database_path

from html import escape
from agent_report import prepare_reports
import deployments as releases
import reviewers
import file_review
import task_anchors
import review_brief
from starter_readme import add_readme
from uuid import uuid4
import re
from contextlib import closing
import sqlite3
import subprocess
from urllib.parse import parse_qs, urlencode


def git(repo, *args):
    return subprocess.check_output(
        ["git", f"--git-dir={repo}", *args], stderr=subprocess.DEVNULL, timeout=10
    )


def patch_preview(repo, base, head):
    """Show a bounded patch while preserving full review metadata for large changes."""
    with subprocess.Popen(
        ["git", f"--git-dir={repo}", "diff", "--no-ext-diff", "--no-textconv", "--no-color", base, head],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as process:
        limit = 128 * 1024
        data = process.stdout.read(limit + 1)
        if len(data) > limit:
            process.kill()
        process.stdout.close()
        result = process.wait(timeout=10)
    if result and len(data) <= limit:
        return "<p>Could not load the patch.</p>"
    lines = []
    for line in data[:limit].decode(errors="replace").splitlines():
        style = "added" if line.startswith("+") else "removed" if line.startswith("-") else "context"
        lines.append(f'<span class="{style}">{escape(line)}</span>')
    clipped = "<p>Showing the first 128 KiB of this patch.</p>" if len(data) > limit else ""
    return '<pre class="patch" tabindex="0" aria-label="Combined code diff">' + "\n".join(lines) + "</pre>" + clipped


def merge_review(repo, number, source, target, head, base):
    """Commit the merge result and its recovery marker in one Git ref transaction."""
    source_ref, target_ref = f"refs/heads/{source}", f"refs/heads/{target}"
    stale = "A branch changed since review. Open a pull request for the latest commits."
    try:
        current = git(repo, "rev-parse", "--verify", source_ref).decode().strip()
        destination = git(repo, "rev-parse", "--verify", target_ref).decode().strip()
    except subprocess.CalledProcessError:
        return stale
    if (current, destination) != (head, base):
        return stale
    try:
        ancestor = git(repo, "merge-base", base, head).decode().strip()
    except subprocess.CalledProcessError:
        return "These branches have no common history."
    if ancestor == head:
        return "The target already contains the reviewed commits."
    result = head
    if ancestor != base:
        try:
            tree = git(repo, "merge-tree", "--write-tree", base, head).decode().strip()
        except subprocess.CalledProcessError:
            return "Git could not merge these branches cleanly. Resolve the conflict locally and open a new review."
        result = git(repo, "-c", "user.name=WeftGit", "-c", "user.email=reviews@localhost",
                     "commit-tree", tree, "-p", base, "-p", head, "-m", f"Merge pull request #{number}").decode().strip()
    commands = (f"verify {source_ref} {head}\nupdate {target_ref} {result} {base}\n"
                f"create refs/weftgit/merges/{number} {result}\n")
    transaction = subprocess.run(["git", f"--git-dir={repo}", "update-ref", "--stdin"],
                                 input=commands.encode(), capture_output=True, timeout=10)
    return stale if transaction.returncode else ""


def open_review(db, repo, source, target, head, base, title, fresh=False):
    if not fresh and db.execute("""SELECT 1 FROM prs WHERE source=? AND target=? AND head=? AND base=?
                                   AND state IN ('declined','merged')""", (source,target,head,base)).fetchone():
        return False
    result = db.execute("INSERT OR IGNORE INTO prs(source,target,head,base,title) VALUES (?, ?, ?, ?, ?)",
                        (source, target, head, base, title))
    if result.rowcount:
        snapshot = f"refs/weftgit/reviews/{result.lastrowid}"
        subprocess.run(["git", f"--git-dir={repo}", "update-ref", "--stdin"],
                       input=f"update {snapshot}/head {head}\nupdate {snapshot}/base {base}\n".encode(),
                       capture_output=True, check=True, timeout=10)
    return result.rowcount


def page(root, query, body=None, actor="", active_users=None):
    fields = parse_qs(query)
    slug = fields.get("repo", [""])[0]
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}/[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", slug):
        raise ValueError("Invalid repository")
    repo = root / (slug + ".git")
    if repo.is_symlink() or repo.parent.is_symlink() or database_path(repo).is_symlink() or not (repo / "HEAD").is_file():
        raise ValueError("Repository not found")
    posted = parse_qs(body or "")
    starting = posted.get("action") == ["start-readme"]
    starter_notice = starter_error = ""
    draft_description = posted.get("description", [""])[0] if starting else ""
    if starting:
        try:
            starter_notice = add_readme(repo, slug.split("/")[1], draft_description)
        except ValueError as error:
            starter_error = str(error)
    tips = reviewers.branch_tips(repo)
    branches = [name.removeprefix("refs/heads/") for name in tips]
    ref = fields.get("ref", ["main" if "main" in branches else next(iter(branches), "")])[0]
    environment = fields.get("environment", ["staging"])[0]
    if posted.get("action") == ["deploy"]:
        ref = posted.get("ref", [ref])[0]
        environment = posted.get("environment", [environment])[0]
    if ref and ref not in branches:
        if "pr" not in fields:
            raise ValueError("Unknown branch")
        ref = "main" if "main" in branches else next(iter(branches), "")
    commit = tips.get("refs/heads/" + ref, "")
    url = "/hosted?" + urlencode({"repo": slug, "ref": ref})
    notice = starter_notice
    form_error = ""
    pr_form = {}
    view = fields.get("view", ["pulls" if "pr" in fields else "source"])[0]
    if view not in ("source", "pulls", "deploys"):
        view = "source"
    selected_pr = fields.get("pr", [""])[0]
    with closing(sqlite3.connect(database_path(repo))) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS prs (source TEXT, target TEXT, head TEXT, base TEXT, title TEXT, UNIQUE(head, base))")
        db.execute("CREATE TABLE IF NOT EXISTS deployments (environment TEXT, commit_id TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)")
        db.execute("""CREATE TABLE IF NOT EXISTS review_tasks (
            id INTEGER PRIMARY KEY, pr INTEGER NOT NULL, note TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0, token TEXT NOT NULL UNIQUE)""")
        if "state" not in {column[1] for column in db.execute("PRAGMA table_info(prs)")}:
            db.execute("ALTER TABLE prs ADD COLUMN state TEXT NOT NULL DEFAULT 'open'")
        db.execute("CREATE TABLE IF NOT EXISTS review_history (previous INTEGER, successor INTEGER, PRIMARY KEY(previous,successor))")
        if db.execute("PRAGMA user_version").fetchone()[0] < 2:
            # Preserve rowids referenced by tasks, evidence and recovery refs.
            db.execute("""CREATE TABLE prs_by_branch (
                source TEXT, target TEXT, head TEXT, base TEXT, title TEXT,
                state TEXT NOT NULL DEFAULT 'open')""")
            db.execute("""INSERT INTO prs_by_branch(rowid,source,target,head,base,title,state)
                          SELECT rowid,source,target,head,base,title,state FROM prs""")
            db.execute("DROP TABLE prs")
            db.execute("ALTER TABLE prs_by_branch RENAME TO prs")
            db.execute("UPDATE prs SET state='superseded' WHERE state='open' AND rowid IN (SELECT previous FROM review_history)")
            db.execute("CREATE UNIQUE INDEX open_pr_snapshot ON prs(source,target,head,base) WHERE state='open'")
            db.execute("PRAGMA user_version=2")
        # A process can stop after updating Git and before committing SQLite.
        markers = git(repo, "for-each-ref", "--format=%(refname:strip=3)", "refs/weftgit/merges", "refs/freethegit/merges").decode().splitlines()
        db.executemany("UPDATE prs SET state='merged' WHERE rowid=? AND state!='merged'",
                       ((marker,) for marker in markers if marker.isdecimal()))
        reviewers.prepare(db)
        prepare_reports(db)
        file_review.prepare(db)
        task_anchors.prepare(db)
        if body is not None:
            data = parse_qs(body)
            action = data.get("action", [""])[0]
            if action == "pr":
                view = "pulls"
                source = data.get("source", [""])[0]
                target = data.get("target", [""])[0]
                title = data.get("title", [""])[0].strip()
                pr_form = {"source": source, "target": target, "title": title}
                if source not in branches or target not in branches:
                    form_error = "Select two existing branches."
                elif source == target:
                    form_error = "Select a source branch different from the target branch."
                elif not title:
                    form_error = "Enter a pull request title."
                else:
                    head, base = git(repo, "rev-parse", f"refs/heads/{source}", f"refs/heads/{target}").decode().splitlines()
                    if head == base:
                        form_error = "Push a new commit to the source branch before opening a pull request."
                    else:
                        created = open_review(db, repo, source, target, head, base, title)
                        if created and actor:
                            db.execute("INSERT INTO pr_authors SELECT rowid,? FROM prs WHERE source=? AND target=? AND head=? AND base=? AND state='open'",
                                       (actor, source, target, head, base))
                        notice = "Pull request opened." if created else "A pull request already exists for these commits."
                        pr_form = {}
            elif action in ("assign", "unassign", "signoff", "undo-signoff", "add-task", "add-anchored-task", "set-task", "merge", "decline", "refresh", "file-review"):
                view = "pulls"
                review = db.execute("SELECT source,target,head,base,state FROM prs WHERE rowid=?", (selected_pr,)).fetchone()
                if not review:
                    raise ValueError("Unknown pull request")
                if review[4] != "open":
                    notice = f"This pull request is {review[4]}."
                elif action == "file-review":
                    file_review.mark(db, repo, selected_pr, review[3], review[2], data)
                elif action in ("assign", "unassign", "signoff", "undo-signoff"):
                    notice, form_error = reviewers.record_action(db, repo, selected_pr, review, action, data, actor, active_users)
                elif action == "refresh":
                    source, target, head, base, _ = review
                    if source not in branches or target not in branches:
                        form_error = "Restore both branches before opening a fresh review."
                    else:
                        current, destination = git(repo, "rev-parse", f"refs/heads/{source}", f"refs/heads/{target}").decode().splitlines()
                        if current == destination or (head, base) == (current, destination):
                            form_error = "There are no new branch changes to review."
                        else:
                            title, = db.execute("SELECT title FROM prs WHERE rowid=?", (selected_pr,)).fetchone()
                            open_review(db, repo, source, target, current, destination, title, fresh=True)
                            successor, successor_state = db.execute(
                                "SELECT rowid,state FROM prs WHERE source=? AND target=? AND head=? AND base=? AND state='open'",
                                (source, target, current, destination)).fetchone()
                            added = db.execute("INSERT OR IGNORE INTO review_history VALUES(?,?)", (selected_pr, successor))
                            if added.rowcount and successor_state == "open":
                                reviewers.inherit(db, selected_pr, successor, active_users)
                                file_review.carry_forward(db, repo, selected_pr, successor, destination, current)
                                for old_task, note in db.execute("SELECT id,note FROM review_tasks WHERE pr=? AND resolved=0", (selected_pr,)):
                                    copied = db.execute("INSERT INTO review_tasks(pr,note,token) VALUES(?,?,?)", (successor,note,uuid4().hex))
                                    task_anchors.copy(db, old_task, copied.lastrowid)
                            db.execute("UPDATE prs SET state='superseded' WHERE rowid=?", (selected_pr,))
                            notice = f"Review #{successor} is available for the latest commits."
                elif action == "decline":
                    db.execute("UPDATE prs SET state='declined' WHERE rowid=?", (selected_pr,))
                    notice = "Pull request declined."
                elif action == "merge":
                    if db.execute("SELECT 1 FROM review_tasks WHERE pr=? AND resolved=0 LIMIT 1", (selected_pr,)).fetchone():
                        form_error = "Resolve open review tasks before merging."
                    else:
                        form_error = merge_review(repo, int(selected_pr), *review[:4])
                        if not form_error:
                            db.execute("UPDATE prs SET state='merged' WHERE rowid=?", (selected_pr,))
                            notice = f"Changes merged into {review[1]}."
                elif action in ("add-task", "add-anchored-task"):
                    note = data.get("note", [""])[0].strip()
                    token = data.get("token", [""])[0]
                    if not re.fullmatch(r"[0-9a-f]{32}", token) or not 1 <= len(note) <= 500:
                        form_error = "Write a review task between 1 and 500 characters."
                    else:
                        anchor = None
                        if action == "add-anchored-task":
                            try:
                                anchor = task_anchors.validate(repo, selected_pr, review[3], review[2], data,
                                                               fields.get("changed", [""])[0], file_review.changed_files)
                            except ValueError as error:
                                form_error = str(error)
                        if not form_error:
                            added = db.execute("INSERT OR IGNORE INTO review_tasks(pr,note,token) VALUES(?,?,?)", (selected_pr,note,token))
                            if added.rowcount and anchor:
                                db.execute("INSERT INTO task_anchors VALUES(?,?,?,?,?,?,?,?)", (added.lastrowid,*anchor))
                            notice = "Review task saved."
                else:
                    resolved = data.get("resolved", [""])[0]
                    if resolved not in ("0", "1"):
                        raise ValueError("Invalid task state")
                    result = db.execute("UPDATE review_tasks SET resolved=? WHERE id=? AND pr=?", (resolved, data.get("task", [""])[0], selected_pr))
                    if not result.rowcount:
                        raise ValueError("Unknown review task")
                    notice = "Task resolved." if resolved == "1" else "Task reopened."
            elif action == "deploy":
                view = "deploys"
                environment = data.get("environment", [""])[0]
                requested = data.get("sha", [""])[0]
                if not commit:
                    form_error = "Push a commit before publishing."
                elif commit != requested:
                    form_error = "The selected revision changed. Check the current commit before publishing."
                elif environment not in releases.ENVIRONMENTS:
                    form_error = "Choose a valid deployment environment."
                else:
                    try:
                        releases.publish(db, repo, environment, requested)
                    except (OSError, subprocess.SubprocessError):
                        form_error = "Could not package this revision. Check local disk space and repository availability, then retry."
                    else:
                        notice = f"Published {requested[:8]} to local {environment}."
        prs = db.execute("SELECT rowid, source, target, head, base, title, state FROM prs ORDER BY rowid DESC").fetchall()
        counts = dict(db.execute("SELECT pr, COUNT(*) FROM review_tasks WHERE resolved=0 GROUP BY pr"))
        tasks = db.execute("SELECT id, note, resolved FROM review_tasks WHERE pr=? ORDER BY resolved, id", (selected_pr,)).fetchall()
        anchors = {task:context for task,*context in db.execute("""
                  SELECT a.* FROM task_anchors a JOIN review_tasks t ON t.id=a.task WHERE t.pr=?""", (selected_pr,))}
        reviewed_files = {key for key, in db.execute("SELECT file_key FROM reviewed_files WHERE pr=?", (selected_pr,))}
        reports = db.execute("""SELECT agent,run,sha,summary,tests,work_item,work_url,created
                            FROM agent_reports WHERE pr=? ORDER BY rowid DESC""", (selected_pr,)).fetchall()
        predecessors = db.execute("SELECT previous FROM review_history WHERE successor=? ORDER BY previous DESC", (selected_pr,)).fetchall()
        latest, = db.execute("""WITH RECURSIVE later(id) AS (
                                  SELECT successor FROM review_history WHERE previous=?
                                  UNION SELECT h.successor FROM review_history h JOIN later ON h.previous=later.id
                                ) SELECT MAX(id) FROM later""", (selected_pr,)).fetchone()
        people_panel = ""
        chosen = next((row for row in prs if str(row[0]) == selected_pr), None)
        if chosen and view == "pulls":
            number, source, target, head, base, _, state = chosen
            tips = reviewers.branch_tips(repo)
            current = (tips.get("refs/heads/"+source), tips.get("refs/heads/"+target)) == (head, base)
            people_panel = reviewers.panel(db, number, actor, state == "open", escape(url + "&" + urlencode({"pr":number})), head, base, current, active_users)
        deployments = db.execute("SELECT environment, commit_id, created FROM deployments ORDER BY rowid DESC").fetchall()

    def options(values, selected=""):
        return "".join(f'<option{" selected" if name == selected else ""}>{escape(name)}</option>' for name in values)

    files = []
    preview = ""
    selected_path = fields.get("file", [""])[0]
    if commit and view == "source":
        tracked = git(repo, "ls-tree", "-r", "-z", commit)
        for entry in tracked.split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            _, kind, object_id = metadata.decode().split()
            path = raw_path.decode(errors="replace")
            if kind != "blob":
                continue
            link = url + "&" + urlencode({"file": path})
            files.append(f'<li><a href="{escape(link)}">{escape(path)}</a></li>')
            if selected_path == path:
                size = int(git(repo, "cat-file", "-s", object_id))
                preview = "<p>File is too large to preview.</p>"
                if size <= 256_000:
                    value = git(repo, "cat-file", "blob", object_id)
                    preview = "<p>Binary file.</p>" if b"\0" in value else f'<pre tabindex="0" aria-label="File contents">{escape(value.decode(errors="replace"))}</pre>'
    requests = []
    detail = ""
    for number, source, target, head, base, title, state in prs:
        link = escape(url + "&" + urlencode({"pr": number}))
        count = counts.get(number, 0)
        status = f"{count} open task" + ("s" if count != 1 else "")
        requests.append(f'<li class="review-row" data-pr-state="{state}"><div><a href="{link}">#{number} {escape(title)}</a><small>{escape(source)} → {escape(target)}</small></div><span class="task-count">{state.title()}{(" · " + status) if state == "open" else ""}</span></li>')
        if view == "pulls" and selected_pr == str(number):
            try:
                origin = git(repo, "merge-base", base, head).decode().strip()
            except subprocess.CalledProcessError:
                origin = ""
            full_diff = ""
            if fields.get("combined") == ["1"]:
                diff = git(repo, "diff", "--stat", origin or base, head).decode(errors="replace")
                full_diff = f'<details open class="patch-panel" id="combined-patch"><summary>Combined diff</summary><pre tabindex="0" aria-label="File change summary">{escape(diff) or "No file changes"}</pre>{patch_preview(repo, origin or base, head)}</details>'
            else:
                full_diff = f'<p class="combined-link"><a href="{link}&amp;combined=1#combined-patch">View combined diff</a></p>'
            closed = "hidden" if state != "open" else ""
            merge_disabled = "disabled" if count else ""
            items = []
            for task, note, resolved in tasks:
                items.append(f'''<li class="review-row"><div><small>{"Resolved" if resolved else "Open"}</small>{escape(note)}{task_anchors.link(anchors.get(task), url, selected_pr)}</div>
<form {closed} method="post" action="{link}"><input type="hidden" name="action" value="set-task"><input type="hidden" name="task" value="{task}">
<button name="resolved" value="{0 if resolved else 1}">{"Reopen" if resolved else "Resolve"}</button></form></li>''')
            report_items = []
            live_head = tips.get(f"refs/heads/{source}", "")
            live_base = tips.get(f"refs/heads/{target}", "")
            stale = (live_head, live_base) != (head, base)
            freshness_action = ""
            if state != "open":
                decision = f"Review {state}"
                if latest:
                    fresh_url = escape(url + "&" + urlencode({"pr": latest}))
                    freshness_action = f'<a class="fresh-link" href="{fresh_url}">Open latest review #{latest} →</a>'
            elif stale:
                decision = "Branch changes need review"
                merge_disabled = "disabled"
                if live_head and live_base and live_head != live_base:
                    freshness_action = f'<form method="post" action="{link}"><button name="action" value="refresh">Open fresh review</button></form><small>Unresolved tasks carry forward.</small>'
                elif not live_head or not live_base:
                    decision = "A reviewed branch was removed"
                else:
                    decision = "Both branches now point to the same commit"
            elif count:
                decision = f"Resolve {status} before merging"
            elif not origin:
                decision = "Branches have no common history"
                merge_disabled = "disabled"
            elif origin == head:
                decision = "Target already contains these changes"
                merge_disabled = "disabled"
            else:
                decision = "Ready to merge"
                if origin != base:
                    preview_result = subprocess.run(
                        ["git", f"--git-dir={repo}", "merge-tree", "--write-tree", base, head],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    if preview_result.returncode:
                        decision = "Resolve branch conflicts locally before merging" if preview_result.returncode == 1 else "Git could not verify merge readiness"
                        merge_disabled = "disabled"
            previous = " · ".join(f'<a href="{escape(url)}&amp;pr={old}">Earlier review #{old}</a>' for old, in predecessors)
            for agent, run, sha, summary, tests, key, work_url, created in reports:
                freshness = "Reviewed snapshot" if sha == head else "Different revision"
                if sha != live_head:
                    freshness += " · source branch has changed"
                work = escape(key)
                if work_url:
                    work = f'<a href="{escape(work_url)}">{work or "Linked work item"}</a>'
                report_items.append(f'''<li><strong>{escape(agent)}</strong> · {escape(run)}
<small>{escape(created)} UTC · {freshness} · <code>{escape(sha[:12])}</code></small>
<p>{escape(summary)}</p><strong>Testing</strong><p>{escape(tests)}</p>{work}</li>''')
            file_panel = file_review.panel(repo, number, base, head, fields, reviewed_files, state == "open",
                                           url + "&" + urlencode({"pr": number}),
                                           posted if form_error and posted.get("action") == ["add-anchored-task"] else None, form_error)
            detail = f'''<article class="review-detail">
<div class="pr-heading"><span class="review-state">{state.title()}</span><h1>{escape(title)} <small>#{number}</small></h1></div>
<p class="branch-pair"><code>{escape(source)}</code> → <code>{escape(target)}</code></p>
{review_brief.panel(slug,number,title,base,head,origin,stale,count,state)}
<section class="merge-readiness" aria-label="Review readiness"><h3>{decision}</h3>

{freshness_action}
<form {closed} method="post" action="{link}"><button name="action" value="merge" {merge_disabled}>Merge pull request</button>
</form><details {closed} class="pr-more"><summary>More actions</summary><form method="post" action="{link}"><button class="secondary" name="action" value="decline">Decline pull request</button></form></details></section>
<nav class="pr-sections" aria-label="Pull request sections"><a href="#changed-files">Files</a><a href="#review-tasks">Tasks <span>{count} open</span></a><a href="#review-details">Review details</a></nav>
<section data-pr-panel="files" aria-label="Files">
{file_panel}{full_diff}</section>
<section data-pr-panel="tasks" aria-labelledby="review-tasks"><h3 id="review-tasks">Review tasks</h3>
<ul class="record-list">{"".join(items) or "<li>No review tasks yet.</li>"}</ul>
<form {closed} method="post" action="{link}"><input type="hidden" name="action" value="add-task">
<input type="hidden" name="token" value="{uuid4().hex}"><input aria-label="Review task" name="note" placeholder="What needs a follow-up?" maxlength="500" required>
<button>Add task</button></form></section>
<section data-pr-panel="details" id="review-details" aria-label="Review details">{people_panel}
<h3>Review snapshot</h3><p class="snapshot-caption">{escape(source)} <code>{head[:8]}</code> compared with {escape(target)} <code>{base[:8]}</code>.</p>{previous}
<h3 id="agent-reports">Agent reports</h3><ul class="record-list agent-reports">{"".join(report_items) or "<li>No agent reports attached.</li>"}</ul>
<details><summary>Attach an agent report</summary><p>Run the local reporting command from the WeftGit checkout. Use a unique run ID and describe the tests performed.</p>
<pre tabindex="0" aria-label="Agent report command">python3 agent_report.py --repo {escape(slug)} --pr {number} --sha {head} --agent my-agent --run review-1 --summary "Review findings" --tests "Test results"</pre></details></section>
</article>'''
    history = "".join(releases.history_row(repo, slug, env, oid, created) for env, oid, created in deployments) if view == "deploys" else ""
    action_url = escape(url)
    tabs = []
    for key, label in (("source", "Code"), ("pulls", "Pull requests"), ("deploys", "Deployments")):
        current = ' aria-current="page"' if key == view else ''
        tabs.append(f'<a{current} href="{action_url}&amp;view={key}">{label}</a>')
    target_branch = "main" if "main" in branches else next(iter(branches), "")
    source_branch = ref if ref != target_branch else next((b for b in branches if b != target_branch), ref)
    project, name = slug.split("/")
    if selected_path and view == "source" and not preview:
        preview = "<p>This file is unavailable on the selected branch.</p>"
    pr_path = f'<span aria-hidden="true">/</span><a href="{action_url}&amp;view=pulls">All pull requests</a>' if detail else ""
    repo_title = f'<a href="{action_url}">{escape(name)}</a>' if detail else f'<h1>{escape(name)}</h1>'
    source_heading = escape(selected_path) if selected_path else "Repository files"
    source_content = (f'<a href="{action_url}">← Repository files</a><h2>{source_heading}</h2>{preview}'
                      if selected_path else '<ul class="record-list">' + (''.join(files) or '<li>No files yet. Push a commit to browse source.</li>') + '</ul>')
    if not branches:
        source_content = f'''<p>Add a README to describe your project, or push your existing code with Git.</p>
<form class="starter-readme" action="/hosted?{urlencode({"repo":slug})}" method="post">
<input type="hidden" name="action" value="start-readme"><label>Project description
<textarea name="description" maxlength="500" rows="3" required>{escape(draft_description)}</textarea></label>
<button>Create README.md</button></form>
<details class="existing-project"><summary>Push an existing Git project</summary>
<p>From your project directory, publish your current commit as the main branch.</p>
<pre tabindex="0" aria-label="Git push command">git push http://127.0.0.1:8091/git/{escape(slug)}.git HEAD:refs/heads/main</pre></details>'''
    if starter_error:
        source_content = f'<p class="form-error" role="alert">{escape(starter_error)}</p>' + source_content
    new_pr_open = 'open' if fields.get('new') == ['1'] or form_error or (not prs and len(branches) > 1) else ''
    default_panel = "tasks" if posted.get("action") in (["add-task"], ["set-task"]) else "details" if posted.get("action") in (["signoff"], ["undo-signoff"], ["assign"], ["unassign"]) else "files"
    notice_link = '<a href="#review-tasks">View tasks</a>' if posted.get("action") == ["add-anchored-task"] else ""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(slug)} · WeftGit</title><link rel="stylesheet" href="/reader.css"><link rel="stylesheet" href="/pipelines.css">
<link rel="stylesheet" href="/hosted.css"><script src="/hosted.js" defer></script><script src="/identity.js" defer></script><script src="/browser-storage.js" defer></script><script src="/file-filter.js" defer></script><body class="product hosted" data-default-panel="{default_panel}"><header class="app-bar"><a class="brand" href="/">WeftGit</a><nav aria-label="Main navigation"><a href="/#reviews">Pull requests</a><a href="/#repositories">Repositories</a><a href="/#local-deployments">Deployments</a><a href="/#pipelines">Pipelines</a><a href="/users">Users</a></nav></header>
<main class="repository-page"><div class="repository-heading"><div class="repo-path"><span>{escape(project)}</span><span aria-hidden="true">/</span>{repo_title}{pr_path}</div><div id="local-identity"></div></div>
<nav class="repo-tabs" aria-label="Repository">{''.join(tabs)}</nav>
{f'<p class="result" role="status">{escape(notice)} {notice_link}</p>' if notice else ''}
<section class="repo-panel" {"hidden" if view != "source" else ""}>
<div class="code-toolbar"><form action="/hosted"><input type="hidden" name="repo" value="{escape(slug)}">
<label>Branch<select name="ref" aria-label="Branch">{options(branches, ref)}</select></label><button {"disabled" if not branches else ""}>Browse branch</button></form>
<details><summary>Clone repository</summary><label>Clone command<input readonly value="git clone http://127.0.0.1:8091/git/{escape(slug)}.git"></label></details></div>
{"" if selected_path else "<h2>Repository files</h2>"}{source_content}
</section><section class="repo-panel" {"hidden" if view != "pulls" else ""}>
{f'<p class="form-error" role="alert">{escape(form_error)}</p>' if form_error and detail and posted.get('action') != ['add-anchored-task'] else ''}
{detail}
<div {"hidden" if detail else ""}>
<h2>Pull requests</h2>
<details class="new-pr" {new_pr_open}><summary>New pull request</summary>
{f'<p class="form-error" role="alert">{escape(form_error)}</p>' if form_error else ''}
<form method="post" action="{action_url}"><input type="hidden" name="action" value="pr">
<input name="title" aria-label="Pull request title" placeholder="Pull request title" value="{escape(pr_form.get("title", ""))}" required>
<label>Source branch<select name="source">{options(branches, pr_form.get("source", source_branch))}</select></label>
<label>Target branch<select name="target">{options(branches, pr_form.get("target", target_branch))}</select></label>
<button {"disabled" if len(branches) < 2 else ""}>Open pull request</button></form>
{('<p>Push a second branch to open a pull request.</p>' if len(branches) < 2 else '')}
</details>
<div class="list-filters" id="pr-filter" hidden aria-label="Filter pull requests"><button type="button" data-state="open" aria-pressed="true">Open</button><button type="button" data-state="closed" aria-pressed="false">Closed</button><button type="button" data-state="all" aria-pressed="false">All</button></div>
<ul class="record-list" id="pr-list">{''.join(requests) or '<li>No pull requests yet.</li>'}</ul></div>
</section><section class="repo-panel" {"hidden" if view != "deploys" else ""}>
<h2>Publish a local snapshot</h2>
<p>{("Package " + escape(ref) + " at " + commit[:8] + " as a ZIP. A prebuilt index.html also enables a static site preview.") if commit else "Push a commit to publish your first deployment."}</p>
{f'<p class="form-error" role="alert">{escape(form_error)}</p>' if form_error and view == 'deploys' else ''}
<form method="get" action="/hosted"><input type="hidden" name="repo" value="{escape(slug)}"><input type="hidden" name="view" value="deploys">
<input type="hidden" name="sha" value="{commit}">
<label>Deployment branch<select name="ref" {"disabled" if not branches else ""}>{options(branches, ref)}</select></label>
<label>Environment<select name="environment">{options(releases.ENVIRONMENTS, environment)}</select></label>
<button {"disabled" if not branches else ""}>Load revision</button>
<button name="action" value="deploy" formmethod="post" formaction="{action_url}" {"disabled" if not commit else ""}>Publish snapshot</button></form><h2>Deployment history</h2><ul class="record-list">{history or "<li>No snapshots published.</li>"}</ul>
</section></main><footer class="host-footer"><a href="/guide">Field guide</a><a href="http://127.0.0.1:8090/#graph">WeftTrack ↗</a></footer></body></html>"""
