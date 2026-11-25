"""Find exact WeftTrack keys in named Git branches and pull request titles."""
from contextlib import closing
import re
import sqlite3
from urllib.parse import urlencode

import deployments
import release_receipts
from repository_store import database_path


KEY_PATTERN = r"WT-(?:[0-9]{3}|[1-9][0-9]{3,17})"
KEY_TOKEN = re.compile(r"(?<!\w)" + KEY_PATTERN + r"(?!\w)", re.IGNORECASE)


def other_keys(key, *texts):
    found = {match.upper() for text in texts if isinstance(text, str) for match in KEY_TOKEN.findall(text)}
    return sorted(found - {key, "WT-000"})


def matching_branches(repo, token):
    current = release_receipts.git(repo, "for-each-ref", "--format=%(objectname) %(refname:lstrip=2)", "refs/heads")
    current.check_returncode()
    live = {}
    for row in current.stdout.decode().split("\n"):
        if not row:
            continue
        sha, name = row.split(" ", 1)
        if token.search(name):
            live[name] = sha
    return live


def candidate_reviews(repo, key):
    db_path = database_path(repo)
    if db_path.is_symlink():
        raise OSError("Repository metadata is unavailable.")
    rows = []
    if db_path.is_file():
        with closing(sqlite3.connect(db_path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
            columns = {c[1] for c in db.execute("PRAGMA table_info(prs)")}
            if columns:
                state = "state" if "state" in columns else "'open'"
                has_history = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_history'").fetchone()
                latest = "AND rowid NOT IN (SELECT previous FROM review_history)" if has_history else ""
                rows = db.execute(f"""SELECT rowid,title,source,target,head,{state} FROM prs
                    WHERE (source LIKE ? OR title LIKE ?) AND {state} != 'superseded' {latest}
                    ORDER BY rowid DESC""", (f"%{key}%", f"%{key}%")).fetchall()
    return rows


def lookup(root, params, origin):
    if "key" not in params or set(params) - {"key", "repo", "details"} or any(len(v) != 1 for v in params.values()):
        raise ValueError("Specify one task key and optionally one repository.")
    details = params.get("details", ["full"])[0]
    if details not in ("identity", "full"):
        raise ValueError("Choose identity or full details.")
    key = params["key"][0].upper()
    if not re.fullmatch(KEY_PATTERN, key) or key == "WT-000":
        raise ValueError("Use the exact WeftTrack key shown on the work item.")
    slug_filter = params.get("repo", [None])[0]
    if slug_filter is not None and not re.fullmatch(f"{deployments.REPOSITORY}/{deployments.REPOSITORY}", slug_filter):
        raise ValueError("Invalid repository.")
    token = re.compile(r"(?<!\w)" + re.escape(key) + r"(?!\w)", re.IGNORECASE)
    matches, branches = [], []
    repos = [root/(slug_filter+".git")] if slug_filter else sorted(root.glob("*/*.git"))
    for repo in repos:
        slug = repo.parent.name + "/" + repo.stem
        if (repo.parent.is_symlink() or repo.is_symlink() or not (repo/"HEAD").is_file()
                or not re.fullmatch(f"{deployments.REPOSITORY}/{deployments.REPOSITORY}", slug)):
            continue
        live = matching_branches(repo, token)
        rows = candidate_reviews(repo, key)
        for number, title, source, target, head, state in rows:
            fields = [field for field, value in (("source_branch", source), ("title", title)) if isinstance(value, str) and token.search(value)]
            if not fields:
                continue
            if source in live and live[source] == head:
                del live[source]
            verified, stage, merge = False, "unmerged", None
            if state == "merged" and details == "identity":
                verified, stage = None, "unverified"
            elif state == "merged":
                receipt = release_receipts.lookup(root, {"repo":[slug],"pr":[str(number)],"head":[head]}, origin)
                verified, stage, merge = receipt["merge_verified"], receipt["staging_status"], receipt["merge_sha"]
            url = origin + "/hosted?" + urlencode({"repo":slug, "pr":number})
            matches.append(dict(repository=slug, pull_request=number, title=title, source_branch=source,
                target_branch=target, reviewed_sha=head, state=state, matched_in=fields, review_url=url,
                conflicting_keys=other_keys(key, title, source),
                merge_verified=verified, merge_sha=merge, staging_status=stage))
        for name, head in live.items():
            url = origin + "/hosted?" + urlencode({"repo":slug, "ref":name})
            branches.append(dict(repository=slug, source_branch=name, head=head, branch_url=url,
                                 conflicting_keys=other_keys(key, name)))
    return {"key":key, "matches":matches, "branches":branches}
