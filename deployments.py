"""Publish immutable Git archives and stream their prebuilt static contents."""
from repository_store import database_path
from contextlib import closing
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote, unquote
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import zipfile


ENVIRONMENTS = ("staging", "pre-prod", "prod")
REPOSITORY = r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}"
REVISION = r"(?:[a-f0-9]{40}|[a-f0-9]{64})"
SITE_ROOTS = ("", "dist/", "build/", "public/", "docs/")
SITE_TYPES = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".avif": "image/avif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf",
}


def archive_path(repo, env, sha):
    if env not in ENVIRONMENTS or not re.fullmatch(REVISION, sha):
        raise ValueError("Invalid deployment reference")
    folder = repo / "deployments"
    result = folder / env / (sha + ".zip")
    if any(p.is_symlink() for p in (repo.parent, repo, folder, result.parent, result)):
        raise ValueError("Unavailable deployment path")
    return result


def regular(info):
    mode = stat.S_IFMT(info.external_attr >> 16)
    return not info.is_dir() and mode in (0, stat.S_IFREG)


def site_root(archive):
    for prefix in SITE_ROOTS:
        try:
            if regular(archive.getinfo(prefix + "index.html")):
                return prefix
        except KeyError:
            continue
    return None


def summary(repo, slug, env, sha, *, verify_revision=False):
    unavailable = dict(available=False, site=None, archive=None)
    try:
        packaged = archive_path(repo, env, sha)
        with zipfile.ZipFile(packaged) as archive:
            # git archive embeds the source commit in the ZIP comment.
            if verify_revision and archive.comment != sha.encode("ascii"):
                return unavailable
            root = site_root(archive)
    except (TypeError, ValueError, OSError, zipfile.BadZipFile):
        return unavailable
    identifier = f"{quote(slug, safe='/')}/{env}/{sha}"
    return dict(available=True, site=f"/sites/{identifier}/" if root is not None else None,
                archive=f"/artifacts/{identifier}.zip")


def prepare_receipts(db):
    if "source_ref" not in {c[1] for c in db.execute("PRAGMA table_info(deployments)")}:
        db.execute("ALTER TABLE deployments ADD COLUMN source_ref TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS merge_publication_order (
        pr INTEGER PRIMARY KEY, prior_deployment INTEGER NOT NULL)""")


def note_merge(db, number):
    db.execute("""INSERT OR IGNORE INTO merge_publication_order
                  SELECT ?, COALESCE(MAX(rowid), 0) FROM deployments""", (number,))


def publish(db, repo, env, sha, source_ref=None):
    destination = archive_path(repo, env, sha)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Build alongside its destination, then publish a complete ZIP with one rename.
    if not destination.exists() or not zipfile.is_zipfile(destination):
        with NamedTemporaryFile(dir=destination.parent, prefix=".publish-", delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            subprocess.run(["git", f"--git-dir={repo}", "archive", "--format=zip",
                            f"--output={temp_path}", sha], check=True, capture_output=True, timeout=60)
            os.replace(temp_path, destination)
        finally:
            temp_path.unlink(missing_ok=True)
    # Pin the source commit so later branch deletion and Git GC preserve evidence.
    subprocess.run(["git", f"--git-dir={repo}", "update-ref", f"refs/weftgit/deployments/{sha}", sha],
                   check=True, capture_output=True, timeout=10)
    previous = db.execute("SELECT rowid,commit_id,source_ref FROM deployments WHERE environment=? ORDER BY rowid DESC LIMIT 1",
                          (env,)).fetchone()
    last_merge_boundary = db.execute("SELECT COALESCE(MAX(prior_deployment),0) FROM merge_publication_order").fetchone()[0]
    if not previous or previous[1:] != (sha, source_ref) or previous[0] <= last_merge_boundary:
        db.execute("INSERT INTO deployments(environment,commit_id,source_ref) VALUES(?,?,?)", (env, sha, source_ref))


def history_row(repo, slug, env, sha, created):
    info = summary(repo, slug, env, sha)
    links = []
    if info["site"]:
        links.append(f'<a href="{escape(info["site"])}" target="_blank" rel="noopener noreferrer">Open site</a>')
    if info["archive"]:
        links.append(f'<a href="{escape(info["archive"])}" download>Download ZIP</a>')
    state = "" if info["site"] else ("Archive only" if info["available"] else "Archive unavailable")
    return (f'<li class="review-row"><div><strong>{escape(env)}</strong> · <code>{escape(sha[:8])}</code>'
            f'<small>{escape(created)} UTC{(" · " + state) if state else ""}</small></div><div class="review-jumps">{" ".join(links)}</div></li>')


def serve(handler, root, path):
    """Serve only registered releases. The site reader never extracts a ZIP entry."""
    match = re.fullmatch(rf"/(artifacts|sites)/({REPOSITORY}/{REPOSITORY})/(staging|pre-prod|prod)/({REVISION})(.*)", path)
    if not match:
        handler.send_error(404, "Deployment not found")
        return
    kind, slug, env, sha, suffix = match.groups()
    repo = root / (slug + ".git")
    started = False
    try:
        packaged = archive_path(repo, env, sha)
        database = database_path(repo)
        if database.is_symlink() or not database.is_file() or not packaged.is_file():
            raise ValueError()
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            if not db.execute("SELECT 1 FROM deployments WHERE environment=? AND commit_id=? LIMIT 1", (env,sha)).fetchone():
                raise ValueError()
        if kind == "artifacts":
            if suffix != ".zip":
                raise ValueError()
            with packaged.open("rb") as stream:
                headers(handler, "application/zip", os.fstat(stream.fileno()).st_size,
                        filename=f'{repo.stem}-{env}-{sha[:12]}.zip')
                started = True
                shutil.copyfileobj(stream, handler.wfile)
            return
        if not suffix.startswith("/"):
            raise ValueError()
        segments = suffix[1:].split("/")
        if segments[-1] == "":
            segments[-1] = "index.html"
        parts = [unquote(segment, errors="strict") for segment in segments]
        if any(not part or part in (".","..") or "/" in part or "\\" in part or "\x00" in part for part in parts):
            raise ValueError()
        mime = SITE_TYPES.get(Path(parts[-1]).suffix.lower())
        if not mime:
            raise ValueError()
        with zipfile.ZipFile(packaged) as archive:
            prefix = site_root(archive)
            if prefix is None:
                raise ValueError()
            entry = archive.getinfo(prefix + "/".join(parts))
            if not regular(entry):
                raise ValueError()
            with archive.open(entry) as stream:
                headers(handler, mime, entry.file_size, site=f"http://127.0.0.1:8091/sites/{slug}/{env}/{sha}/")
                started = True
                shutil.copyfileobj(stream, handler.wfile)
    except (ValueError, OSError, KeyError, sqlite3.Error, zipfile.BadZipFile):
        if started:
            handler.close_connection = True
        else:
            handler.send_error(404, "Deployment file unavailable")


def headers(handler, mime, size, filename=None, site=None):
    handler.send_response(200)
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(size))
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Cross-Origin-Opener-Policy", "same-origin")
    if filename:
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    if site:
        handler.send_header("Content-Security-Policy",
                            "sandbox; default-src 'none'; base-uri 'none'; form-action 'none'; "
                            f"style-src 'unsafe-inline' {site}; img-src data: {site}; font-src data: {site}")
    handler.end_headers()
