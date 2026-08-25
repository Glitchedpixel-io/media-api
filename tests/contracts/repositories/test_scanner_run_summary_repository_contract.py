# tests/contracts/repositories/test_scanner_run_summary_repository_contract.py
"""Contract tests for the scanner run summary repository.

The point of interest is media-api#37: a scan that is not over a filesystem
must be storable without inventing zeros for the columns only a directory walk
can answer, and must have somewhere to put the counters it does have.
"""

import pytest

from tests.contracts.repositories.bundles_impl import (
    make_bundle,
    scanner_run_summary_bundler,
)
from tests.factories import (
    NonFilesystemScannerRunSummaryFactory,
    ScannerRunSummaryFactory,
)

_FILESYSTEM_FIELDS = (
    "scan_path",
    "relative_to_path",
    "total_count",
    "folder_count",
    "excluded_count",
    "error_count",
    "api_error_count",
    "no_metadata_count",
    "unsupported_file_count",
)


@pytest.fixture
def bundle(db_session, _test_engine):
    b = make_bundle(db_session, _test_engine, scanner_run_summary_bundler)
    try:
        yield b
    finally:
        b.close()


# --- Contract tests ----------------------------------------------------------


@pytest.mark.contract
def test_create_get_exists_roundtrip(bundle):
    """A filesystem scan still stores every counter it always did."""
    out = bundle.scanner_run_summary.create(ScannerRunSummaryFactory())

    assert out.id is not None
    assert bundle.scanner_run_summary.exists(out.id) is True

    fetched = bundle.scanner_run_summary.get(out.id)
    assert fetched is not None
    assert fetched.created_at is not None
    assert fetched.scan_path == "/data/media"
    assert fetched.folder_count == 2


@pytest.mark.contract
def test_non_filesystem_scan_persists_with_nulls(bundle):
    """The filesystem-only columns accept NULL rather than forcing a zero."""
    out = bundle.scanner_run_summary.create(NonFilesystemScannerRunSummaryFactory())

    fetched = bundle.scanner_run_summary.get(out.id)
    assert fetched is not None
    for field in _FILESYSTEM_FIELDS:
        assert getattr(fetched, field) is None, f"{field} should be NULL, not a zero"

    # ...while the counters every scanner can answer are still recorded.
    assert fetched.processed_count == 7
    assert fetched.previously_seen_count == 3
    assert fetched.running_time == 12


@pytest.mark.contract
def test_extras_round_trips(bundle):
    """`extras` is where a scanner records what this shape has no field for."""
    out = bundle.scanner_run_summary.create(NonFilesystemScannerRunSummaryFactory())

    fetched = bundle.scanner_run_summary.get(out.id)
    assert fetched is not None
    assert fetched.extras == {"items_seen": 40, "created": 7, "skipped_existing": 33}


@pytest.mark.contract
def test_zero_is_distinguishable_from_not_applicable(bundle):
    """A measured 0 and an inapplicable counter must not collapse together.

    This is the whole reason the columns became nullable: before, a scanner with
    no filesystem had to send `0` for `folder_count`, which no reader could tell
    from a filesystem scan that genuinely found no folders.
    """
    measured = bundle.scanner_run_summary.create(
        ScannerRunSummaryFactory(folder_count=0, error_count=0)
    )
    inapplicable = bundle.scanner_run_summary.create(NonFilesystemScannerRunSummaryFactory())

    assert bundle.scanner_run_summary.get(measured.id).folder_count == 0
    assert bundle.scanner_run_summary.get(inapplicable.id).folder_count is None


@pytest.mark.contract
def test_get_missing_returns_none(bundle):
    assert bundle.scanner_run_summary.get(999_999) is None
    assert bundle.scanner_run_summary.exists(999_999) is False
