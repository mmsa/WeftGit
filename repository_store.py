"""Locate the authoritative metadata database inside a hosted Git repository."""


def database_path(repo):
    # Keep old databases and their SQLite journals together, including while an
    # agent reporter has a connection open. Fresh repositories use the new name.
    legacy = repo / "freethegit.sqlite3"
    return legacy if legacy.exists() or legacy.is_symlink() else repo / "weftgit.sqlite3"
