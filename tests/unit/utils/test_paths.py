from pathlib import Path, PurePosixPath
import os

import pytest

from app.utils.paths import (
    accessory_relative_path,
    artwork_relative_path,
    fix_path,
    resolve_artwork_path,
    resolve_under_root,
    to_linux_path,
    abs_path_from_env,
)


@pytest.mark.unit
class TestFixPath:

    def test_without_path(self) -> None:
        with pytest.raises(ValueError):
            fix_path(None)

    def test_with_windows_path(self) -> None:
        with pytest.raises(ValueError):
            fix_path(PurePosixPath("C:\\"))
        with pytest.raises(ValueError):
            fix_path(PurePosixPath("C:/"))

    def test_with_unc_path(self) -> None:
        with pytest.raises(ValueError):
            fix_path(PurePosixPath("\\\\test\\folder"))
        with pytest.raises(ValueError):
            fix_path(PurePosixPath("\\\\test\\"))
        with pytest.raises(ValueError):
            fix_path(PurePosixPath("\\\\test"))

    def test_with_relative_posix_path(self) -> None:
        assert fix_path(PurePosixPath("a/b/c")) == PurePosixPath("a/b/c")

    def test_with_absolute_posix_path(self) -> None:
        assert fix_path(PurePosixPath("/a/b/c")) == PurePosixPath("a/b/c")

    def test_with_absolute_posix_path_leading_slash(self) -> None:
        assert fix_path(PurePosixPath("/a/b/c/")) == PurePosixPath("a/b/c")

    def test_with_absolute_path(self) -> None:
        assert fix_path(Path("/a/b/c")) == PurePosixPath("a/b/c")
        assert fix_path(Path("\\a\\b\\c")) == PurePosixPath("a/b/c")

    def test_with_relative_path(self) -> None:
        assert fix_path(Path("a/b/c")) == PurePosixPath("a/b/c")
        assert fix_path(Path("a\\b\\c")) == PurePosixPath("a/b/c")

    def test_with_relative_path_leading_slash(self) -> None:
        assert fix_path(Path("/a/b/c/")) == PurePosixPath("a/b/c")

    def test_with_current_path(self) -> None:
        assert fix_path(Path("./test")) == PurePosixPath("test")
        assert fix_path(Path("./")) == PurePosixPath("")
        assert fix_path(Path(".")) == PurePosixPath("")
        assert fix_path(Path("./folder/..")) == PurePosixPath("")


class TestToLinuxPath:
    def test_none_and_empty_return_none(self):
        assert to_linux_path(None) is None
        assert to_linux_path("") is None

    def test_normalization_and_strip_leading_slash(self):
        # Collapses '.', '..' and redundant slashes; strips any leading '/'
        assert to_linux_path("/a/b/./c/../d") == "a/b/c/../d"
        # Already relative stays relative
        assert to_linux_path("a/b/c") == "a/b/c"

    @pytest.mark.parametrize(
        "bad",
        [
            "C\\media\\a.mp4",  # Windows-style backslashes
            "\\\\server\\share\\x",  # UNC-style
        ],
    )
    def test_rejects_backslashes_and_unc(self, bad):
        with pytest.raises(ValueError):
            to_linux_path(bad)


class TestAccessoryRelativePath:
    def test_zero_has_special_handling(self):
        assert accessory_relative_path(0) == "00"

    def test_small_number_pads_to_chunk_size(self):
        # base36(5) = "5" -> padded to two chars -> "05/5"
        assert accessory_relative_path(5) == str(Path("05") / "5")

    def test_chunking_and_trailing_id_with_default_chunk_size(self):
        p = accessory_relative_path(123)
        parts = Path(p).parts
        # Last component is the original id as a string
        assert parts[-1] == "123"
        # All prefix components are of length 2 (default chunk_size)
        assert all(len(part) == 2 for part in parts[:-1])

    def test_custom_chunk_size_groups_prefix(self):
        p = accessory_relative_path(12345, chunk_size=3)
        parts = Path(p).parts
        assert parts[-1] == "12345"
        # With chunk_size=3, all prefix segments should be length 3
        assert len(parts) >= 2  # there is at least one prefix + the id
        assert all(len(part) == 3 for part in parts[:-1])


