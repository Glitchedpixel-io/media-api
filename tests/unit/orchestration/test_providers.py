"""Unit tests for TransformRoute parsing."""

from __future__ import annotations

import pytest

from app.orchestration.providers import TransformRoute


class TestTransformRouteParse:
    @pytest.mark.unit
    def test_parse_splits_on_first_dot(self) -> None:
        route = TransformRoute.parse("prefect.transcode")

        assert route.provider == "prefect"
        assert route.command == "transcode"

    @pytest.mark.unit
    def test_parse_preserves_dots_in_remainder(self) -> None:
        route = TransformRoute.parse("kubernetes.ffmpeg/transcode.v2")

        assert route.provider == "kubernetes"
        assert route.command == "ffmpeg/transcode.v2"

    @pytest.mark.unit
    def test_parse_no_dot_raises(self) -> None:
        with pytest.raises(ValueError, match="provider-local-type"):
            TransformRoute.parse("transcode")

    @pytest.mark.unit
    def test_parse_empty_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="provider-local-type"):
            TransformRoute.parse(".transcode")

    @pytest.mark.unit
    def test_parse_empty_command_raises(self) -> None:
        with pytest.raises(ValueError, match="provider-local-type"):
            TransformRoute.parse("prefect.")

    @pytest.mark.unit
    def test_parse_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="provider-local-type"):
            TransformRoute.parse("")
