# Automated Git-Tag Versioning

This document describes the versioning pattern used in this project. It is written as a reusable reference for applying the same pattern to other Python projects.

## Overview

Version numbers live exclusively in **git tags** (`v<major>.<minor>.<patch>`). There is no version string hardcoded anywhere in the source. A GitHub Actions workflow creates a new tag on every push to `main`, and **hatch-vcs** reads that tag at build time to populate the package version automatically. No manual version management is required.

The **bump level is chosen by the merge commit subject** — the same convention used across our apps (e.g. the imp-dashboard Grafana panel). Because GitHub squash-merges use the PR title as the commit subject, you control the bump from the **PR title**:

| Marker in PR title / merge subject | Bump | `v1.4.2` becomes |
|---|---|---|
| `[major]` | major | `v2.0.0` |
| `[minor]` | minor | `v1.5.0` |
| _(neither — the default)_ | patch | `v1.4.3` |

---

## Files to add or modify

### 1. `pyproject.toml`

Remove any static `version = "..."` field and switch to dynamic versioning via hatch-vcs:

```toml
[project]
name = "your-package-name"
dynamic = ["version"]   # version comes from git tags at build time
# ... rest of [project] unchanged

[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[tool.hatch.version]
source = "vcs"          # reads the nearest v*.*.* git tag
```

If you previously had `version = "1.2.3"` in `[project]`, delete that line and add it to `dynamic`.

### 2. `.github/workflows/version-bump.yml`

Create this file exactly as shown. It runs on every push to `main`, reads the bump level from the merge commit subject, computes the next version, and pushes the tag back to the repository.

```yaml
name: Version Bump

on:
  push:
    branches:
      - main

jobs:
  tag:
    runs-on: ubuntu-latest
    permissions:
      contents: write          # required to push tags
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0       # REQUIRED — shallow clones do not include tags

      - name: Compute next tag
        id: next
        run: |
          # Take the highest existing vX.Y.Z tag and bump it. The bump level comes
          # from the (squash) merge commit subject:
          #   [major] -> X+1.0.0   [minor] -> X.Y+1.0   default -> X.Y.Z+1
          latest=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -1)
          latest=${latest:-v0.0.0}
          echo "Latest tag: $latest"
          version="${latest#v}"
          IFS='.' read -r major minor patch <<< "$version"
          msg=$(git log -1 --pretty=%s)
          if echo "$msg" | grep -q '\[major\]'; then
            echo "Bump level: major"
            next="$((major + 1)).0.0"
          elif echo "$msg" | grep -q '\[minor\]'; then
            echo "Bump level: minor"
            next="${major}.$((minor + 1)).0"
          else
            echo "Bump level: patch"
            next="${major}.${minor}.$((patch + 1))"
          fi
          echo "Next version: $next"
          echo "tag=v$next" >> "$GITHUB_OUTPUT"

      - name: Push tag
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git tag "${{ steps.next.outputs.tag }}"
          git push origin "${{ steps.next.outputs.tag }}"
```

---

## Ensuring a tag only ever names a green commit

The `on: push` workflow above will happily tag a commit whose tests are failing. You
want a tag to only ever name a commit that passed CI. There are two ways to guarantee
that — and the choice has a direct cost in **how many times your tests run**.

### Recommended: gate at merge with branch protection

Make the CI check **required** via branch protection on `main`, so a commit physically
cannot reach `main` unless its PR passed CI. Then the `on: push` trigger is already
safe — every push to `main` is a known-good, already-tested commit — and Version Bump
needs no CI gate at all. This is what this repo does.

Configure branch protection on `main` (GitHub → Settings → Branches, or the API):

- **Require status checks to pass before merging** → add your CI job (here, `test`).
- **Require branches to be up to date before merging** (`strict: true`) → so each PR is
  re-tested against the current tip of `main` before it can merge. Without this, a PR can
  merge on a CI run taken against a stale base.
- **Do not allow bypassing the above** (`enforce_admins: true`) → so even admins go
  through a passing PR.

```bash
gh api -X PUT repos/<owner>/<repo>/branches/main/protection \
  -F required_status_checks.strict=true \
  -F 'required_status_checks.checks[][context]=test' \
  -F enforce_admins=true \
  -F required_pull_request_reviews= -F restrictions=
```

With this in place, keep Version Bump on the plain `on: push: branches: [main]` trigger
shown earlier — no `workflow_run`, no `if` conclusion guard, no `ref: head_sha` (the
default checkout on a push *is* the merge commit, so `git log -1 --pretty=%s` reads the
squash-merge subject correctly).

**Why this is preferred: tests run once.** CI runs on the PR and nowhere else. The
`workflow_run` variant below re-runs the entire CI suite a second time on the
post-merge push, doubling test time and runner cost for every merge.

### Alternative: gate on a CI `workflow_run` (no branch protection)

If you cannot rely on branch protection (e.g. CI is not a required check, or direct
pushes to `main` happen), trigger the bump from **CI success** instead of the raw push,
so a tag only names a commit that passed:

```yaml
on:
  workflow_run:
    workflows: ["CI"]        # must match the `name:` of your CI workflow
    types: [completed]
    branches: [main]

jobs:
  tag:
    # Only tag when CI succeeded — never tag a broken build
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
          ref: ${{ github.event.workflow_run.head_sha }}   # the commit CI passed
      # ... "Compute next tag" and "Push tag" steps unchanged
```