class TestResolveUnderRoot:
    def test_success_inside_root(self, tmp_path: Path):
        root = tmp_path / "root"
        (root / "nested").mkdir(parents=True)
        # Create a file to resolve under root
        target = resolve_under_root("nested/file.txt", root)
        # The resolved path should be under root
        assert str(target).startswith(str(root))
        # And point to the expected location
        assert target.parent.name == "nested"

    def test_empty_relative_path_is_rejected(self, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ValueError, match="relative_path cannot be empty"):
            resolve_under_root("", root)

    def test_traversal_is_rejected(self, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ValueError, match="Path traversal detected"):
            resolve_under_root("a/../secret.txt", root)
        # Backslash traversal also rejected after normalization step
        with pytest.raises(ValueError, match="Path traversal detected"):
            resolve_under_root("..\\outside.txt", root)

    def test_leading_slash_escapes_root(self, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ValueError, match="Path escapes root"):
            resolve_under_root("/etc/passwd", root)

    def test_deep_nested_path_inside_root(self, tmp_path: Path):
        root = tmp_path / "root"
        (root / "a" / "b" / "c").mkdir(parents=True)
        target = resolve_under_root("a/b/c/file.txt", root)
        assert str(target).startswith(str(root))
        assert target.name == "file.txt"

    def test_backslash_converted_to_forward_slash(self, tmp_path: Path):
        root = tmp_path / "root"
        (root / "folder").mkdir(parents=True)
        target = resolve_under_root("folder\\file.txt", root)
        # Backslashes should be converted to forward slashes
        assert str(target).startswith(str(root))

    def test_multiple_dotdot_rejected(self, tmp_path: Path):
        root = tmp_path / "root"
        root.mkdir()
        with pytest.raises(ValueError, match="Path traversal detected"):
            resolve_under_root("../../etc/passwd", root)


@pytest.mark.unit
class TestToLinuxPathExtended:
    def test_strips_multiple_leading_slashes(self):
        assert to_linux_path("///a/b/c") == "a/b/c"

    def test_collapses_redundant_slashes(self):
        assert to_linux_path("a//b///c") == "a/b/c"

    def test_handles_trailing_slash(self):
        assert to_linux_path("/a/b/c/") == "a/b/c"

    def test_single_dot_normalized(self):
        assert to_linux_path("./a/./b") == "a/b"

    def test_preserves_valid_relative_path(self):
        assert to_linux_path("media/movies/title.mp4") == "media/movies/title.mp4"


@pytest.mark.unit
class TestFixPathExtended:
    def test_with_string_path(self):
        assert fix_path("a/b/c") == PurePosixPath("a/b/c")

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError):
            fix_path("")

    def test_dot_only_path(self):
        assert fix_path(PurePosixPath(".")) == PurePosixPath("")

    def test_dotdot_in_middle_of_path_rejected(self):
        # Path traversal should be rejected even in middle of path
        with pytest.raises(ValueError, match="Path traversal detected"):
            fix_path(PurePosixPath("a/b/../c"))

    def test_dotdot_in_string_path_rejected(self):
        # Path traversal should be rejected when passed as string
        with pytest.raises(ValueError, match="Path traversal detected"):
            fix_path("a/b/../c")


@pytest.mark.unit
class TestAccessoryRelativePathExtended:
    def test_single_digit_id(self):
        result = accessory_relative_path(1)
        assert "1" in result

    def test_large_id_multiple_chunks(self):
        result = accessory_relative_path(999999)
        parts = Path(result).parts
        # Last part should be the ID
        assert parts[-1] == "999999"
        # Should have multiple prefix chunks
        assert len(parts) > 1

    def test_chunk_size_one(self):
        result = accessory_relative_path(123, chunk_size=1)
        parts = Path(result).parts
        # With chunk_size=1, each prefix char is separate
        assert parts[-1] == "123"
        assert all(len(part) == 1 for part in parts[:-1])

    def test_base36_conversion(self):
        # Test that base36 encoding works correctly
        # 36 in base36 is '10'
        result = accessory_relative_path(36)
        # Should contain base36 representation
        assert "36" in result


@pytest.mark.unit
class TestAbsPathFromEnv:
    def test_uses_default_when_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("TEST_PATH_VAR", raising=False)
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == Path("/default/path").resolve()

    def test_uses_default_when_env_var_empty(self, monkeypatch):
        monkeypatch.setenv("TEST_PATH_VAR", "")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == Path("/default/path").resolve()

    def test_uses_default_when_env_var_whitespace_only(self, monkeypatch):
        monkeypatch.setenv("TEST_PATH_VAR", "   ")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == Path("/default/path").resolve()

    def test_uses_env_var_when_set(self, monkeypatch, tmp_path):
        test_path = tmp_path / "custom"
        test_path.mkdir()
        monkeypatch.setenv("TEST_PATH_VAR", str(test_path))
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == test_path

    def test_strips_surrounding_whitespace(self, monkeypatch, tmp_path):
        test_path = tmp_path / "custom"
        test_path.mkdir()
        monkeypatch.setenv("TEST_PATH_VAR", f"  {test_path}  ")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == test_path

    def test_strips_surrounding_double_quotes(self, monkeypatch, tmp_path):
        test_path = tmp_path / "custom"
        test_path.mkdir()
        monkeypatch.setenv("TEST_PATH_VAR", f'"{test_path}"')
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == test_path

    def test_strips_surrounding_single_quotes(self, monkeypatch, tmp_path):
        test_path = tmp_path / "custom"
        test_path.mkdir()
        monkeypatch.setenv("TEST_PATH_VAR", f"'{test_path}'")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result == test_path

    def test_expands_tilde_to_home_directory(self, monkeypatch):
        monkeypatch.setenv("TEST_PATH_VAR", "~/custom/path")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert "~" not in str(result)
        # Should be an absolute path
        assert result.is_absolute()

    def test_expands_environment_variables(self, monkeypatch, tmp_path):
        base_path = tmp_path / "base"
        base_path.mkdir()
        monkeypatch.setenv("BASE_DIR", str(base_path))
        monkeypatch.setenv("TEST_PATH_VAR", "$BASE_DIR/custom")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        # Should have expanded $BASE_DIR
        assert str(base_path) in str(result)

    def test_returns_absolute_path(self, monkeypatch):
        monkeypatch.setenv("TEST_PATH_VAR", "relative/path")
        result = abs_path_from_env("TEST_PATH_VAR", "/default/path")
        assert result.is_absolute()

    def test_accepts_path_object_as_default(self, monkeypatch):
        monkeypatch.delenv("TEST_PATH_VAR", raising=False)
        default = Path("/default/path")
        result = abs_path_from_env("TEST_PATH_VAR", default)
        assert result == default.resolve()


