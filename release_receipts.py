"""Read-only release evidence derived from a local repository, its review and its archive."""
from contextlib import closing
import re
import sqlite3
import subprocess

import deployments
from repository_store import database_path


def git(repo, *args):
    return subprocess.run(["git", "--no-replace-objects", f"--git-dir={repo}", *args],
                          capture_output=True, timeout=10)


def ancestor(repo, earlier, later):
    return git(repo, "merge-base", "--is-ancestor", earlier, later).returncode == 0


def parse_request(params):
    if any(len(value) != 1 for value in params.values()) or set(params) not in (
            {"repo","pr"}, {"repo","pr","head"}, {"repo","commit"}):
        raise ValueError("Specify one repository and one pull request or commit.")
    slug = params["repo"][0]
    number = params.get("pr", [None])[0]
    wanted = params.get("commit", params.get("head", [None]))[0]
    if not re.fullmatch(f"{deployments.REPOSITORY}/{deployments.REPOSITORY}", slug):
        raise ValueError("Invalid repository.")
    if number is not None and not re.fullmatch(r"[1-9][0-9]{0,17}", number):
        raise ValueError("Invalid pull request number.")
    if wanted is not None and not re.fullmatch(deployments.REVISION, wanted):
        raise ValueError("Provide a full commit SHA.")
    return slug, int(number) if number else None, wanted


def lookup(root, params, origin):
    slug, number, wanted = parse_request(params)
    result = dict(verified=False, repository=slug, pull_request=number, reviewed_sha=None,
                  commit_sha=None if number else wanted, merge_sha=None, target_branch=None,
                  releases=[], reason="No publication record is available for this repository.")
    repo = root / (slug + ".git")
    path = database_path(repo)
    if repo.is_symlink() or repo.parent.is_symlink() or path.is_symlink() or not path.is_file():
        return result
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "deployments" not in tables:
            return result
        floor, revision, target = 0, wanted, None
        if number:
            result["reason"] = "This pull request has no verified merge."
            if "prs" not in tables or "state" not in {c[1] for c in db.execute("PRAGMA table_info(prs)")}:
                return result
            pr = db.execute("SELECT head,base,target,state FROM prs WHERE rowid=?", (number,)).fetchone()
            if not pr:
                return result
            head, base, target, state = pr
            result.update(reviewed_sha=head, target_branch=target)
            if wanted and head != wanted:
                result["reason"] = "The linked revision differs from the reviewed commit."
                return result
            if state != "merged" or not all(isinstance(s,str) and re.fullmatch(deployments.REVISION,s) for s in (head,base)):
                return result
            markers = []
            for namespace in ("weftgit", "freethegit"):
                ref = git(repo, "show-ref", "--verify", "--hash", f"refs/{namespace}/merges/{number}")
                if ref.returncode == 0:
                    markers.append(ref.stdout.decode().strip())
            if len(set(markers)) != 1 or not all(ancestor(repo, sha, markers[0]) for sha in (base, head)):
                return result
            revision = markers[0]
            result["merge_sha"] = revision
            result["reason"] = "No recorded publication from the merge target verifies this pull request."
            if "merge_publication_order" not in tables:
                return result
            boundary = db.execute("SELECT prior_deployment FROM merge_publication_order WHERE pr=?", (number,)).fetchone()
            if not boundary:
                return result
            floor = boundary[0]
        else:
            result["reason"] = "No verified publication contains the linked commit."
            if git(repo, "rev-parse", "--verify", wanted+"^{commit}").stdout.decode().strip() != wanted:
                return result
        columns = {c[1] for c in db.execute("PRAGMA table_info(deployments)")}
        branch_column = "source_ref" if "source_ref" in columns else "NULL"
        seen, matched = set(), set()
        for rowid, env, sha, created, branch in db.execute(
                f"SELECT rowid,environment,commit_id,created,{branch_column} FROM deployments ORDER BY rowid DESC"):
            if rowid <= floor or len(matched) == len(deployments.ENVIRONMENTS):
                break
            current = env not in seen
            seen.add(env)
            if (env in matched or (number and branch != target)
                    or env not in deployments.ENVIRONMENTS or not re.fullmatch(deployments.REVISION, sha or "")):
                continue
            if not ancestor(repo, revision, sha):
                continue
            info = deployments.summary(repo, slug, env, sha, verify_revision=True)
            if not info["available"]:
                continue
            result["releases"].append(dict(environment=env, published_sha=sha, published_at=created,
                archive_url=origin+info["archive"], site_url=origin+info["site"] if info["site"] else None,
                environment_url=f"{origin}/environments/{slug}/{env}/" if current and info["site"] else None,
                current=current))
            matched.add(env)
    if result["releases"]:
        result.update(verified=True, reason="Verified against the published Git revision.")
    return result
