"""Command line entry point for the design fixture generator.

Runs the API in-process -- via Starlette's TestClient, with no uvicorn and no
listening socket -- against whichever database the DSN names, and writes the captured
response bodies to ``--out``.

In-process rather than over HTTP for two reasons: nothing has to be running before the
tool is useful, and no port is claimed, so it is safe to run alongside another session
working in the same repository.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_OUT = "./design-fixtures"
DSN_ENV_VAR = "DESIGN_FIXTURES_DATABASE_URL"
OUT_ENV_VAR = "DESIGN_FIXTURES_OUT"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        argv: Argument vector, or None to read from sys.argv.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog="design-fixtures",
        description=(
            "Capture real API responses as design fixtures. Read-only: every capture "
            "is a GET and every supporting database query is a SELECT."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help=(
            f"Directory to write fixtures into. Falls back to ${OUT_ENV_VAR}, then to "
            f"{DEFAULT_OUT}. Point it at the consuming project's fixture directory."
        ),
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help=(
            f"SQLAlchemy URL for the database to read. Falls back to ${DSN_ENV_VAR}. "
            f"Use a read-only role."
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=50,
        help=(
            "Cap on how many records a per-record fixture set captures. The manifest "
            "records the true total alongside the captured count (default: 50)"
        ),
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Comma-separated case numbers to run, e.g. 1,2,14. Default: all of them.",
    )
    return parser.parse_args(argv)


def _normalise_dsn(dsn: str) -> str:
    """Point a bare postgresql:// URL at the driver this project installs.

    Args:
        dsn: The database URL as given.

    Returns:
        str: The URL with an explicit psycopg driver where one was missing.
    """
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    return dsn


def _resolve_out(raw: str) -> Path:
    """Resolve the output directory and warn if it looks like a worktree accident.

    A relative ``--out`` is resolved against the current directory, so a path meant as
    a sibling of the repository lands somewhere else entirely when the command is run
    from inside ``.claude/worktrees/<name>``. The resolved path is printed either way,
    so a bad run is visible rather than silent.

    Args:
        raw: The --out value as given.

    Returns:
        Path: The absolute output directory.
    """
    out = Path(raw).resolve()
    if ".claude/worktrees" in str(out):
        print(
            f"warning: --out resolved to {out}, which is inside a worktree directory. "
            f"Pass --out explicitly if that is not what you meant.",
            file=sys.stderr,
        )
    return out


def main(argv: list[str] | None = None) -> int:
    """Run the fixture capture.

    Args:
        argv: Argument vector, or None to read from sys.argv.

    Returns:
        int: Process exit code.
    """
    args = _parse_args(argv)

    dsn = args.database_url or os.environ.get(DSN_ENV_VAR)
    if not dsn:
        print(
            f"error: no database URL. Pass --database-url or set ${DSN_ENV_VAR}.",
            file=sys.stderr,
        )
        return 2
    dsn = _normalise_dsn(dsn)

    out_dir = _resolve_out(args.out or os.environ.get(OUT_ENV_VAR) or DEFAULT_OUT)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"writing fixtures to {out_dir}")

    # The app reads its configuration at import time through pydantic-settings and
    # caches it, so these have to be in the environment before `app.main` is imported.
    # TEST_DATABASE_URL is set as well as DATABASE_URL because the settings loader
    # resolves the database with AliasChoices("TEST_DATABASE_URL", "DATABASE_URL") --
    # a TEST_DATABASE_URL left in the shell profile would otherwise win silently and
    # the capture would run against the wrong database while still looking healthy.
    os.environ["APP_ENV"] = "development"
    os.environ["AUTH_DISABLED"] = "true"
    os.environ["DATABASE_URL"] = dsn
    os.environ["TEST_DATABASE_URL"] = dsn

    from fastapi.testclient import TestClient  # noqa: PLC0415 — see comment above

    from app.main import api  # noqa: PLC0415 — must follow the environment setup

    from tools.design_fixtures.capture import CaseContext, FixtureCapture, parsed  # noqa: PLC0415
    from tools.design_fixtures.cases import CASES  # noqa: PLC0415
    from tools.design_fixtures.manifest import ONE_MEGABYTE, human_size, render  # noqa: PLC0415
    from tools.design_fixtures.selectors import Selectors  # noqa: PLC0415

    selected: set[int] | None = None
    if args.only:
        selected = {int(part) for part in args.only.split(",") if part.strip()}

    selectors = Selectors(dsn)
    try:
        # The context manager form matters: it runs the lifespan, which is what calls
        # init_db(). Without it every data endpoint fails on an uninitialised session
        # factory while /api/ping still answers 200.
        with TestClient(api) as client:
            capture = FixtureCapture(client, out_dir)

            # Smoke-test a real data endpoint before trusting any measurement. /api/ping
            # answers without touching the database and would pass against a misconfigured
            # instance.
            status, body = capture.get("/api/title_types")
            doc = parsed(body)
            if status != 200 or not doc:
                print(
                    f"error: smoke test GET /api/title_types returned HTTP {status}. "
                    f"Not capturing against an instance that cannot read data.",
                    file=sys.stderr,
                )
                return 1

            _, version_body = capture.get("/api/version")
            version_doc = parsed(version_body) or {}
            app_version = str(version_doc.get("version", "unknown"))
            print(f"api version {app_version}; smoke test ok")

            ctx = CaseContext(capture=capture, selectors=selectors, max_records=args.max_records)
            for number, label, run in CASES:
                if selected is not None and number not in selected:
                    continue
                print(f"  [{number:02d}] {label}")
                run(ctx)

            manifest = render(
                fixtures=capture.fixtures,
                findings=capture.findings,
                totals=dict(ctx.totals),
                app_version=app_version,
                max_records=args.max_records,
                request_count=capture.request_count,
            )
            (out_dir / "manifest.md").write_text(manifest, encoding="utf-8")
    finally:
        selectors.dispose()

    total_bytes = sum(f.size_bytes for f in capture.fixtures)
    print(
        f"\n{len(capture.fixtures)} fixtures, {human_size(total_bytes)} "
        f"({total_bytes:,} bytes), {capture.request_count} GETs"
    )
    for fixture in capture.fixtures:
        if fixture.size_bytes > ONE_MEGABYTE:
            print(f"  over 1MB: {fixture.filename} ({human_size(fixture.size_bytes)})")
    for finding in capture.findings:
        print(f"  no data: case {finding.case} — {finding.note}")
    return 0
