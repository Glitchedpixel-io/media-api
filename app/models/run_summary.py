# app/models/run_summary.py
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RunSummaryORM(Base):
    __tablename__ = "run_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_name: Mapped[str] = mapped_column(Text, nullable=False)
    worker_type: Mapped[str] = mapped_column(Text, nullable=False)
    transform_type: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    running_time: Mapped[int] = mapped_column(Integer, nullable=False)
    extras: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )


class ScannerRunSummaryORM(Base):
    __tablename__ = "scanner_run_summaries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_name: Mapped[str] = mapped_column(Text, nullable=False)
    worker_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable because only a filesystem walk can answer them. NULL reads as
    # "not applicable to this kind of scan", which 0 cannot express and no
    # consumer could tell apart from a real zero (media-api#37).
    scan_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    relative_to_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=func.now()
    )
    running_time: Mapped[int] = mapped_column(Integer, nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    folder_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    excluded_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previously_seen_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    no_metadata_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unsupported_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extras: Mapped[dict | None] = mapped_column(JSON, nullable=True)
