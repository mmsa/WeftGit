from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode
from uuid import uuid4
from contextlib import closing
import sqlite3
import subprocess
import unittest

from file_review import changed_files
from hosted import page


class AnchoredTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.repo = self.root / "team/review.git"
        self.query = "repo=team/review&pr=1"
        self.git("init", "-q", "--initial-branch=main")
        (self.work/"billing.py").write_text("heading\nold <currency>\ntail\n")
        (self.work/"removed.txt").write_text("obsolete\n")
        self.commit("Base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("switch", "-qc", "feature")
        (self.work/"billing.py").write_text("new heading\nextra row\nnew currency\ntail\n")
        (self.work/"removed.txt").unlink()
        (self.work/"empty.txt").touch()
        (self.work/"asset.bin").write_bytes(b"hello\x00binary")
        self.commit("Feature")
        self.head = self.git("rev-parse", "HEAD")
        self.git("clone", "-q", "--bare", str(self.work), str(self.repo))
        page(self.root, self.query, "action=pr&source=feature&target=main&title=Review")
        _, changes = changed_files(self.repo, self.base, self.head)
        self.files = {change.label:key for key,change in changes.items()}

    def git(self,*args):
        return subprocess.check_output(["git","-C",str(self.work),"-c","user.name=Test","-c",
                                        "user.email=t@example.test",*args], stderr=subprocess.PIPE).decode().strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def add(self, name, side, line=None, token=None, **overrides):
        key = self.files[name]
        data = dict(action="add-anchored-task",file_key=key,sha=self.head,base=self.base,
                    token=token or uuid4().hex,side=side,note="Check the change")
        if line is not None:
            data["line"] = line
        data.update(overrides)
        return page(self.root,self.query+"&changed="+key,urlencode(data))

    def context(self):
        with closing(sqlite3.connect(self.repo/"freethegit.sqlite3")) as db, db:
            return db.execute("""SELECT t.pr,a.origin_pr,a.side,a.revision,a.line,a.excerpt,a.path
                              FROM review_tasks t LEFT JOIN task_anchors a ON a.task=t.id ORDER BY t.id""").fetchall()

    def test_real_lines_retry_and_original_context_after_new_push(self):
        token = uuid4().hex
        self.add("billing.py","before",2,token)
        self.add("billing.py","before",2,token)
        rendered = self.add("billing.py","after",3)
        self.assertIn("Before · billing.py:2", rendered)
        self.assertIn("old &lt;currency&gt;", rendered)
        self.assertIn("After · billing.py:3", rendered)
        page(self.root,self.query,"action=add-task&note=General&token="+"a"*32)
        original = self.context()
        self.assertEqual(original,[
            (1,1,"before",self.base,2,"old <currency>",b"billing.py"),
            (1,1,"after",self.head,3,"new currency",b"billing.py"),
            (1,None,None,None,None,None,None)])
        self.git("mv","billing.py","invoices.py")
        (self.work/"invoices.py").write_text("entirely replaced\n")
        self.commit("Rename and remove old lines")
        self.git("push","-q",str(self.repo),"feature")
        page(self.root,self.query,"action=refresh")
        fresh = page(self.root,"repo=team/review&pr=2")
        self.assertIn("Earlier review #1 · After · billing.py:3", fresh)
        self.assertIn("new currency", fresh)
        self.assertEqual(self.context()[3:],[(2,*row[1:]) for row in original])
        old_link = self.query+"&"+urlencode(dict(changed=self.files["billing.py"],anchor_side="before",anchor_line=2))
        self.assertIn('<div id="line-context"><strong>Before · line 2</strong><pre tabindex="0" aria-label="Selected source line">old &lt;currency&gt;</pre>',page(self.root,old_link))

    def test_before_line_uses_diff_merge_base_on_diverged_branches(self):
        self.git("switch", "-q", "main")
        (self.work/"billing.py").write_text("shifted target users have not reviewed\n")
        self.commit("Independent target change")
        destination = self.git("rev-parse", "HEAD")
        self.git("push", "-q", str(self.repo), "main")
        page(self.root, self.query, "action=refresh")
        self.query = "repo=team/review&pr=2"
        self.add("billing.py", "before", 2, base=destination)
        saved = self.context()
        self.assertEqual(saved[0][3:6], (self.base, 2, "old <currency>"))

    def test_file_only_and_invalid_sides_or_revisions(self):
        for name in ("empty.txt","asset.bin"):
            html = self.add(name,"after",1)
            self.assertIn("That text line does not exist",html)
            self.assertIn('<details id="file-task" open>',html)
            self.assertIn('value="Check the change"',html)
            self.add(name,"file")
        self.assertIn("That text line does not exist", self.add("removed.txt","after",1))
        self.assertIn("That text line does not exist", self.add("billing.py","after",5))
        self.assertIn("Reload the selected file", self.add("billing.py","after",1,sha=self.base))
        self.assertIn("Reload the selected file", self.add("billing.py","before",1,file_key=self.files["removed.txt"]))
        self.add("removed.txt","before",1)
        self.add("removed.txt","file")
        rows=self.context()
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[-2][3:6],(self.base,1,"obsolete"))
        self.assertEqual(rows[-1][3:6],(self.base,None,""))


if __name__ == "__main__":
    unittest.main()