#: A valid lowercase hex SHA-256, spelled out so the fan-out is readable in assertions.
DIGEST = "ab12cd34" + "0" * 56


@pytest.mark.unit
class TestArtworkRelativePath:
    """The content-addressed layout under ARTWORK_ROOT.

    Unlike `accessory_relative_path`, which derives its path from an asset id the
    caller already trusts, this one takes a digest that may have come from a request
    body. Validating it is what keeps the layout safe -- see the traversal tests below.
    """

    def test_fans_out_on_the_first_two_pairs(self) -> None:
        assert artwork_relative_path(DIGEST, ".jpg") == str(Path("ab", "12", f"{DIGEST}.jpg"))

    def test_full_digest_is_kept_in_the_filename(self) -> None:
        """The fan-out prefix is repeated in the filename, so a file is identifiable
        from its name alone once it has been moved or copied out of the tree."""
        assert artwork_relative_path(DIGEST, ".png").endswith(f"{DIGEST}.png")

    def test_honours_chunk_size(self) -> None:
        assert artwork_relative_path(DIGEST, ".jpg", chunk_size=3) == str(
            Path("ab1", "2cd", f"{DIGEST}.jpg")
        )

    @pytest.mark.parametrize("suffix", [".jpg", ".jpeg", ".png", ".webp", ".avif"])
    def test_accepts_the_extensions_producers_write(self, suffix: str) -> None:
        """A shape check rather than an allow-list, so a new image format does not
        need a code change here."""
        assert artwork_relative_path(DIGEST, suffix).endswith(suffix)

    @pytest.mark.parametrize(
        "digest",
        [
            "../../etc/passwd",
            "../" + "0" * 61,
            "ab/12" + "0" * 59,
            DIGEST.upper(),  # uppercase hex would collide on a case-insensitive store
            "0" * 63,  # too short
            "0" * 65,  # too long
            "zz" + "0" * 62,  # not hex
            "",
        ],
    )
    def test_rejects_a_digest_that_is_not_a_sha256(self, digest: str) -> None:
        with pytest.raises(ValueError):
            artwork_relative_path(digest, ".jpg")

    @pytest.mark.parametrize(
        "suffix",
        [
            ".jp/g",
            "../jpg",
            ".jpg/../..",
            "..jpg",  # a second dot would let a suffix carry its own path segment
            ".jpg.exe",
            "jpg",  # no leading dot
            ".JPG",
            "",
            "." + "a" * 9,  # unbounded length
        ],
    )
    def test_rejects_a_suffix_that_is_not_a_bare_extension(self, suffix: str) -> None:
        with pytest.raises(ValueError):
            artwork_relative_path(DIGEST, suffix)

    @pytest.mark.parametrize("chunk_size", [0, -1, 33])
    def test_rejects_a_chunk_size_that_leaves_no_fan_out(self, chunk_size: int) -> None:
        with pytest.raises(ValueError):
            artwork_relative_path(DIGEST, ".jpg", chunk_size=chunk_size)


@pytest.mark.unit
class TestResolveArtworkPath:

    def test_resolves_under_the_root(self, tmp_path: Path) -> None:
        result = resolve_artwork_path(DIGEST, ".jpg", tmp_path)
        assert result == tmp_path.resolve() / "ab" / "12" / f"{DIGEST}.jpg"

    def test_stays_inside_the_root(self, tmp_path: Path) -> None:
        """The containment guarantee the accessory listing route relies on."""
        result = resolve_artwork_path(DIGEST, ".jpg", tmp_path)
        assert result.is_relative_to(tmp_path.resolve())

    def test_does_not_create_anything(self, tmp_path: Path) -> None:
        """Path computation only. Writing is the registration path's job (#103)."""
        resolve_artwork_path(DIGEST, ".jpg", tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_a_rejected_digest_never_reaches_the_filesystem(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            resolve_artwork_path("../../etc/passwd", ".jpg", tmp_path)

    def test_the_same_contents_resolve_to_the_same_path(self, tmp_path: Path) -> None:
        """Content addressing is the whole reason for this layout: one poster shared
        by a season and its episodes is stored once, not once per entity."""
        assert resolve_artwork_path(DIGEST, ".jpg", tmp_path) == resolve_artwork_path(
            DIGEST, ".jpg", tmp_path
        )