This requires your CI workflow to also trigger on `push: [main]` (so there is a
main-branch run for `workflow_run` to fire from), which is precisely the second test run
the recommended approach avoids. `ref: head_sha` is important here: it checks out the
merge commit on `main` so the marker logic keeps reading the PR title.

---

## Versioning scheme

| Situation | How to handle |
|---|---|
| Backward-compatible fix | Merge normally — patch bumped automatically (`v1.4.2` → `v1.4.3`) |
| New backward-compatible feature | Put `[minor]` in the PR title (`v1.4.2` → `v1.5.0`) |
| Breaking change | Put `[major]` in the PR title (`v1.4.2` → `v2.0.0`) |

The marker can appear anywhere in the subject line, so a title like
`feat: streaming actuator updates [minor]` works. If both `[major]` and
`[minor]` are present, `[major]` wins.

You can still push a tag manually if you need to jump to a specific version
(`git tag v2.0.0 && git push origin v2.0.0`); the next automated bump continues
from whatever the highest tag is.

---

## Accessing the version at runtime

Because the version is injected at build time (when the package is installed into a venv), it is available via `importlib.metadata`:

```python
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("your-package-name")
except PackageNotFoundError:
    __version__ = "unknown"   # package not installed (running from bare source)
```

This works in any installed environment (`uv sync`, `pip install -e .`, etc.).

---

## Shipping the version in a container

hatch-vcs derives the version from git tags **at build time**. That is fine for a
local `uv sync`, but in a Docker build you **must not** copy `.git` into the image.
Two failure modes follow if you do:

- **No tags in the build context** → setuptools-scm cannot resolve a version and the
  build fails (or, with a partial clone, installs as `unknown`).
- **`.git` present but the tree is dirty** → because `.dockerignore` legitimately omits
  many tracked files (`tests/`, `docs/`, `alembic/`, …), the in-image working tree looks
  *modified*, so hatch-vcs reports a dirty dev version like
  `0.3.4.dev0+g<sha>.d<date>` **even when HEAD is exactly on a release tag**. This is a
  silent correctness bug — the image builds and ships the wrong version.

So: exclude `.git` via `.dockerignore` and inject the version the tagging workflow
computed as a build arg, exposing it through setuptools-scm's pretend-version variable:

```dockerfile
# Dockerfile
ARG VERSION=0.0.0+local
RUN SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION \
    uv sync --frozen --no-dev
```

```yaml
# in the build/deploy workflow — VERSION must be the bare number, no leading "v"
- uses: docker/build-push-action@v6
  with:
    build-args: VERSION=${{ steps.tag.outputs.version }}
```

Use the **generic** `SETUPTOOLS_SCM_PRETEND_VERSION`, not the per-distribution
`SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<NAME>` form. hatch-vcs does not forward the
distribution name to setuptools-scm, so the `_FOR_<NAME>` variable is silently ignored
and the build falls through to git detection (the failure modes above). The generic
variable is unambiguous in a single-project image. With this in place the built image
reports the correct version at runtime through the same `importlib.metadata` accessor
above.

> **Where does `VERSION` come from?** A deploy workflow that runs *after* tagging can
> re-read the newest tag itself — `git tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname | head -1`
> — and strip the leading `v`. Do not try to trigger deploy from `on: push: tags:`
> (see the Gotcha below); chain it off the tagging workflow's completion instead.

---

## Gotchas

- **`fetch-depth: 0` is mandatory.** GitHub Actions checks out a shallow clone by default. Without the full history, `git tag --list` returns nothing and every run would create `v0.1.0`.
- **Tag format must match `v[0-9]*.[0-9]*.[0-9]*`.** Other tag formats (e.g. `release-1.0`) are silently ignored by the workflow.
- **`sort -V` handles double-digit components correctly.** `v0.1.10` sorts after `v0.1.9`, unlike plain alphabetic sort.
- **The workflow needs `contents: write`.** Without this permission, `git push` will fail with a 403.
- **No tag → the seed is `v0.0.0`, then the first bump applies.** A brand-new repository gets `v0.0.1` on its first (patch) merge, `v0.1.0` if that PR title carries `[minor]`, or `v1.0.0` for `[major]`.
- **The marker is read from the merge commit, not the PR branch.** With squash merges the subject is the PR title; with merge commits it is the merge message. Rebase/fast-forward merges carry the last commit subject instead — prefer squash merges so the PR title is authoritative.
- **hatch-vcs must be a build dependency, not a runtime dependency.** It belongs in `[build-system] requires`, not `[project] dependencies`.
- **A tag pushed with the default `GITHUB_TOKEN` will not trigger another workflow.** GitHub suppresses workflow events for refs pushed by `GITHUB_TOKEN` (loop prevention), so a downstream `on: push: tags: 'v*'` job will silently never run. Chain anything that consumes the tag (e.g. a deploy/build) off the tagging workflow's completion instead — `on: workflow_run: { workflows: ["Version Bump"], types: [completed] }` with an `if: ...conclusion == 'success'` guard — or push the tag with a PAT / GitHub App token if you genuinely need the tag-push event.
