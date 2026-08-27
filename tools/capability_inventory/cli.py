"""Command-line entry point for the capability inventory.

    uv run capability-inventory [--skip-probes] [--skip-db] [--only <pattern>]

The harness fails loudly. Missing configuration, an unreachable database and an
unreachable instance are all errors, not degraded runs -- a report that looks
complete but silently omits a phase is worse than no report. To produce a
report *without* a phase, say so explicitly with ``--skip-db`` or
``--skip-probes``, and the output records the omission in its header, in every
affected endpoint section, and in Gaps.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import (
    annotate,
    data_shape,
    dead_surface,
    indexes,
    load,
    probes,
    render,
    static_surface,
    verdict,
)
from .models import DataShape, Inventory, ProbeResult, Unknown

_DEFAULT_MARKDOWN = Path("docs/capability-inventory.md")
_DEFAULT_JSON = Path("docs/capability-inventory.json")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="capability-inventory",
        description=(
            "Produce an annotated capability inventory of the media-api HTTP surface: "
            "what each endpoint is, what it costs, what data is reliably present "
            "behind it, and what a front end can responsibly build on it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment:\n"
            f"  {data_shape.ENV_VAR}   read-only DSN for Phase 3 (data shape)\n"
            f"  {probes.BASE_URL_ENV}         base URL of the instance for Phase 4 (probes)\n"
            f"  {probes.TOKEN_ENV}            bearer token for Phase 4; omit when the\n"
            "                              instance runs with AUTH_DISABLED=true\n"
            "\nNothing is read from a committed file and nothing secret is written to one."
        ),
    )
    parser.add_argument(
        "--skip-probes",
        action="store_true",
        help=(
            "skip Phase 4. No instance is contacted; every 'Measured' line reads "
            "UNKNOWN and says why."
        ),
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help=(
            "skip Phase 3. No database is contacted; row counts, fill rates and "
            "collection sizes read UNKNOWN."
        ),
    )
    parser.add_argument(
        "--only",
        metavar="PATTERN",
        help=(
            "restrict the report to routes whose path matches this glob, e.g. "
            "'/api/assets/*'. Phases still run, but only against the matching subset."
        ),
    )
    parser.add_argument(
        "--frontend-path",
        metavar="DIR",
        type=Path,
        help=(
            "a consumer checkout to grep for call sites in Phase 5. Both URL "
            "literals and generated-client operationIds are searched."
        ),
    )
    parser.add_argument(
        "--access-log",
        metavar="FILE",
        type=Path,
        help=(
            "an access log to parse for Phase 5. Request paths are normalised back "
            "to route templates before counting."
        ),
    )
    parser.add_argument(
        "--probes-file",
        metavar="FILE",
        type=Path,
        default=Path(__file__).with_name("probes.yaml"),
        help="probe definitions (default: the probes.yaml beside this package).",
    )
    parser.add_argument(
        "--markdown-out",
        metavar="FILE",
        type=Path,
        default=_DEFAULT_MARKDOWN,
        help=f"where to write the report (default: {_DEFAULT_MARKDOWN}).",
    )
    parser.add_argument(
        "--json-out",
        metavar="FILE",
        type=Path,
        default=_DEFAULT_JSON,
        help=f"where to write the machine-readable form (default: {_DEFAULT_JSON}).",
    )
    parser.add_argument(
        "--cardinality-scan-limit",
        metavar="N",
        type=int,
        default=5000,
        help=(
            "distinct-value scan cap per column in Phase 3 (default: 5000). Above "
            "this the count is reported as a floor, flagged as capped."
        ),
    )
    parser.add_argument(
        "--from-json",
        metavar="FILE",
        type=Path,
        help=(
            "re-render from a previous run's JSON instead of running any phase. No "
            "database and no instance are contacted, so a change to the report's "
            "presentation produces a diff of presentation alone. Risks and verdicts are "
            "re-derived from the stored measurements, so a change to a threshold in "
            "verdict.py takes effect without re-probing."
        ),
    )
    parser.add_argument(
        "--include-example-values",
        action="store_true",
        help=(
            "record the most common values of low-cardinality columns. OFF by "
            "default: those are rows out of the probed database and the report is "
            "committed. Distinct counts and fill rates are recorded either way, so "
            "the report still says whether a column can become a facet."
        ),
    )
    parser.add_argument(
        "--repo-root",
        metavar="DIR",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: the current directory).",
    )
    return parser


def _repo_root(candidate: Path) -> Path:
    """Validate that the given directory really is this repository.

    Raises:
        SystemExit: If the directory does not look like the media-api checkout.
    """
    root = candidate.resolve()
    if not (root / "app" / "main.py").is_file() or not (root / "pyproject.toml").is_file():
        raise SystemExit(
            f"error: {root} does not look like the media-api repository "
            "(no app/main.py). Run the harness from the repository root, or pass "
            "--repo-root."
        )
    return root


def main(argv: list[str] | None = None) -> int:
    """Run the harness.

    Args:
        argv: Command-line arguments, or None to read ``sys.argv``.

    Returns:
        Process exit status: 0 on success, 1 on a handled failure.
    """
    args = build_parser().parse_args(argv)

    try:
        root = _repo_root(args.repo_root)
        inventory = _rerender(args, root) if args.from_json else _run(args, root)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    markdown_path = (
        args.markdown_out if args.markdown_out.is_absolute() else root / args.markdown_out
    )
    json_path = args.json_out if args.json_out.is_absolute() else root / args.json_out
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render.to_markdown(inventory), encoding="utf-8")
    json_path.write_text(render.to_json(inventory), encoding="utf-8")

    print(
        f"Wrote {_display(markdown_path, root)} and {_display(json_path, root)}: "
        f"{len(inventory.endpoints)} endpoints, "
        f"phases {', '.join(inventory.phases_run)}"
        + (f"; skipped {', '.join(inventory.phases_skipped)}" if inventory.phases_skipped else "")
    )
    return 0


def _display(path: Path, root: Path) -> str:
    """Render an output path relative to the repository when it is inside it."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _rerender(args: argparse.Namespace, root: Path) -> Inventory:
    """Rebuild an inventory from a previous run's JSON, running no phase.

    Risks and verdicts are re-derived rather than carried across verbatim. They
    are a deterministic function of the stored surface, annotation, probes and
    data shape -- deriving them again is not a fresh measurement, and it means a
    change to a threshold in :mod:`.verdict` shows up on the next render instead
    of waiting for someone to re-run the probe suite.

    Raises:
        RuntimeError: If ``--from-json`` is combined with a flag that only makes
            sense for a live run.
    """
    for flag, value in (
        ("--frontend-path", args.frontend_path),
        ("--access-log", args.access_log),
    ):
        if value:
            raise RuntimeError(
                f"{flag} cannot be combined with --from-json: Phase 5 evidence is read "
                "from the stored artefact, not gathered again."
            )

    source = args.from_json if args.from_json.is_absolute() else root / args.from_json
    inventory = load.from_json(source)

    endpoints = tuple(
        verdict.apply(
            surface=record.surface,
            annotation=record.annotation,
            probes=record.probes,
            shape=inventory.data_shape,
            usage=record.usage,
        )
        for record in inventory.endpoints
    )
    if args.only:
        endpoints = tuple(e for e in endpoints if fnmatch.fnmatch(e.surface.path, args.only))
        if not endpoints:
            raise RuntimeError(
                f"--only {args.only!r} matched no routes in {source}. Patterns are globs "
                "over the route path, e.g. '/api/assets/*'."
            )

    notes = tuple(n for n in inventory.notes if not n.startswith("Re-rendered")) + (
        f"Re-rendered from `{args.from_json}` with no phase re-run; timings are those "
        "of the recorded run.",
    )
    return replace(inventory, endpoints=endpoints, notes=notes)


