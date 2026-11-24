# Repository Host field guide

Examples use generic names and placeholder hosts. Product-specific plans, dates, and settings are historical reference details and may differ from your deployment. Cloud refers to Vendor-hosted Repository Host. Data Center refers to self-managed Repository Host. In the running example, the Billing team ships scheduled CSV exports, tracked as Work Tracker work item BILL-42.

<!-- repo-host-git -->
## 1. Git and Repository Host

Git records directory snapshots and their history. You can commit, branch, compare, and inspect history locally. Repository Host hosts a shared Git repository and adds collaboration and administration, including permissions, pull requests, review, integrations, and build status. Repository Host Pipelines adds managed CI/CD in Cloud. Work Tracker tracks the work and its status. Confluence holds the design, runbook, and release notes. Connect Work Tracker and include BILL-42 in a branch, commit, or pull request to surface development activity on the work item.

| Term | Meaning and where you see it |
|---|---|
| Repository | Git object database plus references. Repository Host presents files, commits, branches, tags, permissions and reviews around the hosted repository. |
| Working tree | The files checked out on your computer. Unsaved and untracked edits live locally until you capture and share them. |
| Staging area | The index that selects the paths or hunks for the next commit. Inspect with `git diff --cached`. |
| Commit | A snapshot linked to its parent commits, with author, message, and ID. Repository Host shows the message and diff against a parent. |
| Branch | A movable reference to the tip commit of a line of work. Repository Host can restrict pushes and merges by branch or pattern. |
| Remote | A named URL in a local repository. A clone usually creates `origin`. Fetching updates remote-tracking references; pushing updates permitted references on the host. |
| Tag | A named reference usually used to mark a release commit. Annotated tags carry metadata. Push tags explicitly. |
| Fork | A separate hosted repository with a relationship to a source repo, useful when contributors lack write access. Synchronize it with the upstream remote as needed. |
| Pull request | A Repository Host review record proposing changes from a source branch or fork into a destination branch. Git supplies the commits and merge mechanics. |

**Cloud forks.** Since July 1, 2026 new forks must stay in the parent workspace. Old cross-workspace repositories remain, but their fork relationships were broken. Already open cross-workspace PRs remain viewable and mergeable. Use branches or permitted same-workspace forks for new PRs.

<!-- repo-host-setup -->
## 2. Set up the organization and repository

**Cloud** uses workspace → project → repository. The workspace owns membership and billing. A project groups repositories and can apply shared settings. Create a workspace or ask the workspace admin for access. In the workspace, create project Billing, then choose **Create → Repository**, select the workspace and project, name it `billing-export`, and keep it private for this example. Add a README for a new repo you plan to clone. Start empty if you already have a local repository to push. Public repositories expose source to anyone. Review the project visibility and the effective repository settings before opening access.

**Data Center** uses instance → project → repository, with global, project, repository, and branch permissions. On the instance, create a project with a unique key, then create a repository in that project. Have an instance admin configure identity, base URLs and SSH before onboarding. Public access depends on instance policy and project or repository settings. Grant access through groups at project level where all member repositories should share access.

For an existing hosted repository, use **Create → Repository → Import repository** in Cloud. Supply the source clone URL and appropriate source credential for private code. On either deployment, you can migrate Git branches and tags from a local clone by adding the destination remote and using `git push destination --all` and `git push destination --tags`. Verify default branch, branch heads, tags, and LFS objects afterwards. A Git transport copies Git history. Pull requests, permissions, hooks, settings, and build history need their own migration plan. For Data Center to Cloud, evaluate Vendor's migration assistant against your inventory.

Push `main` first if it does not exist. In repository settings select the main/default branch and update branch restrictions, CI triggers, open pull requests, and local tracking branches if you change it later. Add teammates through workspace groups or repository access in Cloud, and through project or repository permissions in Data Center. Start with Read for observers, Write for contributors, and Admin for maintainers. Test the effective permissions of a non-admin because inherited access can be broader than a repo-specific grant.

<!-- repo-host-auth -->
## 3. Authenticate Git, API clients, and automation

