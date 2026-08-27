# tests/contracts/repositories/test_inbox_repository_contract.py
from __future__ import annotations

from pathlib import Path

import pytest

from app.repositories import inbox_repository
from app.repositories.errors import (
    ForbiddenError,
    NotFoundError,
)
from app.schemas import (
    InboxDeleteRequest,
    InboxImportRequest,
    InboxItemTypeEnum,
)
from tests.contracts.repositories.bundles_impl import inbox_bundler


@pytest.fixture
def bundle(test_settings):
    b = inbox_bundler(test_settings)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_list_all(test_settings, bundle) -> None:
    # Arrange
    for i in range(10):
        inbox_tmp_file = Path(test_settings.media.inbox_root) / f"clip{i}.avi"
        inbox_tmp_file.write_bytes(b"in")

    (Path(test_settings.media.inbox_root) / "folder").mkdir(parents=True, exist_ok=True)

    for i in range(10):
        inbox_tmp_file = Path(test_settings.media.inbox_root) / "folder" / f"clip{i+20}.avi"
        inbox_tmp_file.write_bytes(b"in")

    # Act
    all_items = bundle.inbox.list_all()

    # Assert
    assert all_items and len(all_items) == 11
    assert any(item.type == InboxItemTypeEnum.dir for item in all_items)

    for item in all_items:
        if item.type == InboxItemTypeEnum.file:
            assert item.path.endswith(".avi")
            assert not item.children
        if item.type == InboxItemTypeEnum.dir:
            assert item.path.endswith("folder")
            assert len(item.children) == 10


@pytest.mark.contract
def test_move_file_to_folder(test_settings, bundle) -> None:
    # Arrange
    inbox_tmp_file = Path(test_settings.media.inbox_root) / "folder2" / "clip99.avi"
    inbox_tmp_file.parent.mkdir(parents=True, exist_ok=True)
    inbox_tmp_file.write_bytes(b"in")

    abs_dst_path, dst_path = bundle.inbox.move(
        InboxImportRequest(source="folder2/clip99.avi", target="a/b/folder99/file.avi")
    )

    assert dst_path and str(dst_path.as_posix()) == "a/b/folder99/file.avi"
    assert abs_dst_path.as_posix().endswith(dst_path.as_posix())
    assert not inbox_tmp_file.exists()
    assert (Path(test_settings.media.media_root) / "a/b/folder99" / "file.avi").exists()


@pytest.mark.contract
def test_move_file_to_trash(test_settings, bundle) -> None:
    # Arrange
    inbox_tmp_file = Path(test_settings.media.inbox_root) / "folder2" / "clip99.avi"
    inbox_tmp_file.parent.mkdir(parents=True, exist_ok=True)
    inbox_tmp_file.write_bytes(b"in")

    # Act
    bundle.inbox.delete(InboxDeleteRequest(source="folder2/clip99.avi"))

    # Assert
    assert not inbox_tmp_file.exists()
    assert (Path(test_settings.media.inbox_root) / ".trash/folder2" / "clip99.avi").exists()


@pytest.mark.contract
def test_move_non_existent_file_to_trash(bundle) -> None:

    with pytest.raises(NotFoundError):
        bundle.inbox.delete(InboxDeleteRequest(source="bad/clip101.avi"))


@pytest.mark.contract
def test_move_non_existent_file_to_media(bundle) -> None:

    with pytest.raises(NotFoundError):
        bundle.inbox.move(InboxImportRequest(source="bad/clip101.avi", target="any/file.txt"))

    with pytest.raises(ForbiddenError):
        bundle.inbox.move(InboxImportRequest(source="bad/clip101.avi", target="../../any/file.txt"))

    with pytest.raises(ForbiddenError):
        bundle.inbox.move(InboxImportRequest(source="../../bad/clip101.avi", target="any/file.txt"))


# --- Bounded walk (#54) -------------------------------------------------------


