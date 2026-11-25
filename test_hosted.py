"""Exercise review lifecycle operations against disposable, real Git repositories."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
import sqlite3
import subprocess
import unittest
from urllib.parse import urlencode

from release_receipts import lookup
import task_reviews
import file_review
from agent_report import record
from hosted import git, page
from file_review import changed_files, file_patch
from web import create_review_example, repository_catalog


class ReviewLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "test" / "billing.git"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(self.repo)],
                       check=True, capture_output=True)
        create_review_example(self.repo)
        self.query = "repo=test/billing&pr=1"
        self.head = self.rev("include-currency")
        self.base = self.rev("main")
        # Old installations have a five-column table and pre-existing review rows.
        with closing(sqlite3.connect(self.repo / "weftgit.sqlite3")) as db, db:
            db.execute("CREATE TABLE prs(source TEXT,target TEXT,head TEXT,base TEXT,title TEXT,UNIQUE(head,base))")
            db.execute("INSERT INTO prs VALUES(?,?,?,?,?)", ("include-currency","main",self.head,self.base,"Review"))

    def rev(self, ref):
        return git(self.repo, "rev-parse", ref).decode().strip()

    def submit(self, action, **values):
        return page(self.root, self.query, urlencode(dict(action=action, **values)))

    def test_large_file_sections_and_literal_names(self):
        import tempfile
        with tempfile.TemporaryDirectory() as checkout:
            subprocess.run(["git", "clone", "-q", str(self.repo), checkout], check=True)
            # Brackets normally mean a Git pathspec pattern.
            name = "large[1].txt"
            text = "section content 0123456789\n" * 8500 + "END_OF_LARGE_PATCH\n"
            Path(checkout, name).write_text(text)
            Path(checkout, "large1.txt").write_text("OTHER_FILE\n")
            subprocess.run(["git", "-C", checkout, "add", "."], check=True)
            subprocess.run(["git", "-C", checkout, "-c", "user.name=Test", "-c", "user.email=test@example.test",
                            "commit", "-qm", "Large patch"], check=True)
            subprocess.run(["git", "-C", checkout, "push", "-q", "origin", "HEAD:refs/heads/large"], check=True)
        head = self.rev("large")
        origin, changes = changed_files(self.repo, self.base, head)
        change = next(c for c in changes.values() if c.label == name)
        first, more = file_patch(self.repo, origin, head, change, 0)
        self.assertTrue(more)
        self.assertNotIn("END_OF_LARGE_PATCH", first)
        second, more = file_patch(self.repo, origin, head, change, 1)
        self.assertFalse(more)
        self.assertIn("END_OF_LARGE_PATCH", second)
        self.assertNotIn("OTHER_FILE", first + second)

    def test_task_gate_and_merge_race(self):
        self.submit("add-task", note="Check currency", token="f" * 32)
        self.assertIn("Resolve open review tasks before merging.", self.submit("merge"))
        self.assertEqual(self.rev("main"), self.base)
        self.submit("set-task", task=1, resolved=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.submit, ["merge", "merge"]))
        self.assertEqual(sum("Changes merged into main." in html for html in results), 1)
        self.assertEqual(self.rev("main"), self.head)
        self.assertEqual(self.rev("refs/weftgit/merges/1"), self.head)
        self.assertIn("This pull request is merged.", self.submit("decline"))
        with closing(sqlite3.connect(self.repo / "weftgit.sqlite3")) as db, db:
            db.execute("UPDATE prs SET state='open'")
        self.assertIn('class="review-state">Merged', page(self.root, self.query))

    def test_upgrade_retains_historical_review_report_and_merge_marker(self):
        old = self.repo / "freethegit.sqlite3"
        current = self.repo / "weftgit.sqlite3"
        current.rename(old)
        git(self.repo, "update-ref", "refs/freethegit/reviews/1/head", self.head)
        self.submit("add-task", note="Check existing change", token="a" * 32)
        record(self.root, "test/billing", 1, "Agent", "1", self.head, "Upgrade evidence", "Passed")
        self.assertIn("Check existing change", page(self.root, self.query))
        self.assertIn("Upgrade evidence", page(self.root, self.query))
        self.assertEqual(repository_catalog(self.root)[0]["reviews"][0]["title"], "Review")
        git(self.repo, "update-ref", "refs/freethegit/merges/1", self.head)
        self.assertIn('class="review-state">Merged', page(self.root, self.query))
        self.assertEqual(self.rev("refs/freethegit/reviews/1/head"), self.head)
        self.assertFalse(current.exists())

    def test_release_receipts_require_merged_target_and_actual_publication(self):
        origin = "http://127.0.0.1:8091"
        def receipt(**params):
            return lookup(self.root, {"repo":["test/billing"], **{k:[v] for k,v in params.items()}}, origin)
        def publish(branch, sha, env):
            return page(self.root, "repo=test/billing", urlencode(dict(action="deploy", ref=branch, sha=sha, environment=env)))
        self.assertIn("Published", publish("include-currency", self.head, "prod"))
        self.assertTrue(receipt(commit=self.head)["verified"])
        self.assertFalse(receipt(pr="1")["verified"])
        self.assertIn("Changes merged", self.submit("merge"))
        self.assertFalse(receipt(pr="1")["verified"])
        self.assertIn("Published", publish("include-currency", self.head, "staging"))
        self.assertFalse(receipt(pr="1")["verified"])
        next_commit = git(self.repo, "-c", "user.name=Test", "-c", "user.email=t@example.test",
                          "commit-tree", self.rev("main^{tree}"), "-p", self.head, "-m", "Next").decode().strip()
        git(self.repo, "update-ref", "refs/heads/main", next_commit)
        self.assertIn("Published", publish("main", next_commit, "prod"))
        result = receipt(pr="1", head=self.head)
        self.assertEqual((result["verified"],result["reviewed_sha"],result["merge_sha"]), (True,self.head,self.head))
        self.assertFalse(receipt(pr="1", head=self.base)["verified"])
        self.assertEqual(len(result["releases"]), 1)
        release = result["releases"][0]
        self.assertEqual(release["published_sha"], next_commit)
        self.assertTrue(release["current"])
        self.assertTrue(release["archive_url"].endswith(next_commit+".zip"))
        self.assertEqual(self.rev("refs/weftgit/deployments/"+next_commit), next_commit)
        git(self.repo, "update-ref", "refs/heads/main", self.base)
        self.assertIn("Published", publish("main", self.base, "prod"))
        past = receipt(pr="1")["releases"][0]
        self.assertFalse(past["current"])
        self.assertIsNone(past["environment_url"])
        self.assertEqual(past["archive_url"], release["archive_url"])
        (self.repo / "deployments" / "prod" / (next_commit+".zip")).unlink()
        self.assertFalse(receipt(pr="1")["verified"])

    def test_publication_before_merge_cannot_certify_review(self):
        origin = "http://127.0.0.1:8091"
        # The exact head was on the target and published before this PR was merged.
        git(self.repo, "update-ref", "refs/heads/main", self.head)
        post = urlencode(dict(action="deploy", ref="main", sha=self.head, environment="prod"))
        page(self.root, self.query, post)
        git(self.repo, "update-ref", "refs/heads/main", self.base)
        self.submit("merge")
        params = {"repo":["test/billing"], "pr":["1"]}
        self.assertFalse(lookup(self.root, params, origin)["verified"])
        page(self.root, self.query, post)
        self.assertTrue(lookup(self.root, params, origin)["verified"])
        git(self.repo, "update-ref", "refs/freethegit/merges/1", self.base)
        self.assertFalse(lookup(self.root, params, origin)["verified"])

    def test_task_key_lookup_and_verified_merge_status(self):
        origin = "http://127.0.0.1:8091"
        params = {"key":["WT-001"]}
        def results():
            return task_reviews.lookup(self.root, params, origin)
        page(self.root, self.query)
        with closing(sqlite3.connect(self.repo / "weftgit.sqlite3")) as db, db:
            db.execute("UPDATE prs SET title='[wt-001] fix tax'")
            for branch in ("XWT-001", "WT-0012", "WT-001b", "WT-001_suffix", "WT-1", "WT-001é"):
                git(self.repo, "update-ref", "refs/heads/"+branch, self.head)
                db.execute("INSERT INTO prs(source,target,head,base,title) VALUES(?,?,?,?,?)",
                           (branch,"main",self.head,self.base,branch))
        branch = "feature/WT-001-export"
        git(self.repo, "update-ref", "refs/heads/"+branch, self.head)
        self.assertEqual([b["source_branch"] for b in results()["branches"]], [branch])
        match, = results()["matches"]
        self.assertEqual((match["matched_in"],match["staging_status"]), (["title"],"unmerged"))
        self.assertIn("Changes merged", self.submit("merge"))
        match, = results()["matches"]
        self.assertTrue(match["merge_verified"])
        self.assertEqual(match["staging_status"], "awaiting_staging")
        page(self.root, self.query, urlencode(dict(action="deploy", ref="main", sha=self.head, environment="prod")))
        self.assertEqual(results()["matches"][0]["staging_status"], "awaiting_staging")
        page(self.root, self.query, urlencode(dict(action="deploy", ref="main", sha=self.head, environment="staging")))
        self.assertEqual(results()["matches"][0]["staging_status"], "staged")
        git(self.repo, "update-ref", "refs/heads/main", self.base)
        page(self.root, self.query, urlencode(dict(action="deploy", ref="main", sha=self.base, environment="staging")))
        self.assertEqual(results()["matches"][0]["staging_status"], "previously_staged")
        git(self.repo, "update-ref", "-d", "refs/weftgit/merges/1")
        match, = results()["matches"]
        self.assertFalse(match["merge_verified"])
        self.assertEqual(match["staging_status"], "unverified")

    def test_task_key_ambiguity_scoping_and_supersession(self):
        import shutil
        page(self.root, self.query)
        branch = "feature/WT-007-export"
        git(self.repo, "update-ref", "refs/heads/"+branch, self.head)
        with closing(sqlite3.connect(self.repo / "weftgit.sqlite3")) as db, db:
            db.execute("UPDATE prs SET source=?,title='[WT-007] update'", (branch,))
            db.execute("INSERT INTO prs(source,target,head,base,title,state) VALUES(?,?,?,?,?,'superseded')",
                       (branch,"main",self.base,self.head,"WT-007 old"))
        shutil.copytree(self.repo, self.repo.parent / "second.git")
        origin = "http://127.0.0.1:8091"
        result = task_reviews.lookup(self.root, {"key":["WT-007"]}, origin)
        self.assertEqual([r["repository"] for r in result["matches"]], ["test/billing","test/second"])
        self.assertFalse(result["branches"])
        result = task_reviews.lookup(self.root, {"key":["wt-007"],"repo":["test/second"]}, origin)
        self.assertEqual(result["matches"][0]["matched_in"], ["source_branch","title"])
        self.assertEqual(len(result["matches"]), 1)
        for invalid in ({"key":["WT-7"]}, {"key":["WT-000"]}, {"key":["WT-007","WT-008"]},
                        {"key":["WT-001"], "repo":["../bad"]}):
            with self.assertRaises(ValueError):
                task_reviews.lookup(self.root, invalid, origin)

    def test_key_conflicts_require_explicit_link_and_identity_skips_receipt(self):
        from unittest.mock import patch
        origin = "http://127.0.0.1:8091"
        page(self.root, self.query)
        branch = "feature/WT-001-export"
        git(self.repo, "update-ref", "refs/heads/"+branch, self.head)
        git(self.repo, "update-ref", "refs/heads/WT-001-and-WT-003", self.head)
        with closing(sqlite3.connect(self.repo / "weftgit.sqlite3")) as db, db:
            db.execute("UPDATE prs SET source=?,title='[WT-002] tests for WT-002, notWT-005',state='merged'", (branch,))
        with patch("task_reviews.release_receipts.lookup", side_effect=AssertionError("receipt computed twice")):
            for key, other in (("WT-001","WT-002"),("WT-002","WT-001")):
                found = task_reviews.lookup(self.root, {"key":[key],"details":["identity"]}, origin)
                pr, = found["matches"]
                self.assertEqual(pr["conflicting_keys"], [other])
                self.assertIsNone(pr["merge_verified"])
                self.assertNotIn(branch, [b["source_branch"] for b in found["branches"]])
            found = task_reviews.lookup(self.root, {"key":["WT-001"], "details":["identity"]}, origin)
            self.assertEqual(found["branches"][0]["conflicting_keys"], ["WT-003"])

    def test_changed_source_and_target(self):
        git(self.repo, "update-ref", "refs/heads/include-currency", self.base)
        self.assertIn("A branch changed since review", self.submit("merge"))
        git(self.repo, "update-ref", "refs/heads/include-currency", self.head)
        git(self.repo, "update-ref", "refs/heads/main", self.head)
        self.assertIn("A branch changed since review", self.submit("merge"))

    def test_refresh_stale_review_carries_only_open_tasks_once(self):
        self.submit("add-task", note="Keep me", token="f" * 32)
        self.submit("add-task", note="Done already", token="e" * 32)
        self.submit("set-task", task=2, resolved=1)
        record(self.root, "test/billing", 1, "Review agent", "run-1", self.head, "Earlier findings", "Tests passed")
        updated = git(self.repo, "-c", "user.name=Test", "-c", "user.email=t@example.test",
                      "commit-tree", self.rev("include-currency^{tree}"), "-p", self.head, "-m", "Update").decode().strip()
        git(self.repo, "update-ref", "refs/heads/include-currency", updated)
        old = page(self.root, self.query)
        self.assertIn("Branch changes need review", old)
        self.assertIn('value="merge" disabled', old)
        for attempt in range(2):
            self.assertIn("Open latest review #2", self.submit("refresh"))
        new = page(self.root, "repo=test/billing&pr=2")
        self.assertIn("Earlier review #1", new)
        self.assertIn("Resolve 1 open task before merging", new)
        self.assertIn("Keep me", new)
        self.assertNotIn("Done already", new)
        self.assertNotIn("Earlier findings", new)
        self.assertIn("Earlier findings", page(self.root, self.query))

    def test_personal_routing_is_snapshot_specific_and_advisory(self):
        def act(number, actor, action, **kwargs):
            return page(self.root, f"repo=test/billing&pr={number}",
                        urlencode(dict(action=action, actor=actor, **kwargs)), actor)
        def inbox(actor):
            return repository_catalog(self.root, actor)[0]

        self.assertIn("Choose a local reviewer identity first", act(1, "", "assign", person="sam"))
        self.assertIn("Assigned @sam", act(1, "owner", "assign", person="Sam"))
        self.assertTrue(inbox("sam")["reviews"][0]["needsReview"])
        self.assertFalse(inbox("pat")["reviews"][0]["needsReview"])
        _, changes = file_review.changed_files(self.repo, self.base, self.head)
        key = next(iter(changes))
        act(1, "owner", "file-review", file_key=key, reviewed="1", sha=self.head)
        self.assertTrue(inbox("sam")["reviews"][0]["needsReview"])
        self.assertIn("Reload this snapshot", act(1, "sam", "signoff", head=self.base, base=self.head))
        self.assertIn("Review recorded for @sam", act(1, "sam", "signoff", head=self.head, base=self.base))
        self.assertTrue(inbox("sam")["reviews"][0]["reviewed"])
        self.assertIn("Personal review completion removed", act(1, "sam", "undo-signoff"))
        act(1, "sam", "signoff", head=self.head, base=self.base)
        act(1, "owner", "unassign", person="sam")
        act(1, "owner", "assign", person="pat")
        self.assertTrue(inbox("pat")["reviews"][0]["needsReview"])
        self.assertTrue(inbox("sam")["reviews"][0]["reviewed"])
        act(1, "owner", "assign", person="sam")
        updated = git(self.repo, "-c", "user.name=Test", "-c", "user.email=t@example.test",
                      "commit-tree", self.rev("include-currency^{tree}"), "-p", self.head, "-m", "Update").decode().strip()
        git(self.repo, "update-ref", "refs/heads/include-currency", updated)
        self.assertTrue(inbox("sam")["reviews"][0]["needsReview"])
        self.assertFalse(inbox("sam")["reviews"][0]["reviewed"])
        self.assertIn("branches changed", act(1, "pat", "signoff", head=self.head, base=self.base))
        act(1, "owner", "refresh")
        new = inbox("sam")
        self.assertEqual(new["reviews"][0]["id"], 2)
        self.assertTrue(new["reviews"][0]["needsReview"])
        self.assertTrue(new["history"][0]["reviewed"])
        self.assertIn("Changes merged into main.", act(2, "", "merge"))

    def test_author_identity_and_assignment_are_independent(self):
        git(self.repo, "update-ref", "refs/heads/extra", self.head)
        page(self.root, "repo=test/billing", urlencode(dict(action="pr", title="Authored",
             source="extra", target="main")), "mira")
        reviews = repository_catalog(self.root, "mira")[0]["reviews"]
        self.assertTrue(reviews[0]["authored"])
        self.assertFalse(reviews[0]["needsReview"])
        self.assertFalse(reviews[1]["authored"])
        self.assertNotIn("authored", repository_catalog(self.root)[0]["reviews"][0])
        self.assertIn("Your local identity changed", page(self.root, "repo=test/billing&pr=2",
                      "action=assign&actor=previous&person=alice", "mira"))

    def test_superseded_snapshot_is_read_only_and_rewind_starts_a_new_generation(self):
        changed = git(self.repo, "-c", "user.name=Test", "-c", "user.email=t@example.test",
                      "commit-tree", self.rev("include-currency^{tree}"), "-p", self.head, "-m", "Change").decode().strip()
        git(self.repo, "update-ref", "refs/heads/include-currency", changed)
        self.assertIn("Open latest review #2", self.submit("refresh"))
        git(self.repo, "update-ref", "refs/heads/include-currency", self.head)
        self.assertIn("This pull request is superseded", self.submit("merge"))
        self.assertEqual(self.rev("main"), self.base)
        self.assertIn("This pull request is superseded", self.submit("add-task", note="Late task", token="b"*32))
        next_query = "repo=test/billing&pr=2"
        self.assertIn("Open latest review #3", page(self.root, next_query, "action=refresh"))
        self.assertIn("Open latest review #3", page(self.root, self.query))
        self.assertIn("Changes merged into main.", page(self.root, "repo=test/billing&pr=3", "action=merge"))

    def test_pr_sections_and_on_demand_combined_diff(self):
        html = page(self.root, self.query)
        for name in ("files","tasks","details"):
            self.assertIn(f'data-pr-panel="{name}"', html)
        self.assertIn("View combined diff", html)
        self.assertNotIn('id="combined-patch"', html)
        combined = page(self.root, self.query + "&combined=1")
        self.assertIn('id="combined-patch"', combined)
        self.assertIn('aria-label="Combined code diff"', combined)
        self.assertIn('<details class="new-pr" open>', page(self.root, "repo=test/billing&view=pulls&new=1"))
        file_page = page(self.root, "repo=test/billing&ref=include-currency&file=exports.py")
        self.assertIn('ref=include-currency">← Repository files', file_page)
        self.assertIn('aria-label="File contents"', file_page)

    def test_copied_review_commands_read_the_immutable_comparison(self):
        from html import unescape
        import re
        import shlex

        # The checkout's hosted object directory remains available after branch updates.
        host = self.root / ".repositories"
        host.mkdir()
        self.repo.parent.rename(host / "test")
        self.repo = host / "test/billing.git"
        with closing(sqlite3.connect(self.repo / "weftgit.sqlite3")) as db, db:
            db.execute("UPDATE prs SET title='Private PR title'")
        risky_note = "Private reviewer note"
        page(host,self.query,urlencode(dict(action="add-task",note=risky_note,token="b"*32)))
        def fields():
            html = page(host,self.query)
            return {key:unescape(value) for key,value in
                    re.findall(r'<textarea id="([^"]+)"[^>]*>(.*?)</textarea>',html,re.S)}
        before = fields()
        self.assertIn("Open tasks when generated: 1",before["assistant-brief"])
        self.assertNotIn(risky_note,before["assistant-brief"])
        self.assertIn('PR title (quoted context): "Private PR title"',before["assistant-brief"])
        self.assertNotIn("Private PR title",before["review-git-commands"])
        self.assertIn(self.head,before["assistant-brief"])
        self.assertIn(self.base,before["assistant-brief"])
        moved = git(self.repo,"-c","user.name=Test","-c","user.email=t@example.test",
                    "commit-tree",self.rev("main^{tree}"),"-p",self.base,"-m","Replacement").decode().strip()
        git(self.repo,"update-ref","refs/heads/include-currency",moved)
        after = fields()
        self.assertIn("A branch has moved",after["assistant-brief"])
        self.assertEqual(before["review-git-commands"],after["review-git-commands"])
        results = [subprocess.check_output(shlex.split(line),cwd=self.root).decode()
                   for line in after["review-git-commands"].splitlines()]
        self.assertIn("Include currency",results[0])
        self.assertNotIn("Replacement",results[0])
        self.assertIn("exports.py",results[1])
        self.assertIn("currency",results[2])
        self.assertEqual(self.rev("include-currency"),moved)
        self.assertEqual(self.rev("main"),self.base)

    def test_branch_and_tag_can_have_the_same_name(self):
        git(self.repo, "update-ref", "refs/tags/main", self.head)
        html = page(self.root, "repo=test/billing&ref=main&file=exports.py")
        self.assertIn("COLUMNS", html)
        self.assertNotIn("currency", html.split('aria-label="File contents">')[1].split("</pre>")[0])
        self.assertIn('name="sha" value="' + self.base, html)

    def test_decline_is_terminal(self):
        self.assertIn("Pull request declined.", self.submit("decline"))
        self.assertIn("This pull request is declined.", self.submit("merge"))
        self.assertIn("This pull request is declined.", self.submit("add-task", note="Closed", token="a"*32))
        self.assertEqual(self.rev("main"), self.base)

    def test_agent_report_retry_provenance_and_staleness(self):
        link = "http://127.0.0.1:8090/?workspace=local&item=ae5ea668-a934-4cc4-b9eb-2b42e001852b#board"
        args = (self.root, "test/billing", 1, "Review bot", "run-17", self.head)
        for attempt in range(2):
            record(*args, "Check <currency>", "5 tests passed", "EXPORT-9", link)
        html = page(self.root, self.query)
        self.assertEqual(html.count("Check &lt;currency&gt;"), 1)
        self.assertIn("Reviewed snapshot", html)
        self.assertIn("5 tests passed", html)
        self.assertIn("EXPORT-9</a>", html)
        self.assertIn('class="review-state">Open', html)
        with self.assertRaises(ValueError):
            record(*args, "Changed report", "5 tests passed", "EXPORT-9", link)
        with self.assertRaises(ValueError):
            record(*args, "Report", "5 tests passed", "EXPORT-9", "javascript:alert(1)")
        git(self.repo, "update-ref", "refs/heads/include-currency", self.base)
        self.assertIn("source branch has changed", page(self.root, self.query))

    def branch_target(self, filename, content):
        checkout = self.root / "checkout"
        subprocess.run(["git", "clone", "-q", str(self.repo), str(checkout)], check=True)
        (checkout / filename).write_text(content)
        for args in (["add", filename], ["-c", "user.email=t@example.test", "-c", "user.name=Test",
                     "commit", "-qm", "Change target"], ["push", "-q", "origin", "main"]):
            subprocess.run(["git", "-C", str(checkout), *args], check=True)
        self.submit("pr", source="include-currency", target="main", title="New review")
        self.query = "repo=test/billing&pr=2"

    def test_diverged_branches_merge_without_losing_target_changes(self):
        self.branch_target("notes.txt", "Independent change")
        target = self.rev("main")
        self.assertIn("Changes merged into main.", self.submit("merge"))
        self.assertEqual(self.rev("main^1"), target)
        self.assertEqual(self.rev("main^2"), self.head)
        self.assertEqual(git(self.repo, "show", "main:notes.txt"), b"Independent change")

    def test_conflict_leaves_branches_open_and_unchanged(self):
        self.branch_target("exports.py", 'COLUMNS = ["conflict"]\n')
        target = self.rev("main")
        preview = page(self.root, self.query)
        self.assertIn("Resolve branch conflicts locally before merging", preview)
        self.assertIn('value="merge" disabled', preview)
        self.assertIn("could not merge these branches cleanly", self.submit("merge"))
        self.assertEqual(self.rev("main"), target)
        self.assertIn('class="review-state">Open', page(self.root, self.query))


if __name__ == "__main__":
    unittest.main()
