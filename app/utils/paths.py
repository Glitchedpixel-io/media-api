# app/utils/paths.py
import os
from pathlib import Path, PurePosixPath, PureWindowsPath, WindowsPath


def to_linux_path(path: str | None) -> str | None:
    if not path:
        return None
    # Reject Windows drive letters/backslashes early; normalize redundant slashes/./..
    if "\\" in path:
        raise ValueError("Backslashes are not allowed in POSIX paths.")
    # PurePosixPath doesn’t touch case; it collapses ".", ".." in a safe way
    norm = str(PurePosixPath(path))
    return norm.lstrip("/")


def fix_path(path: PurePosixPath | Path | str | None) -> PurePosixPath:
    if not path:
        raise ValueError("Path cannot be None")
    elif isinstance(path, str):
        # Check for .. before PurePosixPath normalizes it away
        if ".." in Path(path).parts:
            raise ValueError("Path traversal detected")
        return fix_path(PurePosixPath(path))
    elif isinstance(path, Path):
        # Must precede the PurePosixPath branch: on Linux PosixPath is a subclass of
        # PurePosixPath, so we'd never reach this branch if PurePosixPath came first.
        # Convert backslashes to forward slashes (handles Windows-style paths on Linux),
        # then use normpath to collapse . and .. without anchoring to CWD via resolve().
        posix_str = str(path).replace("\\", "/")
        normalized = os.path.normpath(posix_str)
        return fix_path(PurePosixPath(normalized))
    elif isinstance(path, PurePosixPath):
        # Check for .. in the original path string representation
        # PurePosixPath may have already normalized it, but we can check parts
        if ".." in path.parts:
            raise ValueError("Path traversal detected")
        if path.is_absolute():
            if path.relative_to("/"):
                return path.relative_to(PurePosixPath("/"))
            else:
                raise ValueError(f"Path cannot have {path.anchor} as anchor")
        else:
            try:
                if PureWindowsPath(path).drive:
                    raise ValueError(f"Path cannot have {PureWindowsPath(path).drive} as drive")
            except ValueError:
                raise
            if "\\" in str(path):
                raise ValueError("Backslashes are not allowed in POSIX paths.")
            return path
    else:
        raise ValueError(f"Invalid path type: {type(path)}")


def accessory_relative_path(asset_id: int, chunk_size: int = 2) -> str:
    """Compute relative accessory path for a given asset id.

    Example: asset_id=123 -> "00/03/123" (depending on base36 chunking)
    Logic mirrors the worker's previous implementation, centralized for reuse.
    """
    chars = "0123456789abcdefghijklmnopqrstuvwxyz"
    base36 = ""
    n = int(asset_id)
    while n:
        n, r = divmod(n, 36)
        base36 = chars[r] + base36
    if not base36:
        return "00"
    base36 = base36.rjust(chunk_size, "0")

    components = [base36[i : i + chunk_size] for i in range(0, len(base36), chunk_size)]
    components.append(str(asset_id))
    return str(Path(*components))


def resolve_under_root(relative_path: str, root: Path) -> Path:
    """Resolve a relative path under a given root with security checks.

    Ensures the resolved absolute path stays within the root.
    """
    if not relative_path:
        raise ValueError("relative_path cannot be empty")
    clean = Path(str(relative_path).replace("\\", "/"))
    if ".." in clean.parts:
        raise ValueError("Path traversal detected")
    absolute = (root / clean).resolve()
    try:
        absolute.relative_to(root)
    except ValueError:
        raise ValueError("Path escapes root")
    return absolute


def abs_path_from_env(var_name: str, default: str | Path) -> Path:
    """Read a path from environment and return an absolute Path.

    Rules:
    - Use provided default if env var is missing or empty.
    - Trim surrounding whitespace and quotes.
    - Expand environment variables (e.g., %USERPROFILE% or $HOME) and ~ user home.
    - Return an absolute Path (resolve when possible; fallback to absolute()).
    """
    raw = os.getenv(var_name)
    if raw is None or raw.strip() == "":
        value = str(default)
    else:
        value = raw
    # Strip surrounding whitespace and quotes
    sanitized = value.strip().strip('"').strip("'").strip()
    # Expand variables and user
    expanded = os.path.expandvars(sanitized)
    p = Path(expanded).expanduser()
    try:
        return p.resolve()
    except Exception:
        # In case resolve fails (e.g., permissions), still return an absolute path
        return p.absolute()
