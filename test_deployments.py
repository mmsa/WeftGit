"""Exercise immutable releases and package-only HTTP views with disposable repositories."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlencode
import os
from contextlib import closing
import sqlite3
import subprocess
import unittest
import zipfile

from deployments import publish, serve, summary
from hosted import page


class Response:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers[name] = value

    def end_headers(self):
        pass

    def send_error(self, status, message):
        self.status = status


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.repo = self.root / "team" / "site.git"
        self.git("init", "-q", "--initial-branch=main")
        self.git("init", "-q", "--bare", "--initial-branch=main", str(self.repo))
        self.git("remote", "add", "origin", str(self.repo))
        self.query = "repo=team/site&view=deploys"
        self.db = self.repo / "freethegit.sqlite3"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.work), "-c", "user.name=Test",
                                       "-c", "user.email=test@localhost", *args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, filename, content):
        target = self.work / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git("add", ".")
        self.git("commit", "-qm", filename)
        self.git("push", "-q", "origin", "HEAD")
        return self.git("rev-parse", "HEAD")

    def deploy(self, sha, env="staging", ref="main"):
        return page(self.root, self.query, urlencode(dict(action="deploy", sha=sha, environment=env, ref=ref)))

    def fetch(self, path):
        result = Response()
        serve(result, self.root, path)
        return result

    def test_site_assets_archive_and_stale_revision(self):
        first = self.commit("dist/index.html", '<link rel="stylesheet" href="css/app.css">v1')
        second = self.commit("dist/css/app.css", "body {color: navy}")
        self.assertIn("revision changed", self.deploy(first))
        self.assertIn("Published", self.deploy(second))
        release = summary(self.repo, "team/site", "staging", second)
        site = release["site"]
        self.assertEqual(self.fetch(site).wfile.getvalue(), (self.work/"dist/index.html").read_bytes())
        css = self.fetch(site + "css/app.css")
        self.assertEqual(css.status, 200)
        self.assertTrue(css.headers["Content-Type"].startswith("text/css"))
        preview = self.fetch(site)
        self.assertIn("sandbox;", preview.headers["Content-Security-Policy"])
        self.assertNotIn("allow-scripts", preview.headers["Content-Security-Policy"])
        self.assertNotIn("Set-Cookie", preview.headers)
        zipped = self.fetch(release["archive"])
        self.assertIn("attachment", zipped.headers["Content-Disposition"])
        with zipfile.ZipFile(BytesIO(zipped.wfile.getvalue())) as archive:
            self.assertEqual(archive.read("dist/css/app.css"), css.wfile.getvalue())
        self.git("switch", "-qc", "next")
        third = self.commit("dist/index.html", "New source")
        form = page(self.root, self.query + "&ref=next&environment=prod")
        self.assertIn('<option selected>next</option>', form)
        self.assertIn('<option selected>prod</option>', form)
        self.assertIn(f'name="sha" value="{third}"', form)
        self.assertIn("Published", self.deploy(third, "prod", "next"))
        self.assertIn(b"v1", self.fetch(site).wfile.getvalue())

    def test_environment_tracks_latest_published_site(self):
        from web import ReaderHandler
        def current():
            result = Response()
            with patch("web.GIT_ROOT", self.root):
                ReaderHandler.serve_environment(result, "/environments/team/site/staging/")
            return result
        first = self.commit("index.html", "Revision one")
        self.assertEqual(current().status, 404)
        self.deploy(first)
        self.assertEqual(current().headers["Location"], f"/sites/team/site/staging/{first}/")
        second = self.commit("index.html", "Revision two")
        self.assertIn(first, current().headers["Location"])
        self.deploy(second)
        self.assertEqual(current().headers["Location"], f"/sites/team/site/staging/{second}/")
        self.assertEqual(current().headers["Cache-Control"], "no-store")

    def test_archive_failure_keeps_publish_form_and_allows_retry(self):
        sha = self.commit("index.html", "ready")
        with patch("hosted.releases.publish", side_effect=OSError("disk full")):
            html = self.deploy(sha, "prod")
        self.assertIn("Could not package this revision", html)
        self.assertIn('<option selected>prod</option>', html)
        self.assertIn("No snapshots published", html)
        self.assertIn("Published", self.deploy(sha, "prod"))

    def test_empty_archive_only_and_legacy_record(self):
        self.assertIn("Push a commit", page(self.root, self.query))
        sha = self.commit("src/README.md", "source only")
        # Match the archive and SQLite layout written by the previous implementation.
        artifact = self.repo / "deployments/prod" / f"{sha}.zip"
        artifact.parent.mkdir(parents=True)
        self.git("archive", "--format=zip", f"--output={artifact}", sha)
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute("INSERT INTO deployments(environment,commit_id) VALUES('prod',?)", (sha,))
        release = summary(self.repo, "team/site", "prod", sha)
        self.assertTrue(release["available"])
        self.assertIsNone(release["site"])
        self.assertEqual(self.fetch(release["archive"]).status, 200)
        self.assertIn("Archive only", page(self.root, self.query))
        artifact.unlink()
        self.assertIn("Archive unavailable", page(self.root, self.query))

    def test_packaged_files_only_and_retry_consistency(self):
        self.commit("private.txt", "unrelated")
        (self.work / "public").mkdir()
        os.symlink("../private.txt", self.work/"public"/"link.html")
        sha = self.commit("public/index.html", "site")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(self.deploy, (sha,sha)))
        release = summary(self.repo, "team/site", "staging", sha)
        for name in ("../private.txt", "%2e%2e/private.txt", "index.html%2f..", "link.html", "bad.js", ".git/config"):
            self.assertEqual(self.fetch(release["site"] + name).status, 404, name)
        with closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0], 1)
            with patch("deployments.subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
                with self.assertRaises(subprocess.CalledProcessError):
                    publish(db, self.repo, "prod", sha)
            self.assertEqual(list((self.repo / "deployments/prod").iterdir()), [])
            db.execute("DELETE FROM deployments")
        self.assertEqual(self.fetch(release["archive"]).status, 404)
        self.assertEqual(self.fetch(release["site"]).status, 404)


if __name__ == "__main__":
    unittest.main()
