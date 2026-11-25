"""Attach a local agent's review evidence to a hosted pull request."""
from repository_store import database_path
import argparse
from pathlib import Path
import re
from contextlib import closing
import sqlite3
import subprocess
from urllib.parse import urlsplit


def prepare_reports(db):
    db.execute("""CREATE TABLE IF NOT EXISTS agent_reports (
        pr INTEGER NOT NULL, agent TEXT NOT NULL, run TEXT NOT NULL, sha TEXT NOT NULL,
        summary TEXT NOT NULL, tests TEXT NOT NULL, work_item TEXT NOT NULL,
        work_url TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(pr,agent,run))""")


def record(root, repo, pr, agent, run, sha, summary, tests, work_item="", work_url=""):
    if not re.fullmatch(r"[a-zA-Z0-9][\w-]{0,63}/[a-zA-Z0-9][\w-]{0,63}", repo, re.ASCII):
        raise ValueError("Enter a hosted repository as project/name.")
    repo_path = root / (repo + ".git")
    db_path = database_path(repo_path)
    if repo_path.parent.is_symlink() or repo_path.is_symlink() or db_path.is_symlink() or not db_path.is_file():
        raise ValueError("Open an existing pull request before attaching a report.")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
        raise ValueError("Provide the complete reviewed commit SHA.")
    kind = subprocess.run(["git", f"--git-dir={repo_path}", "cat-file", "-t", sha],
                          capture_output=True, timeout=10)
    if kind.stdout.strip() != b"commit":
        raise ValueError("Reviewed commit is unavailable in this repository.")
    for label, value, limit in (("agent", agent, 100), ("run", run, 120),
                                ("summary", summary, 8000), ("tests", tests, 8000)):
        if not 1 <= len(value.strip()) <= limit or len(value) > limit:
            raise ValueError(f"Provide {label} within {limit} characters.")
    if len(work_item) > 240 or len(work_url) > 2048:
        raise ValueError("Work item reference is too long.")
    if work_url:
        target = urlsplit(work_url)
        if (target.scheme != "http" or target.netloc not in ("127.0.0.1:8090", "localhost:8090")
                or target.path not in ("", "/") or any(ord(char) < 32 for char in work_url)):
            raise ValueError("Use a local WeftTrack work item link on port 8090.")
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM prs WHERE rowid=?", (pr,)).fetchone():
            raise ValueError("Unknown pull request.")
        prepare_reports(db)
        content = (sha, summary, tests, work_item, work_url)
        previous = db.execute("SELECT sha,summary,tests,work_item,work_url FROM agent_reports WHERE pr=? AND agent=? AND run=?",
                              (pr, agent, run)).fetchone()
        if previous and previous != content:
            raise ValueError("That run already has a different report. Use a new run ID.")
        db.execute("""INSERT OR IGNORE INTO agent_reports
                   (pr,agent,run,sha,summary,tests,work_item,work_url) VALUES(?,?,?,?,?,?,?,?)""",
                   (pr, agent, run, *content))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="Hosted repository project/name")
    parser.add_argument("--pr", required=True, type=int)
    for name in ("agent", "run", "sha", "summary", "tests"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--work-item", default="")
    parser.add_argument("--work-url", default="")
    args = parser.parse_args()
    try:
        record(Path(__file__).resolve().parent / ".repositories", **vars(args))
    except (ValueError, sqlite3.Error, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Could not record agent review: {error}\n")
    print(f"Review report recorded for {args.repo} pull request #{args.pr}.")


if __name__ == "__main__":
    main()
