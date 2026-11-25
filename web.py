"""Serve the field guide and the WeftTrack example on loopback."""
from repository_store import database_path

from contextlib import closing
from html import escape
from io import BytesIO
from http.cookies import CookieError, SimpleCookie
import deployments
import release_receipts
import local_users
import reviewers
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

from fieldguide import ROOT, load_index
from hosted import page as hosted_page

WEFTTRACK = "http://127.0.0.1:8090"
GUIDES = {"repo-host": "Repository Host", "work-tracker": "Work Tracker"}
INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*")


def inline(value):
    parts = []
    end = 0
    for match in INLINE.finditer(value):
        parts.append(escape(value[end:match.start()]))
        tag = "code" if match.group(1) else "strong"
        parts.append(f"<{tag}>{escape(match.group(1) or match.group(2))}</{tag}>")
        end = match.end()
    return "".join(parts) + escape(value[end:])


def render_markdown(body):
    """Render the block styles used in the bundled guides."""
    output = []
    for index, section in enumerate(re.split(r"^```[^\n]*\n|^```$", body, flags=re.M)):
        if index % 2:
            output.append(f"<pre><code>{escape(section.strip(chr(10)))}</code></pre>")
            continue
        for block in re.split(r"\n\s*\n", section.strip()):
            lines = block.splitlines()
            if not lines:
                continue
            if all(line.startswith("|") for line in lines) and len(lines) >= 2:
                def row(line, cell):
                    cells = "".join(f"<{cell}>{inline(value.strip())}</{cell}>"
                                    for value in line.strip("|").split("|"))
                    return f"<tr>{cells}</tr>"

                output.append('<div class="table-wrap"><table><thead>' + row(lines[0], "th") +
                              "</thead><tbody>" + "".join(row(line, "td") for line in lines[2:]) +
                              "</tbody></table></div>")
                continue
            active_list = None
            paragraph = []

            def flush():
                if paragraph:
                    output.append(f"<p>{inline(' '.join(paragraph))}</p>")
                    paragraph.clear()

            for line in lines:
                item = re.match(r"^(-|\d+\.) (.*)", line)
                tag = ("ul" if item[1] == "-" else "ol") if item else None
                if tag != active_list:
                    flush()
                    if active_list:
                        output.append(f"</{active_list}>")
                    if tag:
                        output.append(f"<{tag}>")
                    active_list = tag
                if item:
                    output.append(f"<li>{inline(item[2])}</li>")
                elif line.startswith("### "):
                    flush()
                    output.append(f"<h2>{inline(line[4:])}</h2>")
                else:
                    paragraph.append(line)
            flush()
            if active_list:
                output.append(f"</{active_list}>")
    return "".join(output)


def chapter_link(identifier, query=""):
    params = {"chapter": identifier}
    if query:
        params["q"] = query
    return escape("/guide?" + urlencode(params), quote=True)


def wefttrack_panel():
    try:
        with urlopen(f"{WEFTTRACK}/example/graph", timeout=1) as response:
            graph = json.load(response)
        nodes = {node["id"]: node for node in graph["nodes"]}
        links = []
        for edge in graph["edges"]:
            if edge["kind"] != "implements":
                continue
            change = nodes[edge["source"]]
            work = nodes[edge["target"]]
            target = escape(f"{WEFTTRACK}/?" + urlencode({"workspace": "example", "item": work["id"]}) + "#graph", quote=True)
            links.append(f'<li><span>{escape(change["external_id"])} · '
                         f'{escape(change["repository"])}</span>'
                         f'<a href="{target}">{escape(work["title"])}</a></li>')
        content = ("<ul>" + "".join(links) + "</ul>" if links else
                   "<p>The example workspace has no implementation links.</p>")
    except (OSError, ValueError, KeyError, TypeError):
        content = "<p>Start WeftTrack on port 8090 to see its example links here.</p>"
    return f'''<section class="companion"><h2>WeftTrack</h2>
    <p>Implementation links in the example workspace</p>
    {content}<a href="{WEFTTRACK}/?workspace=example#graph">Open WeftTrack <span aria-hidden="true">↗</span></a></section>'''


