# app/repositories/inbox_repository.py
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.schemas import (
    InboxDeleteRequest,
    InboxImportRequest,
    InboxItem,
    InboxItemTypeEnum,
)

from .errors import ForbiddenError, NotFoundError
from .protocols import InboxRepository
from ..config import MediaConfig

# Hard ceilings, applied whatever the caller asks for. The inbox is a directory
# anything with write access can add to, so the response size and the recursion
# depth cannot be left to what happens to be on disk.
MAX_DEPTH = 20
MAX_ITEMS = 5_000


@dataclass
class _Budget:
    """How many more entries the whole response may contain."""

    remaining: int


class FileInboxRepository(InboxRepository):
    def __init__(self, config: MediaConfig) -> None:
        self.inbox_root = Path(config.inbox_root).resolve()
        self.media_root = Path(config.media_root).resolve()
        self.trash_root = self.inbox_root / ".trash"
        # ensure directories exist
        self.inbox_root.mkdir(parents=True, exist_ok=True)
        self.trash_root.mkdir(parents=True, exist_ok=True)

    def list_all(self, depth: int | None = None) -> list[InboxItem]:
        """Walk the inbox tree, bounded.

        The walk is bounded whatever the caller asks for: at most ``MAX_DEPTH``
        levels and ``MAX_ITEMS`` entries, and never descending into a symlinked
        directory. Without those a symlink cycle recursed until ``RecursionError``
        -- reachable by anyone who can write to the inbox -- and the response size
        was whatever happened to be on disk.

        Args:
            depth: How many levels to walk, or None for the maximum. Clamped to
                ``MAX_DEPTH`` regardless.

        Returns:
            list[InboxItem]: The top-level entries. Any directory whose children
            were not walked carries ``children_truncated``, so an unexpanded
            directory is never mistaken for an empty one.
        """
        levels = MAX_DEPTH if depth is None else max(1, min(depth, MAX_DEPTH))
        budget = _Budget(remaining=MAX_ITEMS)
        items, _ = self._walk(self.inbox_root, levels, budget)
        return items

    def delete(self, file: InboxDeleteRequest) -> None:
        src = self._safe_relative_path(self.inbox_root, file.source)
        if not src.exists():
            raise NotFoundError("Source not found in inbox")

        # Keep folder structure in trash
        rel = src.relative_to(self.inbox_root)
        dst = (self.trash_root / rel).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)

        # attempt to move the file
        shutil.move(str(src), str(dst))

    def move(self, move: InboxImportRequest) -> tuple[Path, Path]:
        # get the source & destination files
        src = self._safe_relative_path(self.inbox_root, move.source)
        dst = self._safe_relative_path(self.media_root, move.target)
        if not src.exists() or not src.is_file():
            raise NotFoundError("Source file not found in inbox")
        # make sure the destination parent exists
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise ForbiddenError("Destination file already exists")
        # do the move
        shutil.move(str(src), str(dst))
        return dst, dst.relative_to(self.media_root)

    def _walk(self, directory: Path, levels: int, budget: _Budget) -> tuple[list[InboxItem], bool]:
        """List one directory, recursing while depth and budget allow.

        Args:
            directory: Directory to list.
            levels: Remaining depth. At 1, children are listed but not descended.
            budget: Shared item allowance for the whole response.

        Returns:
            A tuple of (items, truncated). ``truncated`` is True when entries were
            left unlisted because the budget ran out.
        """
        items: list[InboxItem] = []
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            # Unreadable or vanished between listing and walking. An inbox the
            # caller cannot fully see is not a reason to fail the whole request.
            return [], True

        for entry in entries:
            if entry.name == ".trash":
                continue
            if budget.remaining <= 0:
                return items, True
            budget.remaining -= 1
            items.append(self._to_item(entry, levels, budget))
        return items, False

    def _to_item(self, path: Path, levels: int, budget: _Budget) -> InboxItem:
        """Describe one entry, descending into it only if that is allowed."""
        rel_path = str(path.relative_to(self.inbox_root)).replace("\\", "/")

        if not path.is_dir():
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            return InboxItem(
                path=rel_path,
                name=path.name,
                type=InboxItemTypeEnum.file,
                size=size,
                children=None,
            )

        # A symlinked directory is never descended into: `is_dir()` follows the
        # link, so a cycle inside the inbox recursed until RecursionError, which
        # surfaced as a 500 and was reachable by anyone who can write there.
        if levels <= 1 or path.is_symlink() or budget.remaining <= 0:
            return InboxItem(
                path=rel_path,
                name=path.name,
                type=InboxItemTypeEnum.dir,
                size=None,
                children=None,
                children_truncated=True,
            )

        children, truncated = self._walk(path, levels - 1, budget)
        return InboxItem(
            path=rel_path,
            name=path.name,
            type=InboxItemTypeEnum.dir,
            size=None,
            children=children,
            children_truncated=truncated,
        )

    @staticmethod
    def _safe_relative_path(base: Path, rel: str) -> Path:
        # Normalize separators and remove leading slashes
        rel_norm = rel.replace("\\", "/").lstrip("/")
        candidate = (base / rel_norm).resolve()
        if base not in candidate.parents and candidate != base:
            raise ForbiddenError("Path traversal detected")
        return candidate
