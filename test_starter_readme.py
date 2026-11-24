import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from hosted import page


class StarterReadmeTests(unittest.TestCase):
    def test_browser_creation_and_retries_preserve_the_first_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "team" / "sample.git"
            repo.parent.mkdir()
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", repo],
                           capture_output=True, check=True)
            query = "repo=team%2Fsample"

            def submit(description):
                return page(root, query, urlencode({"action":"start-readme", "description":description}))

            def git(*args):
                return subprocess.check_output(["git", f"--git-dir={repo}", *args]).decode().strip()

            self.assertIn("Create README.md", page(root, query))
            self.assertIn("1–500 characters", submit("   "))
            description = "Track expenses for my café."
            self.assertIn("first commit", submit(description))
            head = git("rev-parse", "main")
            self.assertEqual(git("show", "main:README.md"), "# sample\n\n" + description)
            self.assertIn("already saved", submit(description))
            self.assertIn("already has commits", submit("Different description"))
            self.assertEqual(git("rev-parse", "main"), head)
            self.assertNotIn("Create README.md", page(root, query))
