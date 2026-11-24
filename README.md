# freethegit

An independent, open-source Python reference for learning Work Tracker and Repository Host through one SaaS feature launch. The example team builds scheduled CSV exports for billing reports.

- [Repository Host guide](guides/repo-host.md)
- [Work Tracker guide](guides/work-tracker.md)

## Run locally

Requires Python 3.10 or later. From the checkout:

```sh
python fieldguide.py list
python fieldguide.py show work-tracker-workflow
python fieldguide.py search release
python fieldguide.py related repo-host-pipelines
python fieldguide.py graph > guide.mmd
```

The guides are plain Markdown. The app indexes sections in an in-memory SQLite database and models relationships as directed, labeled edges. `related` exposes links in both directions. `graph` exports Mermaid source. SQLite keeps the project portable and can use recursive queries when multi-hop exploration is needed. A dedicated graph database becomes worth evaluating if the app grows into an explorer for large, live instance graphs with frequent multi-hop queries.

The example uses fictional records. The app runs locally without Vendor credentials and does not connect to Vendor or GitHub.

Code and documentation are available under the MIT license.
