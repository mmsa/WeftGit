from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from contextlib import closing
import sqlite3

import local_users
from hosted import page


class UserDirectoryTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def call(self, action, token="", **values):
        return local_users.change(self.root,token,dict(action=action,**values))

    def setup_owner(self):
        return self.call("setup",handle="owner",name="Local owner",password="example-owner-pass")

    def test_first_owner_and_role_boundaries(self):
        def bootstrap(_):
            try:
                return self.setup_owner()
            except local_users.Denied:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            tokens = list(executor.map(bootstrap,range(2)))
        self.assertEqual(sum(bool(t) for t in tokens),1)
        owner = next(t for t in tokens if t)
        for fake_token in ("","owner","weftgit_actor_8091=owner"):
            with self.assertRaises(local_users.Denied):
                self.call("add",fake_token,handle="fake",name="Fake",password="long-password")
        self.call("add",owner,handle="ADMIN",name="Office admin",role="admin",password="long-password")
        admin = self.call("login",handle="admin",password="long-password")
        self.call("add",admin,handle="sam",name="Sam",role="member",password="member-password")
        sam = self.call("login",handle="sam",password="member-password")
        with self.assertRaises(local_users.Denied):
            self.call("add",sam,handle="other",name="Other",password="long-password")
        with self.assertRaises(local_users.Denied):
            self.call("edit",admin,handle="sam",name="Sam",role="owner",password="")
        with self.assertRaises(local_users.Denied):
            self.call("deactivate",admin,handle="owner")
        with self.assertRaisesRegex(ValueError,"at least one"):
            self.call("deactivate",owner,handle="owner")
        self.call("edit",admin,handle="sam",name="Sam Rivera",role="member",password="")
        self.assertEqual(local_users.state(self.root,sam)[1]["name"],"Sam Rivera")
        self.call("edit",owner,handle="sam",name="Sam Rivera",role="member",password="replacement-pass")
        self.assertIsNone(local_users.state(self.root,sam)[1])
        sam = self.call("login",handle="sam",password="replacement-pass")
        self.call("deactivate",admin,handle="sam")
        self.assertIsNone(local_users.state(self.root,sam)[1])
        with self.assertRaises(local_users.Denied):
            self.call("login",handle="sam",password="replacement-pass")
        self.call("activate",admin,handle="sam")
        self.assertIsNone(local_users.state(self.root,sam)[1])
        signed_in = self.call("login",handle="sam",password="replacement-pass")
        self.call("logout",signed_in)
        self.assertIsNone(local_users.state(self.root,signed_in)[1])

    def test_import_historical_handles_even_with_a_bad_repository(self):
        for name in ("broken","existing"):
            (self.root/"team"/(name+".git")).mkdir(parents=True)
        (self.root/"team/broken.git/weftgit.sqlite3").write_bytes(b"broken")
        with closing(sqlite3.connect(self.root/"team/existing.git/freethegit.sqlite3")) as db, db:
            db.execute("CREATE TABLE pr_authors(pr INTEGER PRIMARY KEY, person TEXT NOT NULL)")
            db.execute("INSERT INTO pr_authors VALUES(1,'legacy')")
            db.execute("CREATE TABLE pr_signoffs(pr INTEGER, legacy_column TEXT)")
        owner = self.setup_owner()
        self.assertIn("@legacy · Member · Inactive",local_users.page(self.root,owner))
        with self.assertRaisesRegex(ValueError,"Set a password"):
            self.call("activate",owner,handle="legacy")
        self.call("edit",owner,handle="legacy",name="Legacy Name",role="member",password="legacy-password")
        self.call("activate",owner,handle="legacy")
        token=self.call("login",handle="legacy",password="legacy-password")
        with local_users.reviewer_context(self.root,token,True) as (actor,active):
            self.assertEqual((actor,active["legacy"]),("legacy","Legacy Name"))

    def test_reviewer_assignment_rejects_inactive_and_unknown_handles(self):
        from test_hosted import ReviewLifecycleTests
        fixture=ReviewLifecycleTests(methodName="test_decline_is_terminal")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        active={"owner":"Local owner","sam":"Sam"}
        anonymous=page(fixture.root,fixture.query,None,"",active)
        from urllib.parse import urlencode
        expected = "/users?" + urlencode({"return":"/hosted?repo=test%2Fbilling&ref=main&pr=1#review-details"})
        self.assertIn(expected,anonymous)
        def assign(person,people):
            return page(fixture.root,fixture.query,f"action=assign&actor=owner&person={person}","owner",people)
        self.assertIn("Choose an active user",assign("unknown",active))
        self.assertIn("Assigned @sam",assign("sam",active))
        self.assertIn("Choose an active user",assign("sam",{"owner":"Local owner"}))
        self.assertIn("@sam · Awaiting review",page(fixture.root,fixture.query,None,"owner",{"owner":"Local owner"}))
        self.assertIn("Choose a local reviewer identity first",page(fixture.root,fixture.query,
                      "action=signoff&actor=sam","sam",{"owner":"Local owner"}))
