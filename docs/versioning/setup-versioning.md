Apply the automated git-tag versioning pattern to this Python project. Follow every step below exactly.

## Step 1 — Edit `pyproject.toml`

1. Find and remove any static `version = "..."` line in `[project]`.
2. Add `"version"` to the `dynamic` list in `[project]` (create the list if absent).
3. Replace or add the `[build-system]` table:
   ```toml
   [build-system]
   requires = ["hatchling", "hatch-vcs"]
   build-backend = "hatchling.build"
   ```
4. Add the hatch-vcs version source:
   ```toml
   [tool.hatch.version]
   source = "vcs"
   ```

Make only these targeted edits — do not reformat or reorder the rest of `pyproject.toml`.

## Step 2 — Create `.github/workflows/version-bump.yml`

Create the file with exactly this content (do not alter the workflow logic):

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
      contents: write
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0

      - name: Compute next tag
        id: next
        run: |
          # Bump level comes from the (squash) merge commit subject:
          #   [major] -> X+1.0.0   [minor] -> X.Y+1.0   default -> X.Y.Z+1
          latest=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' | sort -V | tail -1)
          latest=${latest:-v0.0.0}
          version="${latest#v}"
          IFS='.' read -r major minor patch <<< "$version"
          msg=$(git log -1 --pretty=%s)
          if echo "$msg" | grep -q '\[major\]'; then
            next="$((major + 1)).0.0"
          elif echo "$msg" | grep -q '\[minor\]'; then
            next="${major}.$((minor + 1)).0"
          else
            next="${major}.${minor}.$((patch + 1))"
          fi
          echo "tag=v$next" >> "$GITHUB_OUTPUT"

      - name: Push tag
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git tag "${{ steps.next.outputs.tag }}"
          git push origin "${{ steps.next.outputs.tag }}"
```

### Optional — gate tagging on a green CI run

If the project has a CI workflow, prefer triggering the bump from CI success so a tag
only ever names a commit that passed. Replace the `on:` block above with the
following, and add the `if` guard plus `ref` to the checkout (everything else stays
the same):

```yaml
on:
  workflow_run:
    workflows: ["CI"]        # must match the CI workflow's `name:`
    types: [completed]
    branches: [main]

jobs:
  tag:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
          ref: ${{ github.event.workflow_run.head_sha }}
      # ... "Compute next tag" and "Push tag" steps unchanged
```

`ref: head_sha` keeps `git log -1 --pretty=%s` pointing at the merge subject so the
marker logic still works. See `VERSIONING.md` → "Variant: gate tagging on a green CI
run".

## Step 3 — Add runtime version accessor

Find the package name from `pyproject.toml` `[project] name`. Then locate the package's top-level `__init__.py` and add (or replace any existing `__version__` assignment with):

```python
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("PACKAGE-NAME")
except PackageNotFoundError:
    __version__ = "unknown"
```

If there is no `__init__.py`, skip this step and say so.

## Step 4 — (Optional) Ship the version in a Docker image

Skip this step entirely if the project does not build a container image — say so in
the summary.

hatch-vcs reads the version from git tags at build time, but a Docker build has no
tags, so the package would install without a resolvable version and the accessor
above would return `unknown`. Bridge it:

1. Exclude `.git` from the image (add `.git` to `.dockerignore`). Shipping `.git`
   makes hatch-vcs read a *dirty* tree — `.dockerignore` omits tracked files, so the
   in-image tree looks modified and you get a `…dev0+g<sha>.d<date>` version even on a
   release tag. Do not install `git` in the image for versioning purposes.
2. In the `Dockerfile`, accept a `VERSION` build arg and pass it to the install step
   via setuptools-scm's pretend-version variable:
   ```dockerfile
   ARG VERSION=0.0.0+local
   RUN SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION uv sync --frozen --no-dev
   ```
   Use the **generic** `SETUPTOOLS_SCM_PRETEND_VERSION`. The per-distribution
   `SETUPTOOLS_SCM_PRETEND_VERSION_FOR_<DIST>` form is silently ignored under hatch-vcs
   (it does not forward the dist name to setuptools-scm).
3. In the build/deploy workflow, pass the bare version number (no leading `v`) as the
   build arg, e.g. `build-args: VERSION=${{ steps.tag.outputs.version }}`.

Do not wire a deploy to `on: push: tags:` — tags pushed by `GITHUB_TOKEN` do not
trigger workflows. Chain off the tagging workflow's completion instead. See
`VERSIONING.md` → "Shipping the version in a container".

## Step 5 — Confirm and summarise

After making all edits, report:
- What changed in `pyproject.toml` (show the diff if small)
- Whether `.github/workflows/version-bump.yml` was created or already existed
- The package name used in the `importlib.metadata` call
- Whether the CI-gate variant (Step 2 optional) was applied
- Whether the Docker version bridge (Step 4) was applied or skipped (`.git` excluded
  from the image and the generic `SETUPTOOLS_SCM_PRETEND_VERSION` used)
- Any step that was skipped and why

Do not commit the changes — leave that to the user.
