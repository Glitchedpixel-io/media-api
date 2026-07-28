# tests/contracts/repositories/test_inbox_repository_contract.py
from __future__ import annotations

from pathlib import Path

import pytest

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
