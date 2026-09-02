"""Command line entry point for the front-end API contract generator."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .load import Inventory
from .render import render
from .surfaces import SurfaceMapError, load_surface_map

#: Repository root, three levels up from this file.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Default inventory location inside this repository.
DEFAULT_INVENTORY = REPO_ROOT / "docs" / "capability-inventory.json"

#: Environment variable naming the destination. The contract is consumed by a
#: separate repository, whose location is a property of a working copy rather
#: than of this one, so there is no default path: pass ``--out`` or set this.
OUT_ENV_VAR = "DESIGN_CONTRACT_OUT"

#: The document lives permanently in a design tool's context.
DEFAULT_MAX_BYTES = 20_480


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="design-contract",
        description=("Generate the front-end API contract from the capability inventory."),
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="capability-inventory.json to read (default: %(default)s)",
    )
    parser.add_argument(
        "--surfaces",
        type=Path,
        default=Path(__file__).resolve().parent / "surfaces.yaml",
        help="surface map to read (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(f"file to write; required unless --check, and falls back to ${OUT_ENV_VAR}"),
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help="fail if the document exceeds this size (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate and report the size without writing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate the contract.

    Args:
        argv: Command line arguments, or ``None`` to read ``sys.argv``.

    Returns:
        Process exit code: 0 on success, 1 on a validation or size failure.
    """
    args = build_parser().parse_args(argv)

    if not args.inventory.is_file():
        print(f"inventory not found: {args.inventory}", file=sys.stderr)
        return 1

    inventory = Inventory.from_path(args.inventory)
    try:
        surface_map = load_surface_map(args.surfaces, inventory)
    except SurfaceMapError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    document = render(inventory, surface_map)
    size = len(document.encode("utf-8"))
    budget = f"{size:,} bytes of {args.max_bytes:,}"

    if size > args.max_bytes:
        print(f"contract is too large: {budget}", file=sys.stderr)
        return 1

    if args.check:
        print(f"ok — {budget}, not written")
        return 0

    out = args.out or (Path(os.environ[OUT_ENV_VAR]) if os.environ.get(OUT_ENV_VAR) else None)
    if out is None:
        print(
            f"no destination: pass --out, or set ${OUT_ENV_VAR}. The contract is "
            "consumed by a separate repository, so there is no default path.",
            file=sys.stderr,
        )
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document, encoding="utf-8")
    print(f"wrote {out} — {budget}")
    return 0
