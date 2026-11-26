# WeftGit

A local Git host for reviewing code with people and coding assistants. Browse a repository, review a pull request, and publish a static snapshot on your machine. Review tasks stay linked to the original lines, even after a later push renames the file.

**Which commit reached staging?** [Open the read-only example](https://mmsa.github.io/WeftTrack/) to inspect a sample task and follow its published commit to the exact page. [Watch the captioned walkthrough](https://mmsa.github.io/WeftTrack/#walkthrough).

[Quickstart](#quickstart) · [Review a change](#review-a-change) · [Publish a snapshot](#publish-a-snapshot) · [Local accounts and storage](#local-accounts-and-storage) · [Development](#development)

![WeftGit pull request showing the changed file, review tasks, and merge readiness for a billing export](docs/preview.png)

<details>
<summary>Watch a requirement reach staging (28 seconds)</summary>

![Screen recording of a requirement being claimed in WeftTrack, reviewed and merged in WeftGit, and opened from its verified staging receipt](docs/requirement-to-staging.gif)

The recording follows two local apps from the original requirement to the exact commit behind **Open site**. The published site is a static release note.

</details>

## Quickstart

You need **Python 3.10+**, **Git 2.38+** on your PATH, and a browser. WeftGit uses the Python standard library and SQLite.

```sh
git clone https://github.com/mmsa/WeftGit.git
cd WeftGit
python3 web.py
```

Open the local workspace and choose **Create example review** at the foot of the **Pull requests** page. It creates a real Git repository with two branches and a small change to a billing export. To start your own project, choose **Repositories → New repository**. On its Code page, describe what you want to build and select **Create README.md** to make the first Git commit from the browser. If you already have a local project, expand **Push an existing Git project** and run the command from your project directory. [Open WeftGit](http://127.0.0.1:8091/)

## Review a change

1. **Read the diff.** Open a pull request and choose a changed file. Click a line number to add a task to the before or after version. Whole-file tasks work for binary files too.
2. **Review a later push.** Choose **Open fresh review**. Unresolved tasks carry forward with links to their original code. Unchanged files keep their shared review marks. Personal sign-offs stay with the commits each person saw.
3. **Merge the reviewed commits.** Resolve the open tasks and select **Merge pull request**. WeftGit rechecks both branch tips and reports stale revisions or conflicts before changing the target branch.

**Review with an assistant.** Choose **Copy review brief** on a pull request and paste it into an assistant with access to the WeftGit checkout. The brief specifies the reviewed commits, the full change to inspect, and where to find open tasks. To inspect it yourself, expand **Review brief and Git commands**. The commands read the saved objects directly from the hosted repository, so a later push does not change the comparison.

**Attach the results.** In **Review details → Attach an agent report**, copy the command generated for that PR. It records the agent's findings, test results, run ID, and reviewed commit. Add `--work-item` and `--work-url` to link a requirement in a local WeftTrack workspace. Reviewers can see when a report refers to an earlier revision.

## Publish a snapshot

In a hosted repository, open **Deployments**, choose the branch and environment, then **Load revision → Publish snapshot**. Every published revision has a downloadable ZIP. If it contains a prebuilt `index.html` in the root, `dist/`, `build/`, `public/`, or `docs/`, **Open site** opens a sandboxed local preview. Revision links stay pinned. The environment link follows the latest publication.

Static previews support relative links to packaged HTML, CSS, images, and fonts. Scripts, form submissions, and external assets are disabled in previews. Build execution, application servers, and remote deployment require a separate runner.

### Verify a release from WeftTrack

A linked pull request can show which local environments received the reviewed change. WeftGit checks its recorded merge result, the target branch used for publication, the published commit’s ancestry, and the registered ZIP’s embedded revision. Each environment shows its latest qualifying publication. After a rollback you can still open its pinned archive or site. A current environment link is available when that same publication still serves a site.

The read-only loopback endpoint accepts either a PR number or a complete Git commit SHA. A PR lookup can also include the reviewed head SHA for an exact snapshot match.

```sh
curl 'http://127.0.0.1:8091/api/release-receipt?repo=example/billing&pr=1'
curl "http://127.0.0.1:8091/api/release-receipt?repo=example/billing&commit=$COMMIT_SHA"
```

Commit lookups verify inclusion in a published snapshot. Older publications without a recorded branch can verify a commit. To obtain a PR receipt for an older merge, publish the target branch again. Receipts describe local WeftGit publications and follow the same trusted-machine access model as repository browsing.

Put the displayed WeftTrack task key in a source branch or PR title, such as `feature/WT-012-exports` or `[WT-012] Add exports`. WeftTrack can discover the matching review and distinguish a verified merge awaiting staging from a current staging publication or a previous publication after rollback. Matching is case-insensitive and uses the whole key, including its zero padding.

```sh
curl 'http://127.0.0.1:8091/api/task-reviews?key=WT-012'
```

The lookup returns all matching reviews across repositories and separately lists live matching branches without a review at their head. Add `&repo=project/repository` to narrow the search. Superseded snapshots are omitted. When several changes mention the same key, or the title and source branch name different tasks, use an explicit PR link to identify the intended change. Each candidate lists its other keys in `conflicting_keys`. Clients that fetch the receipt separately can request `details=identity` to skip duplicate Git and archive verification.

## Local accounts and storage

The server binds to `127.0.0.1:8091` for use on a trusted machine. Open **Users** to create the first owner and add reviewer accounts. Once configured, password-backed sessions identify authors and reviewers. Deactivating an account revokes its sessions and preserves its review history. Git pushes, general review tasks, and merges remain available to local clients without signing in.

Bare repositories, SQLite records, accounts, and published archives live under `.repositories/` in the checkout. The pipeline editor stores its project and stage configuration in the browser. Back up the server directory and retain the browser profile if you need both.

Upgrading from FreeTheGit? Keep the existing checkout and its `.repositories/` directory. Start WeftGit on the same port and open it in your usual browser profile. Existing reviews, account sessions, pipeline settings, Git remotes and published URLs continue to work.

## Development

Run the full regression suite from the repository root. Tests create disposable Git repositories and databases.

```sh
python3 -m unittest discover -v
```

The bundled field guide follows a team shipping scheduled CSV exports. Read it in the browser, use the Markdown guides directly, or run `python3 fieldguide.py list` to see chapter IDs. The CLI also supports `show`, `search`, `related`, and `graph`. [Browser field guide](http://127.0.0.1:8091/guide) · [Repository hosting](guides/repo-host.md) · [Work tracking](guides/work-tracker.md)

To connect a review to its original requirement, run the companion work tracker alongside WeftGit. [WeftTrack on GitHub](https://github.com/mmsa/WeftTrack)

Code and documentation are released under the [MIT license](LICENSE).