def reader(params):
    query = params.get("q", [""])[0].strip()
    with closing(sqlite3.connect(":memory:")) as db:
        load_index(db)
        matches = db.execute(
            "SELECT id, title, body FROM chapter "
            "WHERE instr(lower(title || ' ' || body), lower(?)) > 0 OR ? = '' ORDER BY rowid",
            (query, query),
        ).fetchall()
        identifier = params.get("chapter", [matches[0][0] if matches else ""])[0]
        chapter = db.execute("SELECT title, body FROM chapter WHERE id = ?", (identifier,)).fetchone()
        edges = db.execute(
            """SELECT e.label, c.id, c.title, e.source = ?
               FROM edge e JOIN chapter c ON c.id = CASE WHEN e.source = ? THEN e.target ELSE e.source END
               WHERE e.source = ? OR e.target = ? ORDER BY e.source, e.target""",
            (identifier,) * 4,
        ).fetchall()

    navigation = []
    for prefix, name in GUIDES.items():
        chapters = [(key, title) for key, title, _ in matches if key.startswith(prefix + "-")]
        if chapters:
            navigation.append(f"<h2>{name}</h2><ul>")
            for key, title in chapters:
                current = ' aria-current="page"' if key == identifier else ""
                navigation.append(f'<li><a{current} href="{chapter_link(key, query)}">{escape(title)}</a></li>')
            navigation.append("</ul>")

    if chapter:
        title, body = chapter
        guide = next(name for prefix, name in GUIDES.items() if identifier.startswith(prefix + "-"))
        article = f'<p class="guide">{guide}</p><h1>{escape(title)}</h1>' + render_markdown(body)
    else:
        article = "<h1>Chapter unavailable</h1>" if identifier else "<h1>No matching chapters</h1>"

    relationships = "".join(
        f'<li><span>{escape(label)} {"→" if outgoing else "←"}</span>'
        f'<a href="{chapter_link(key)}">{escape(title)}</a></li>'
        for label, key, title, outgoing in edges
    )
    related = f"<section><h2>Related chapters</h2><ul>{relationships}</ul></section>" if edges else ""
    reset = '<a class="reset" href="/guide">Clear search</a>' if query else ""
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WeftGit Field Guide</title><link rel="stylesheet" href="/reader.css"><script src="/navigation.js" defer></script></head>
<body><header class="app-bar"><a class="brand" href="/">WeftGit</a><nav aria-label="Main navigation"><a  href="/">Workspace</a><a href="/#local-deployments">Local deployments</a><a href="/users">Users</a><a aria-current="page" href="/guide">Field guide</a><a href="http://127.0.0.1:8090/#graph">WeftTrack ↗</a></nav></header>
<div class="guide-search"><form action="/guide"><label for="query">Search chapters</label>
<input id="query" name="q" type="search" value="{escape(query, quote=True)}" placeholder="Search chapters">
<button>Search</button></form></div>
<div class="layout"><nav aria-label="Chapters"><details class="nav-sections" open><summary>Chapters</summary>{reset}{''.join(navigation)}</details></nav>
<main>{article}</main><aside>{related}{wefttrack_panel()}</aside></div></body></html>'''


ASSETS = {
    "/browser-storage.js": ("browser-storage.js", "text/javascript"),
    "/file-filter.js": ("file-filter.js", "text/javascript"),
    "/": ("pipelines.html", "text/html"),
    "/hosted.css": ("hosted.css", "text/css"),
    "/hosted.js": ("hosted.js", "text/javascript"),
    "/users.css": ("users.css", "text/css"),
    "/identity.js": ("identity.js", "text/javascript"),
    "/navigation.js": ("navigation.js", "text/javascript"),
    "/pipelines.js": ("pipelines.js", "text/javascript"),
    "/pipelines.css": ("pipelines.css", "text/css"),
    "/reader.css": ("reader.css", "text/css"),
}


GIT_ROOT = ROOT / ".repositories"
REPOSITORY = r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}"
GIT_ROUTE = re.compile(rf"/git/({REPOSITORY}/{REPOSITORY}\.git)/(info/refs|git-upload-pack|git-receive-pack)")


def repository_catalog(root, actor=""):
    repositories = []
    for repo in sorted(root.glob("*/*.git")):
        if repo.is_symlink() or repo.parent.is_symlink() or not (repo / "HEAD").is_file():
            continue
        if not all(re.fullmatch(REPOSITORY, part) for part in (repo.parent.name, repo.stem)):
            continue
        entry = {"name": repo.parent.name + "/" + repo.stem, "reviews": []}
        database = database_path(repo)
        if database.is_file() and not database.is_symlink():
            try:
                with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deployments'").fetchone():
                        releases = db.execute("""SELECT environment, commit_id, created FROM deployments
                            WHERE rowid IN (SELECT max(rowid) FROM deployments GROUP BY environment)
                            ORDER BY rowid DESC""")
                        entry["deployments"] = [
                            {"environment": env, "commit": sha, "created": created,
                             **deployments.summary(repo, entry["name"], env, sha)}
                            for env, sha, created in releases
                        ]
                    columns = {row[1] for row in db.execute("PRAGMA table_info(prs)")}
                    if columns:
                        has_history = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_history'").fetchone()
                        superseded = "EXISTS (SELECT 1 FROM review_history WHERE previous=prs.rowid)" if has_history else "0"
                        state = "state" if "state" in columns else "'open'"
                        commits = "head,base" if {"head","base"} <= columns else "NULL,NULL"
                        rows = [dict(id=i,title=t,source=s,target=d,state=status,head=h,base=b,superseded=bool(old))
                                for i,t,s,d,status,h,b,old in db.execute(
                                    f"SELECT rowid,title,source,target,{state},{commits},{superseded} FROM prs ORDER BY rowid DESC")]
                        if actor:
                            reviewers.inbox_metadata(db, repo, actor, rows)
                            entry["history"] = [row for row in rows if (row["reviewed"] or row["authored"])
                                                and (row["state"] != "open" or row["superseded"])]
                        entry["reviews"] = [row for row in rows if row["state"] == "open" and not row["superseded"]]
            except (sqlite3.Error, subprocess.SubprocessError, OSError):
                entry["unavailable"] = True
        repositories.append(entry)
    return repositories


def create_review_example(repo):
    """Populate a newly created repository with a small, reviewable change."""
    with TemporaryDirectory() as checkout:
        def run(*args):
            subprocess.run(["git", "-C", checkout, "-c", "user.name=WeftGit example",
                            "-c", "user.email=example@localhost", *args],
                           check=True, capture_output=True, timeout=10)
        run("init", "--initial-branch=main")
        source = Path(checkout) / "exports.py"
        source.write_text('COLUMNS = ["invoice", "total"]\n')
        run("add", "exports.py")
        run("commit", "-m", "Define billing export columns")
        run("switch", "-c", "include-currency")
        source.write_text('COLUMNS = ["invoice", "currency", "total"]\n')
        run("commit", "-am", "Include currency in billing exports")
        run("push", str(repo), "main", "include-currency")


class ReaderHandler(BaseHTTPRequestHandler):
    def cookie(self, prefix):
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
            port = str(self.server.server_port)
            value = cookies.get(prefix + port) or cookies.get(prefix.replace("weftgit_", "freethegit_", 1) + port)
            return value.value if value else ""
        except CookieError:
            return ""

    def legacy_actor(self):
        try:
            name = self.cookie("weftgit_actor_")
            return reviewers.handle(name) if name else ""
        except ValueError:
            return ""

    def local_actor(self):
        configured, actor = local_users.state(GIT_ROOT,self.cookie("weftgit_session_"))
        return (actor["handle"] if actor else "") if configured else self.legacy_actor()

    def user_page(self, params, data=None):
        token = self.cookie("weftgit_session_")
        return_to = local_users.safe_return(params.get("return","/users"))
        error, status = "", 200
        if data is not None:
            try:
                new_token = local_users.change(GIT_ROOT,token,data)
                dest = return_to if data.get("action") in ("login","setup","logout") else "/users"
                self.send_response(303)
                self.send_header("Location",dest)
                self.send_header("Content-Length","0")
                self.send_header("Cache-Control","no-store")
                if new_token is not None:
                    age = local_users.SESSION_SECONDS if new_token else 0
                    self.send_header("Set-Cookie", f"weftgit_session_{self.server.server_port}={new_token}; Path=/; SameSite=Strict; HttpOnly; Max-Age={age}")
                    self.send_header("Set-Cookie", f"freethegit_session_{self.server.server_port}=; Path=/; SameSite=Strict; HttpOnly; Max-Age=0")
                self.end_headers()
                return
            except local_users.Denied as problem:
                error, status = str(problem),403
            except ValueError as problem:
                error, status = str(problem),400
        payload = local_users.page(GIT_ROOT,token,return_to,error,data).encode()
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        url = urlsplit(self.path)
        form_limits = {"/users": 8192, "/identity": 1024, "/repositories": 4096, "/hosted": 8192}
        if url.path in form_limits:
            if self.headers.get("Origin") != "http://127.0.0.1:8091":
                self.send_error(403)
                return
            body = self.read_form(form_limits[url.path])
            if body is None:
                return
        if url.path == "/users":
            data = {k:v[0] for k,v in parse_qs(body,keep_blank_values=True).items()}
            self.user_page(data,data)
            return
        if url.path == "/identity":
            fields = parse_qs(body, keep_blank_values=True)
            if "person" not in fields:
                self.send_error(400, "Expected a local reviewer handle")
                return
            value = fields["person"][0]
            try:
                actor = reviewers.handle(value) if value.strip() else ""
            except ValueError as error:
                self.send_error(400, str(error))
                return
            if local_users.state(GIT_ROOT)[0]:
                self.send_error(403, "Sign in through Users to change reviewer identity.")
                return
            self.send_response(204)
            age = 31536000 if actor else 0
            self.send_header("Set-Cookie", f"weftgit_actor_{self.server.server_port}={actor}; Path=/; SameSite=Strict; HttpOnly; Max-Age={age}")
            self.send_header("Set-Cookie", f"freethegit_actor_{self.server.server_port}=; Path=/; SameSite=Strict; HttpOnly; Max-Age=0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if url.path == "/hosted":
            self.serve_hosted(url.query, body)
        elif url.path == "/repositories":
            fields = parse_qs(body)
            project = fields.get("project", [""])[0]
            name = fields.get("name", [""])[0]
            if not all(re.fullmatch(REPOSITORY, part) for part in (project, name)):
                self.send_error(400, "Use letters, numbers, hyphens or underscores in repository slugs")
                return
            repo = GIT_ROOT / project / f"{name}.git"
            try:
                if repo.parent.is_symlink():
                    self.send_error(400, "Unavailable repository project")
                    return
                repo.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=repo.parent) as staging_dir:
                    staging = Path(staging_dir)
                    candidate = staging / project / repo.name
                    candidate.mkdir(parents=True)
                    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(candidate)],
                                   check=True, capture_output=True)
                    if fields.get("example") == ["review"]:
                        create_review_example(candidate)
                        hosted_page(staging, urlencode({"repo": f"{project}/{name}"}),
                                    urlencode({"action": "pr", "title": "Include currency in billing exports",
                                               "source": "include-currency", "target": "main"}), self.local_actor())
                    if repo.exists():
                        raise FileExistsError()
                    try:
                        candidate.rename(repo)
                    except OSError:
                        if repo.exists():
                            raise FileExistsError() from None
                        raise
            except FileExistsError:
                self.send_error(409, "Repository already exists")
                return
            except (OSError, ValueError, subprocess.SubprocessError, sqlite3.Error):
                self.send_error(500, "Could not initialize the repository")
                return
            payload = json.dumps({"url": f"http://127.0.0.1:8091/git/{project}/{name}.git"}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif not self.serve_git(url):
            self.send_error(404)

    def read_form(self, limit):
        length = self.headers.get("Content-Length")
        if length is None:
            self.send_error(411, "Form length required")
            return None
        if not length.isascii() or not length.isdecimal():
            self.send_error(400, "Invalid form length")
            return None
        if len(length) > 10 or int(length) > limit:
            self.send_error(413, "Form exceeds size limit")
            return None
        data = self.rfile.read(int(length))
        if len(data) != int(length):
            self.send_error(400, "Incomplete form")
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            self.send_error(400, "Form must be UTF-8")
            return None

    def read_git_body(self, limit):
        """Git streams larger pushes with HTTP/1.1 chunked transfer encoding."""
        encoding = self.headers.get("Transfer-Encoding", "").lower()
        length = self.headers.get("Content-Length", "")
        if encoding and (encoding != "chunked" or length):
            self.send_error(400, "Unsupported Git request framing")
            return None
        if encoding == "chunked":
            output = BytesIO()
            while True:
                line = self.rfile.readline(1024)
                if not line.endswith(b"\r\n"):
                    break
                value = line[:-2].split(b";", 1)[0]
                if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", value):
                    break
                size = int(value, 16)
                if size + output.tell() > limit:
                    self.send_error(413, "Git request exceeds 64 MiB")
                    return None
                if not size:
                    trailer_bytes = 0
                    while trailer_bytes < 8192:
                        trailer = self.rfile.readline(1024)
                        if trailer == b"\r\n":
                            return output.getvalue()
                        if not trailer.endswith(b"\r\n"):
                            break
                        trailer_bytes += len(trailer)
                    break
                chunk = self.rfile.read(size)
                if len(chunk) != size or self.rfile.read(2) != b"\r\n":
                    break
                output.write(chunk)
            self.send_error(400, "Incomplete chunked Git request")
            return None
        if not length.isascii() or not length.isdecimal():
            self.send_error(411, "Git request length required")
            return None
        if len(length) > 10 or int(length) > limit:
            self.send_error(413, "Git request exceeds 64 MiB")
            return None
        body = self.rfile.read(int(length))
        if len(body) != int(length):
            self.send_error(400, "Incomplete Git request")
            return None
        return body

    def serve_git(self, url):
        match = GIT_ROUTE.fullmatch(url.path)
        if not match:
            return False
        repo, operation = match.groups()
        directory = GIT_ROOT / repo
        if directory.is_symlink() or directory.parent.is_symlink() or not (directory / "HEAD").is_file():
            self.send_error(404, "Repository not found")
            return True
        if (operation == "info/refs") != (self.command == "GET"):
            self.send_error(405)
            return True
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
            self.send_error(403, "Use a Git client")
            return True
        body = self.read_git_body(64 * 1024 * 1024) if self.command == "POST" else b""
        if body is None:
            return True
        env = dict(os.environ, GIT_PROJECT_ROOT=str(GIT_ROOT), GIT_HTTP_EXPORT_ALL="1",
                   GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="http.receivepack", GIT_CONFIG_VALUE_0="true",
                   GIT_CONFIG_KEY_1="transfer.hideRefs", GIT_CONFIG_VALUE_1="refs/weftgit/",
                   GIT_CONFIG_KEY_2="transfer.hideRefs", GIT_CONFIG_VALUE_2="refs/freethegit/",
                   PATH_INFO=url.path.removeprefix("/git"), QUERY_STRING=url.query,
                   REQUEST_METHOD=self.command, CONTENT_TYPE=self.headers.get("Content-Type", ""),
                   CONTENT_LENGTH=str(len(body)), HTTP_GIT_PROTOCOL=self.headers.get("Git-Protocol", ""))
        try:
            result = subprocess.run(["git", "http-backend"], input=body, env=env,
                                    capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            self.send_error(502, "Git backend unavailable")
            return True
        headers, separator, payload = result.stdout.partition(b"\r\n\r\n")
        if not separator:
            self.send_error(502, "Invalid response from Git backend")
            return True
        response_headers = dict(line.decode("latin1").split(": ", 1) for line in headers.split(b"\r\n"))
        self.send_response(int(response_headers.pop("Status", "200").split()[0]))
        for name, value in response_headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    def serve_hosted(self, query, body=None):
        try:
            with local_users.reviewer_context(GIT_ROOT,self.cookie("weftgit_session_"),body is not None) as (person,active):
                actor = self.legacy_actor() if active is None else person
                payload = hosted_page(GIT_ROOT, query, body, actor, active).encode()
        except ValueError as error:
            self.send_error(400, str(error))
            return
        except (OSError, subprocess.SubprocessError, sqlite3.Error):
            self.send_error(500, "Could not read or update hosted repository")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def serve_environment(self, path):
        match = re.fullmatch(rf"/environments/({REPOSITORY}/{REPOSITORY})/(staging|pre-prod|prod)/?", path)
        if not match:
            self.send_error(404, "Unknown local environment")
            return
        slug, env = match.groups()
        repo = GIT_ROOT / (slug + ".git")
        database = database_path(repo)
        if repo.is_symlink() or repo.parent.is_symlink() or database.is_symlink() or not database.is_file():
            self.send_error(404, "No release in this environment")
            return
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT commit_id FROM deployments WHERE environment=? ORDER BY rowid DESC LIMIT 1",
                                 (env,)).fetchone()
            site = deployments.summary(repo, slug, env, row[0])["site"] if row else None
        except sqlite3.Error:
            site = None
        if not site:
            self.send_error(404, "This environment has no published static site")
            return
        self.send_response(302)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Location", site)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/users":
            self.user_page({k:v[0] for k,v in parse_qs(url.query).items()})
            return
        if url.path.startswith("/environments/"):
            self.serve_environment(url.path)
            return
        if url.path.startswith(("/sites/", "/artifacts/")):
            deployments.serve(self, GIT_ROOT, url.path)
            return
        if url.path == "/hosted":
            self.serve_hosted(url.query)
            return
        if self.serve_git(url):
            return
        if url.path == "/api/release-receipt":
            content_type = "application/json"
            try:
                result = release_receipts.lookup(GIT_ROOT, parse_qs(url.query, keep_blank_values=True),
                                                 f"http://127.0.0.1:{self.server.server_port}")
            except ValueError as error:
                self.send_error(400, str(error))
                return
            except (OSError, sqlite3.Error, subprocess.SubprocessError):
                self.send_error(503, "Release evidence is unavailable.")
                return
            payload = json.dumps(result).encode()
        elif url.path == "/identity":
            content_type = "application/json"
            configured, actor = local_users.state(GIT_ROOT,self.cookie("weftgit_session_"))
            person = (actor["handle"] if actor else "") if configured else self.legacy_actor()
            payload = json.dumps({"person":person,"managed":configured,"name":actor["name"] if actor else ""}).encode()
        elif url.path == "/repositories":
            content_type = "application/json"
            payload = json.dumps(repository_catalog(GIT_ROOT, self.local_actor())).encode()
        elif url.path == "/guide" or (url.path == "/" and url.query):
            content_type = "text/html"
            payload = reader(parse_qs(url.query)).encode()
        elif url.path in ASSETS:
            filename, content_type = ASSETS[url.path]
            payload = (ROOT / filename).read_bytes()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        if url.path in ("/identity", "/repositories", "/api/release-receipt"):
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    with ThreadingHTTPServer(("127.0.0.1", 8091), ReaderHandler) as server:
        print("WeftGit is ready at http://127.0.0.1:8091/", flush=True)
        server.serve_forever()