def _run(args: argparse.Namespace, root: Path) -> Inventory:
    """Execute the requested phases and assemble the inventory.

    Raises:
        RuntimeError: If a phase that was not skipped cannot run.
    """
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    phases_run: list[str] = []
    phases_skipped: list[str] = []
    unknowns: list[Unknown] = []
    notes: list[str] = []

    # -- Phase 1 -----------------------------------------------------------
    api = static_surface.load_app()
    routes, _spec = static_surface.collect(api)
    phases_run.append("1 (static surface)")

    if args.only:
        pattern = args.only
        routes = tuple(r for r in routes if fnmatch.fnmatch(r.path, pattern))
        if not routes:
            raise RuntimeError(
                f"--only {pattern!r} matched no routes. Patterns are globs over the "
                "route path, e.g. '/api/assets/*'."
            )
        notes.append(f"Filtered to `--only {pattern}` — {len(routes)} of the full surface.")

    # -- Phase 2 -----------------------------------------------------------
    index_inventory = indexes.from_metadata() + indexes.from_migrations(
        root / "alembic" / "versions"
    )
    lookup = indexes.IndexLookup(index_inventory)
    graph = annotate.CodeGraph(root / "app", root)
    analyser = annotate.RouteAnalyser(graph, lookup)
    annotations = {route.key: analyser.analyse(route) for route in routes}
    phases_run.append("2 (code annotation)")

    # -- Phase 3 -----------------------------------------------------------
    shape: DataShape | None = None
    if args.skip_db:
        phases_skipped.append("3 (data shape)")
        unknowns.append(
            Unknown(
                scope="Phase 3",
                question="row counts, fill rates, cardinality and collection sizes",
                resolution=(
                    f"re-run without --skip-db and with {data_shape.ENV_VAR} set to a "
                    "read-only connection string"
                ),
            )
        )
    else:
        tables = {
            table
            for annotation in annotations.values()
            for query in annotation.queries
            for table in query.tables
        }
        noload = {
            (table, field.name)
            for route in routes
            for response in route.responses
            for field in response.fields
            if field.conditional_on
            for table in _tables_for(annotations[route.key])
        }
        shape = data_shape.collect(
            tables=tables,
            noload_columns=noload,
            cardinality_scan_limit=args.cardinality_scan_limit,
            include_example_values=args.include_example_values,
        )
        phases_run.append("3 (data shape)")
        if not args.include_example_values:
            notes.append(
                "Example column values were withheld (`--include-example-values` "
                "not passed): distinct counts and fill rates are recorded, the "
                "underlying rows are not."
            )

    # -- Phase 4 -----------------------------------------------------------
    probe_results: dict[str, list[ProbeResult]] = {}
    if args.skip_probes:
        phases_skipped.append("4 (timed probes)")
        unknowns.append(
            Unknown(
                scope="Phase 4",
                question="latency percentiles, payload sizes and Range handling",
                resolution=(
                    f"re-run without --skip-probes and with {probes.BASE_URL_ENV} "
                    "pointing at a running instance backed by a realistic library"
                ),
            )
        )
    else:
        config = probes.load_config(args.probes_file)
        base_url = probes.resolve_base_url()
        token = os.environ.get(probes.TOKEN_ENV) or None
        results, probe_unknowns = probes.run_suite(config, base_url, token)
        unknowns.extend(probe_unknowns)
        by_key = {route.key: route for route in routes}
        unmatched: list[str] = []
        for result in results:
            route = by_key.get(result.endpoint_key)
            if route is None:
                unmatched.append(f"{result.name} ({result.endpoint_key})")
                continue
            probe_results.setdefault(route.key, []).append(result)
        for label in unmatched:
            unknowns.append(
                Unknown(
                    scope="Phase 4",
                    question=f"which endpoint probe `{label}` exercised",
                    resolution=(
                        "the probe's method and path do not match any route in this "
                        "run; correct the path in probes.yaml, or drop --only"
                    ),
                )
            )
        phases_run.append("4 (timed probes)")
        notes.append(f"Probed `{base_url}` with {len(results)} probe(s).")

    # -- Phase 5 -----------------------------------------------------------
    usage = dead_surface.from_repository(routes, root)
    if args.frontend_path:
        usage = dead_surface.from_consumer(routes, args.frontend_path.resolve(), usage)
        notes.append(f"Phase 5 searched consumer checkout `{args.frontend_path}`.")
    if args.access_log:
        usage = dead_surface.from_access_log(routes, args.access_log.resolve(), usage)
        notes.append(f"Phase 5 parsed access log `{args.access_log}`.")
    if not args.frontend_path and not args.access_log:
        unknowns.append(
            Unknown(
                scope="Phase 5",
                question="whether an endpoint has any real caller",
                resolution=(
                    "re-run with --frontend-path pointing at a consumer checkout, or "
                    "--access-log pointing at a log with real traffic; in-repository "
                    "evidence alone cannot see callers that live in other repositories"
                ),
            )
        )
    phases_run.append("5 (dead surface)")

    records = tuple(
        verdict.apply(
            surface=route,
            annotation=annotations[route.key],
            probes=tuple(probe_results.get(route.key, ())),
            shape=shape,
            usage=usage.get(route.key),
        )
        for route in routes
    )

    return Inventory(
        generated_from=f"{root.name} @ app.openapi()",
        app_version=_app_version(),
        phases_run=tuple(phases_run),
        phases_skipped=tuple(phases_skipped),
        endpoints=records,
        indexes=index_inventory,
        data_shape=shape,
        unknowns=tuple(unknowns),
        notes=tuple(notes),
    )


def _tables_for(annotation: object) -> set[str]:
    """Tables an annotation's queries touch."""
    queries = getattr(annotation, "queries", ())
    return {table for query in queries for table in query.tables}


def _app_version() -> str:
    """The application version, as the app itself reports it."""
    try:
        from app.config import get_version  # noqa: PLC0415 -- deferred with the app.

        return str(get_version())
    except Exception:  # pragma: no cover - defensive
        return "unknown"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
