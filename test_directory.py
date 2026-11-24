"""The review inbox must also work with repositories created before review states."""
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from web import repository_catalog


class DirectoryTests(unittest.TestCase):
    def test_read_only_discovery_and_state_filter(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            repo = root / "team" / "web.git"
            repo.mkdir(parents=True)
            (repo / "HEAD").write_text("ref: refs/heads/main")
            database = repo / "freethegit.sqlite3"
            self.assertEqual(repository_catalog(root), [{"name": "team/web", "reviews": []}])
            self.assertFalse(database.exists())
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE prs(title,source,target)")
                db.execute("INSERT INTO prs VALUES('New search','feature','main')")
            self.assertEqual(repository_catalog(root)[0]["reviews"][0]["title"], "New search")
            with sqlite3.connect(database) as db:
                db.execute("ALTER TABLE prs ADD state TEXT DEFAULT 'open'")
                db.execute("UPDATE prs SET state='merged'")
            self.assertEqual(repository_catalog(root)[0]["reviews"], [])
            with sqlite3.connect(database) as db:
                db.execute("UPDATE prs SET state='open'")
                db.execute("INSERT INTO prs(title,source,target) VALUES('Fresh review','feature','main')")
                db.execute("CREATE TABLE review_history(previous,successor)")
                db.execute("INSERT INTO review_history VALUES(1,2)")
            self.assertEqual([pr["title"] for pr in repository_catalog(root)[0]["reviews"]], ["Fresh review"])
