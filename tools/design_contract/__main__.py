"""Allow ``python -m tools.design_contract`` as well as the console script."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