@pytest.mark.contract
def test_a_symlink_cycle_does_not_recurse(test_settings, bundle) -> None:
    """The case that returned a 500 to anyone who could write to the inbox.

    `is_dir()` follows symlinks, so a directory linking back to its own parent
    recursed until RecursionError. The link is still listed -- it is really there --
    but it is never descended into.
    """
    root = Path(test_settings.media.inbox_root)
    folder = root / "folder"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "loop").symlink_to(root, target_is_directory=True)

    items = bundle.inbox.list_all()

    folder_item = next(item for item in items if item.name == "folder")
    loop = next(child for child in folder_item.children if child.name == "loop")
    assert loop.type == InboxItemTypeEnum.dir
    assert loop.children is None
    assert loop.children_truncated is True, "a symlinked directory must not be walked"


@pytest.mark.contract
def test_depth_limits_the_walk_and_says_so(test_settings, bundle) -> None:
    """An unwalked directory is distinguishable from an empty one."""
    root = Path(test_settings.media.inbox_root)
    (root / "folder" / "nested").mkdir(parents=True, exist_ok=True)
    (root / "folder" / "nested" / "clip.avi").write_bytes(b"in")

    top = bundle.inbox.list_all(depth=1)

    folder = next(item for item in top if item.name == "folder")
    assert folder.children is None
    assert folder.children_truncated is True

    two = bundle.inbox.list_all(depth=2)
    folder = next(item for item in two if item.name == "folder")
    assert folder.children_truncated is False
    nested = next(child for child in folder.children if child.name == "nested")
    assert nested.children is None
    assert nested.children_truncated is True, "the next level down is still unwalked"


@pytest.mark.contract
def test_an_empty_directory_is_not_reported_as_truncated(test_settings, bundle) -> None:
    """The distinction the flag exists to make, in the other direction."""
    (Path(test_settings.media.inbox_root) / "empty").mkdir(parents=True, exist_ok=True)

    items = bundle.inbox.list_all()

    empty = next(item for item in items if item.name == "empty")
    assert empty.children == []
    assert empty.children_truncated is False


@pytest.mark.contract
def test_the_response_is_capped_however_deep_the_tree(test_settings, bundle, monkeypatch) -> None:
    """A tree larger than the cap is truncated rather than returned whole."""
    monkeypatch.setattr(inbox_repository, "MAX_ITEMS", 5)
    root = Path(test_settings.media.inbox_root)
    for i in range(12):
        (root / f"clip{i}.avi").write_bytes(b"in")

    items = bundle.inbox.list_all()

    assert len(items) == 5, "the item cap must bound the response"


@pytest.mark.contract
def test_depth_cannot_exceed_the_hard_ceiling(test_settings, bundle, monkeypatch) -> None:
    """A caller cannot ask for more depth than the ceiling allows."""
    monkeypatch.setattr(inbox_repository, "MAX_DEPTH", 2)
    root = Path(test_settings.media.inbox_root)
    (root / "a" / "b" / "c").mkdir(parents=True, exist_ok=True)

    items = bundle.inbox.list_all(depth=99)

    a = next(item for item in items if item.name == "a")
    b = next(child for child in a.children if child.name == "b")
    assert b.children is None
    assert b.children_truncated is True


@pytest.mark.contract
def test_an_unreadable_directory_does_not_fail_the_request(
    test_settings, bundle, monkeypatch
) -> None:
    """One unreadable subtree is reported as truncated, not raised.

    Permissions are simulated rather than applied: the suite runs as root, which
    can read a 0o000 directory, so chmod would not exercise this path at all.
    """
    root = Path(test_settings.media.inbox_root)
    locked = root / "locked"
    locked.mkdir(parents=True, exist_ok=True)
    (locked / "clip.avi").write_bytes(b"in")

    real_iterdir = Path.iterdir

    def deny_locked(self: Path):
        if self.name == "locked":
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", deny_locked)

    items = bundle.inbox.list_all()

    locked_item = next(item for item in items if item.name == "locked")
    assert locked_item.children == []
    assert locked_item.children_truncated is True