| Deployment and credential | Where to use it |
|---|---|
| Cloud personal SSH key | Git clone, fetch and push using the SSH URL. The user keeps the private key and adds the public key to their Repository Host account. Repository/project SSH access keys are appropriate for read-only Git consumers. |
| Cloud scoped user API token | For HTTPS Git, use the Repository Host username or `x-repo-host-api-token-auth` and supply the token at the password prompt. For REST Basic auth, use Vendor account email plus token. Clone requires `read:repository:repo-host`. Push needs both read and write repository scopes. Choose an expiry of 1–365 days at creation and rotate credentials before expiry. |
| Cloud resource access token | Give automation the smallest repository, project, or workspace scope. Project and workspace tokens require Premium. Use Bearer for supported REST calls and the documented `x-token-auth` username for HTTPS Git. The workspace can impose an expiry policy. |
| Cloud OAuth 2.0 | Use for integrations that need delegated access. Configure consumer scopes and an authorized flow, use the access token as Bearer for REST, and handle the two-hour access token expiry and refresh where the grant provides it. |
| Data Center SSH or HTTP access token | Use a personal SSH key for Git. For HTTPS Git or REST use a user HTTP access token, with name, permissions and expiry. Project and repository HTTP tokens use Bearer authentication, including for Git. Check expiry requirements and token policies on the instance. |

Store secrets in a credential helper or secret store and keep credentials out of clone URLs and logs. A token scope never grants a user permissions beyond their entitlement. Check endpoint scope requirements for pull request and pipeline operations separately.

**Old Cloud app passwords.** Creation stopped September 9, 2025. Brownouts ran June 9–July 27, 2026, and permanent removal was July 28, 2026. Some older support articles still give June 9 as the final date. Update credential helpers and build secrets, and use the correct REST email or Git username when moving to scoped API tokens. **Repository Host Server** support ended February 15, 2024. Older Server documentation can describe predecessor behavior. Check the matching version of Data Center documentation before applying it.

**SSH hostname changes.** When your provider changes its SSH endpoint, update developer remotes, submodule URLs, allowed hosts, and build systems. Use the SSH clone URL shown by your provider.

<!-- repo-host-storage -->
## 4. Storage, large files, and submodules

Cloud's Free workspace has **1 GB of total Git repository storage**. Vendor advises keeping individual repositories below 2 GB and documents a **4 GB** repository push limit. An optional control can reject files of 100 MB or larger. Treat Git LFS as a separate allowance. Data Center capacity depends on instance configuration and attached storage. Budget for clones, concurrent fetches, replication and backups as well as repository size. Diagnose Git object growth with `git count-objects -vH`. Removing a large tracked file in a new commit leaves its earlier versions in history. Coordinate any history rewrite across contributors and integrations.

Git LFS commits a pointer into Git and uploads the binary to an LFS endpoint. Install Git LFS, run `git lfs install`, use `git lfs track "*.psd"`, and commit `.gitattributes` along with the files. For migration, explicitly transfer and validate all required LFS objects. Confirm LFS settings at the Data Center repository and instance. A submodule pins another repository at one commit. Commit the gitlink and `.gitmodules`, clone with `--recurse-submodules` or run `git submodule update --init --recursive`. Each private submodule needs credentials from the developer or CI runner.

<!-- repo-host-review -->
## 5. Daily development and review

```sh
git clone git@ssh.example.invalid:WORKSPACE/billing-export.git
cd billing-export
git switch -c feature/BILL-42-scheduled-csv
# Edit and test your changes.
git status
git diff
git add path/to/changed-file.py
git diff --cached
git commit -m "BILL-42 Add scheduled CSV export"
git push -u origin feature/BILL-42-scheduled-csv
```

Open a pull request into `main`. Include acceptance criteria, test evidence and a link to BILL-42. Review the diff and build results, keep discussion next to the changed lines, and push follow-up commits to the same branch. If the branch needs updating, fetch and merge `origin/main` into it, resolve conflicts, test and push. Rebase only when rewriting that branch is acceptable to its contributors. A merge commit preserves branch topology, squash combines the proposed changes into one commit, and fast-forward moves the destination reference when history makes it possible. Choose among the strategies the repo enables. After release, create and push a tag such as `v1.4.0` on the shipped commit.

Restrict direct writes to `main`, select reviewers, and make passing checks part of the merge policy. Cloud project restrictions apply to repositories in that project. Required merge checks are a Premium feature. Data Center has version-specific branch permissions and merge checks. For either deployment, verify the policy with a developer account and a deliberately failing build.

<!-- repo-host-pipelines -->
## 6. Build CI/CD

