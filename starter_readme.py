"""Create the first README in a local repository using a create-only ref update."""
import subprocess


def add_readme(repo, project, description):
    description = description.strip()
    if not 1 <= len(description) <= 500 or "\0" in description:
        raise ValueError("Describe the project in 1–500 characters.")

    def git(*args, data=None):
        result = subprocess.run(["git", f"--git-dir={repo}", *args], input=data,
                                capture_output=True, check=True, timeout=10)
        return result.stdout.decode("utf-8", errors="replace").strip()

    content = f"# {project}\n\n{description}\n".encode()
    blob = git("hash-object", "-w", "--stdin", data=content)
    tree = git("mktree", data=f"100644 blob {blob}\tREADME.md\n".encode())
    branch = git("symbolic-ref", "HEAD")
    branches = git("for-each-ref", "--format=%(refname)", "refs/heads").splitlines()
    if branches:
        if branch in branches and git("rev-parse", f"{branch}^{{tree}}") == tree:
            return "This README is already saved."
        raise ValueError("This repository already has commits. Open its files to see the latest version.")

    commit = git("-c", "user.name=FreeTheGit", "-c", "user.email=workspace@localhost",
                 "commit-tree", tree, "-m", "Add project README")
    try:
        git("update-ref", branch, commit, "0" * len(commit))
    except subprocess.CalledProcessError as error:
        raise ValueError("The branch changed before the README was saved. Refresh the repository to see its files.") from error
    return "README.md saved in the first commit."
