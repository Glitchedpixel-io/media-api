"""Phase 5 -- evidence that an endpoint is, or is not, used.

Three sources, in descending order of how much they prove:

1. **A consumer codebase** (``--frontend-path``). Both raw URL literals and
   generated-client ``operationId`` symbols are searched, and reported
   separately: a repository that consumes a generated SDK contains no URLs at
   all, so searching only for paths would declare the whole API dead.
2. **Access logs** (``--access-log``). Request paths are normalised back to
   their route templates, so ``/api/assets/4213`` counts as a hit on
   ``/api/assets/{asset_id}``. This is the only source that catches non-UI
   consumers -- workers, scanners, scheduled jobs.
3. **This repository alone**, the fallback. Tests and internal references only.

The fallback is labelled ``weak`` everywhere it appears, and for a good reason:
several endpoints here exist for machine consumers that live in other
repositories entirely -- ``POST /api/transform_requests/claim`` and the
heartbeat routes are a worker pull queue, and nothing in this repository or in
any front end would ever call them. Absence of evidence from a code-only run is
not evidence of absence, and the report says so at the top of the section.

Nothing is ever deleted. The output is a list of candidates and the evidence
behind each.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import RouteSurface, UsageEvidence

# Files worth searching in a consumer repository.
_SOURCE_SUFFIXES = frozenset(
    {
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".vue",
        ".svelte",
        ".py",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".swift",
        ".dart",
        ".rb",
        ".php",
        ".cs",
        ".json",
        ".yaml",
        ".yml",
        ".graphql",
        ".astro",
        ".html",
    }
)

_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "__pycache__",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "coverage",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".claude",
    }
)

# Combined access-log formats: a quoted request line is the common denominator
# across nginx combined, Apache combined and most JSON loggers' `request` field.
_ACCESS_LOG_REQUEST = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD) ([^ "?]+)')

_NUMERIC_SEGMENT = re.compile(r"^\d+$")


def _search_root(root: Path) -> list[Path]:
    """Collect searchable files under a root, skipping vendored directories."""
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _SOURCE_SUFFIXES:
            continue
        if any(part in _SKIP_DIRECTORIES for part in path.parts):
            continue
        out.append(path)
    return sorted(out)


def _path_pattern(route_path: str) -> re.Pattern[str]:
    r"""Compile a route template into a pattern that matches real call sites.

    ``/api/assets/{asset_id}/streams`` becomes ``/api/assets/[^/"\'\s]+/streams``,
    which matches ``f"/api/assets/{asset.id}/streams"`` in a test and
    ```/api/assets/${id}/streams`` `` in a TypeScript template literal, but does
    *not* match the bare prefix ``/api/assets/``.

    The obvious alternative -- searching for the longest literal prefix -- is
    useless here. Every route under ``/api/assets`` shares the prefix
    ``/api/assets/``, and ``app_factory.py`` contains that string because it is
    where the router is mounted, so a prefix search reports every asset endpoint
    as referenced by the code that declares it.
    """
    parts = []
    for segment in route_path.split("/"):
        if not segment:
            continue
        if segment.startswith("{") and segment.endswith("}"):
            parts.append(r"[^/\"\'\s]+")
        else:
            parts.append(re.escape(segment))
    return re.compile("/" + "/".join(parts))


def _scan_patterns(files: list[Path], patterns: dict[str, re.Pattern[str]]) -> dict[str, set[str]]:
    """Find which route patterns appear in which files."""
    hits: dict[str, set[str]] = {key: set() for key in patterns}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:  # pragma: no cover - unreadable file
            continue
        for key, pattern in patterns.items():
            if pattern.search(text):
                hits[key].add(str(path))
    return hits


def _scan_symbols(files: list[Path], symbols: dict[str, str]) -> dict[str, set[str]]:
    """Find which operationIds appear in which files.

    Matched on a word boundary so ``get_asset`` does not also count as a hit for
    ``get_asset_titles``.
    """
    compiled = {
        key: re.compile(rf"\b{re.escape(symbol)}\b") for key, symbol in symbols.items() if symbol
    }
    return _scan_patterns(files, compiled)


def from_repository(routes: tuple[RouteSurface, ...], repo_root: Path) -> dict[str, UsageEvidence]:
    """Look for references inside this repository only.

    This is the weakest evidence available and is labelled as such: it finds the
    tests that exercise a route and any internal reference, and nothing else.
    """
    # Only directories that could hold a *caller*. `app/` is excluded on
    # purpose: a router declaring its own path is not evidence that anything
    # calls it, and including it marks the entire surface as referenced.
    searchable: list[Path] = []
    for directory in ("tests", "scripts"):
        root = repo_root / directory
        if root.is_dir():
            searchable.extend(_search_root(root))

    patterns = {route.key: _path_pattern(route.path) for route in routes}
    symbols = {route.key: route.operation_id or "" for route in routes}
    path_hits = _scan_patterns(searchable, patterns)
    symbol_hits = _scan_symbols(searchable, symbols)

    out: dict[str, UsageEvidence] = {}
    for route in routes:
        found = path_hits[route.key] | symbol_hits[route.key]
        tests = sorted(
            str(Path(p).relative_to(repo_root))
            for p in found
            if "tests" in Path(p).relative_to(repo_root).parts
        )
        callers = sorted(
            str(Path(p).relative_to(repo_root))
            for p in found
            if "tests" not in Path(p).relative_to(repo_root).parts
        )
        referenced = bool(tests or callers)
        out[route.key] = UsageEvidence(
            endpoint_key=route.key,
            referenced=referenced,
            strength="weak",
            callers=tuple(callers),
            test_references=tuple(tests),
            note=(
                "in-repository evidence only, from tests/ and scripts/. The app "
                "tree is excluded: a router declaring its own path says nothing "
                "about whether anything calls it. An endpoint called exclusively "
                "by an external worker or front end therefore looks unreferenced "
                "here."
            ),
        )
    return out


def from_consumer(
    routes: tuple[RouteSurface, ...],
    consumer_root: Path,
    existing: dict[str, UsageEvidence],
) -> dict[str, UsageEvidence]:
    """Search a consumer codebase for call sites.

    Args:
        routes: The API surface.
        consumer_root: Root of the consuming repository.
        existing: Evidence gathered so far, which is merged into.

    Returns:
        Updated evidence.

    Raises:
        RuntimeError: If the path does not exist. A typo here would otherwise
            produce a confident, entirely wrong list of dead endpoints.
    """
    if not consumer_root.is_dir():
        raise RuntimeError(
            f"Consumer path {consumer_root} does not exist. " "Correct it or omit --frontend-path."
        )
    files = _search_root(consumer_root)
    if not files:
        raise RuntimeError(
            f"No searchable source files under {consumer_root}. "
            "Check the path points at a checkout rather than a build directory."
        )

    url_hits = _scan_patterns(files, {r.key: _path_pattern(r.path) for r in routes})
    op_hits = _scan_symbols(files, {r.key: r.operation_id or "" for r in routes})

    out = dict(existing)
    for route in routes:
        urls = sorted(str(Path(p).relative_to(consumer_root)) for p in url_hits[route.key])
        ops = sorted(str(Path(p).relative_to(consumer_root)) for p in op_hits[route.key])
        previous = existing.get(route.key)
        callers = tuple(
            [f"{consumer_root.name}: {p} (url)" for p in urls]
            + [f"{consumer_root.name}: {p} (operationId)" for p in ops]
        )
        out[route.key] = UsageEvidence(
            endpoint_key=route.key,
            referenced=bool(callers) or bool(previous and previous.referenced),
            strength="strong",
            callers=callers + (previous.callers if previous else ()),
            test_references=previous.test_references if previous else (),
            note=(
                f"searched {len(files)} source files under {consumer_root} for both "
                "URL literals and generated-client operationIds"
            ),
        )
    return out


def _normalise_logged_path(path: str, templates: list[tuple[str, list[str]]]) -> str | None:
    """Match a logged request path back to its route template."""
    segments = [s for s in path.split("/") if s]
    for template, template_segments in templates:
        if len(template_segments) != len(segments):
            continue
        matched = True
        for expected, actual in zip(template_segments, segments, strict=True):
            if expected.startswith("{"):
                continue
            if expected != actual:
                matched = False
                break
        if matched:
            return template
    return None


def from_access_log(
    routes: tuple[RouteSurface, ...],
    log_path: Path,
    existing: dict[str, UsageEvidence],
) -> dict[str, UsageEvidence]:
    """Count requests per route template from an access log.

    Args:
        routes: The API surface.
        log_path: A combined-format or JSON access log.
        existing: Evidence gathered so far.

    Returns:
        Updated evidence.

    Raises:
        RuntimeError: If the log is missing or no request lines parse out of it.
    """
    if not log_path.is_file():
        raise RuntimeError(f"Access log {log_path} does not exist.")

    templates = [(r.path, [s for s in r.path.split("/") if s]) for r in routes]
    # Longest templates first so a literal segment wins over a placeholder.
    templates.sort(key=lambda t: (-sum(1 for s in t[1] if not s.startswith("{")), t[0]))

    counts: dict[str, int] = {}
    parsed = 0
    with log_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            match = _ACCESS_LOG_REQUEST.search(line)
            if not match:
                continue
            parsed += 1
            template = _normalise_logged_path(match.group(1), templates)
            if template:
                counts[template] = counts.get(template, 0) + 1

    if parsed == 0:
        raise RuntimeError(
            f"No request lines could be parsed out of {log_path}. The harness "
            'expects a quoted request line (e.g. "GET /api/assets/ HTTP/1.1"), '
            "which nginx and Apache combined formats both produce."
        )

    out = dict(existing)
    for route in routes:
        hits = counts.get(route.path, 0)
        previous = existing.get(route.key)
        caller = (f"access log: {hits} request(s) to {route.path}",) if hits else ()
        out[route.key] = UsageEvidence(
            endpoint_key=route.key,
            referenced=bool(hits) or bool(previous and previous.referenced),
            strength="strong",
            callers=caller + (previous.callers if previous else ()),
            test_references=previous.test_references if previous else (),
            note=(
                f"{parsed} request lines parsed from {log_path.name}; paths were "
                "normalised back to route templates before counting"
            ),
        )
    return out