**Cloud.** Enable Pipelines for the repository and commit `repo-host-pipelines.yml` at its root. This example expects a Python app with dependencies in requirements.txt, pytest installed by that file, and a checked-in staging deploy script. Set up a staging deployment environment and its authentication before using the deployment step.

```yaml
image: python:3.12
definitions:
  steps:
    - step: &test
        name: Test CSV export
        caches:
          - pip
        script:
          - pip install -r requirements.txt
          - python -m pytest
pipelines:
  pull-requests:
    '**':
      - step: *test
  branches:
    main:
      - step: *test
      - step:
          name: Deploy to staging
          deployment: staging
          trigger: manual
          oidc: true
          script:
            - bash scripts/deploy-staging.sh
```

PR builds validate changes before merge. The branch pipeline tests the resulting main commit and then waits for a staging deploy. Add production only after deciding who can approve, how a build artifact is promoted, and how to roll back. Steps run in separate containers. Use artifacts to transfer a build output to a later step and caches to speed up re-downloads. Use a custom pipeline for manual or scheduled tasks. Use deployment variables for environment configuration, secured variables for secrets where required, and OIDC with audience and repository/environment restrictions for short-lived cloud access. Self-hosted Cloud runners execute steps on infrastructure you maintain and match labels under `runs-on`. Keep untrusted PRs away from privileged runners and deployment credentials.

**Data Center.** Use a separately operated CI system such as Bamboo or Jenkins. Configure repository discovery or webhooks, give the runner a read credential, build the exact revision under review, report its status to Repository Host, and enforce successful builds where supported. Cloud runner configuration does not turn Data Center into Repository Host Pipelines. If adopting Hybrid, plan which repository data and build jobs will live on each side.

<!-- repo-host-admin -->
## 7. Plans, operations, and lifecycle

| Cloud plan | Monthly list pricing shown in Vendor billing docs | Monthly build minutes | Git LFS | Selected controls |
|---|---|---:|---:|---|
| Free | Free for up to five users | 50 | 1 GB | Core Git, pull requests, Pipelines |
| Standard | US$18.25 for 1–5 users, US$3.65 per user above five | 2,500 | 5 GB | Increased allowances and larger step sizes |
| Premium | US$36.25 for 1–5 users, US$7.25 per user above five | 3,500 | 10 GB | Enforced merge checks, deployment permissions, IP allowlisting, required two-step verification, project and workspace access tokens |

Confirm costs in the workspace's current quote, especially annual billing and usage charges. Monthly usage allowances began moving to organization-level pooling on October 26, 2026. Vendor announces annual-plan overage billing and availability of plan features such as Packages from December 3, 2026.

Cloud receives rolling releases. Check the Cloud support release notes and end-of-support announcements for changes affecting your workspace.

In Cloud, assign owners for workspace administration, billing, repos and runners. Review public repositories, external collaborators, tokens, webhooks and privileged branches. Audit administrative events and use Git history and API data for detailed work activity. In Data Center, also plan patches, identity, supported database and Git versions, clustering and storage, backup and restore drills, monitoring, and incident response. The 10.5 release notes document system-level X.509 signing and a November 3, 2026 patch (10.5.2). Check the upgrade matrix and current advisories before changing an instance.

Data Center remains available after March 28, 2029 through the **Repository Host Hybrid License**, which combines Cloud and Data Center rights for eligible customers. Vendor says the standalone Data Center licensing path ends on that date and instances without valid replacement licensing become read-only on expiry. Confirm eligibility and timing with Vendor before planning renewal.

<!-- repo-host-reference -->
## 8. Checklist and troubleshooting

- Create a private repo, confirm the default branch, and test access as a contributor.
- Link Work Tracker and attach BILL-42 to the branch and pull request.
- Configure branch restrictions, credential expiry and a CI owner. Run a PR that fails a test before checking a passing one.
- Validate staging deploy, rollback, and retention of build evidence. Tag the release and link it back to the Work Tracker version.
- **Clone fails:** verify remote URL, account access, SSH key selection or the HTTPS token's username and scope.
- **Push rejected:** check branch restrictions, token write scopes, and repo size. Fetch first if the destination moved.
- **Build missing:** check whether Pipelines is enabled, YAML is on the applicable ref, trigger pattern matches, and runner or minutes are available.
- **Deployment blocked:** check manual gates, environment permissions, credential scope and OIDC trust claims.
