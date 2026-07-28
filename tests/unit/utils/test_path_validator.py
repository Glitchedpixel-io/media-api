# tests/unit/domain/test_path_validation.py
import pytest

from app.utils.paths import to_linux_path


@pytest.mark.unit
def test_normalizes_simple_posix_path():
    assert to_linux_path("/a/b/./c") == "a/b/c"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["C:\\media\\a.mp4", "\\server\\share\\x"])
def test_rejects_non_posix(bad):
    with pytest.raises(ValueError):
        to_linux_path(bad)
