# app/repositories/inbox_repository.py
import shutil
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


class FileInboxRepository(InboxRepository):
    def __init__(self, config: MediaConfig) -> None:
        self.inbox_root = Path(config.inbox_root).resolve()
        self.media_root = Path(config.media_root).resolve()
        self.trash_root = self.inbox_root / ".trash"
        # ensure directories exist
        self.inbox_root.mkdir(parents=True, exist_ok=True)
        self.trash_root.mkdir(parents=True, exist_ok=True)

    def list_all(self) -> list[InboxItem]:
        items: list[InboxItem] = []
        for entry in sorted(self.inbox_root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            if entry.name == ".trash":
                continue
            items.append(self._to_item(entry, rel_path=entry.relative_to(self.inbox_root)))
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

    def _to_item(self, path: Path, rel_path: Path) -> InboxItem:
        if path.is_dir():
            children = [
                self._to_item(child, rel_path=child.relative_to(self.inbox_root))
                for child in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
                if child.name != ".trash"
            ]
            return InboxItem(
                path=str(rel_path).replace("\\", "/"),
                name=path.name,
                type=InboxItemTypeEnum.dir,
                size=None,
                children=children,
            )
        else:
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = None
            return InboxItem(
                path=str(rel_path).replace("\\", "/"),
                name=path.name,
                type=InboxItemTypeEnum.file,
                size=size,
                children=None,
            )

    @staticmethod
    def _safe_relative_path(base: Path, rel: str) -> Path:
        # Normalize separators and remove leading slashes
        rel_norm = rel.replace("\\", "/").lstrip("/")
        candidate = (base / rel_norm).resolve()
        if base not in candidate.parents and candidate != base:
            raise ForbiddenError("Path traversal detected")
        return candidate
