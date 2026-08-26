"""Allow ``python -m tools.capability_inventory`` as well as the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
