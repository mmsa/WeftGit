"""Browse the field guide and the relationships between its chapters."""

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parent
SECTION = re.compile(r"^<!-- ([a-z][a-z0-9-]+) -->\n## ([^\n]+)\n", re.MULTILINE)


def load_index(db):
    db.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE chapter (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            body TEXT NOT NULL
        );
        CREATE TABLE edge (
            source TEXT NOT NULL REFERENCES chapter(id),
            target TEXT NOT NULL REFERENCES chapter(id),
            label TEXT NOT NULL,
            PRIMARY KEY (source, target, label)
        );
    """)
    for path in sorted((ROOT / "guides").glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        sections = list(SECTION.finditer(markdown))
        for index, section in enumerate(sections):
            end = sections[index + 1].start() if index + 1 < len(sections) else len(markdown)
            db.execute(
                "INSERT INTO chapter VALUES (?, ?, ?)",
                (*section.groups(), markdown[section.end():end].strip()),
            )
    edges = json.loads((ROOT / "relationships.json").read_text(encoding="utf-8"))
    db.executemany("INSERT INTO edge VALUES (?, ?, ?)", edges)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "graph"):
        commands.add_parser(command)
    for command, argument in (("show", "chapter"), ("related", "chapter"), ("search", "query")):
        commands.add_parser(command).add_argument(argument)
    args = parser.parse_args()

    with closing(sqlite3.connect(":memory:")) as db:
        load_index(db)
        if args.command == "list":
            matches = db.execute("SELECT id, title FROM chapter ORDER BY rowid")
        elif args.command == "search":
            query = args.query.strip().lower()
            if not query:
                parser.error("enter a search term")
            matches = db.execute(
                "SELECT id, title FROM chapter WHERE instr(lower(title || ' ' || body), ?) ORDER BY rowid",
                (query,),
            )
        elif args.command == "graph":
            print("flowchart LR")
            for identifier, title in db.execute("SELECT id, title FROM chapter ORDER BY rowid"):
                print(f'  {identifier.replace("-", "_")}["{title.replace(chr(34), "#quot;")}"]')
            for source, target, label in db.execute("SELECT source, target, label FROM edge"):
                print(f'  {source.replace("-", "_")} -->|"{label}"| {target.replace("-", "_")}')
            return
        else:
            chapter = db.execute("SELECT title, body FROM chapter WHERE id = ?", (args.chapter,)).fetchone()
            if chapter is None:
                parser.error(f"unknown chapter {args.chapter!r}; use list to find chapter IDs")
            if args.command == "show":
                print(f"# {chapter[0]}\n\n{chapter[1]}")
                return
            for source, target, label in db.execute(
                "SELECT source, target, label FROM edge WHERE source = ? OR target = ? ORDER BY source, target",
                (args.chapter, args.chapter),
            ):
                print(f"{source} --{label}--> {target}")
            return
        for identifier, title in matches:
            print(f"{identifier}\t{title}")


if __name__ == "__main__":
    main()
