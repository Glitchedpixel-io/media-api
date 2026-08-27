# app/models/title_reference.py

from sqlalchemy import (
    Enum,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.schemas.enums import TitleReferenceTypeEnum


class TitleReferenceORM(Base):
    __tablename__ = "title_references"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("titles.id", ondelete="CASCADE"),
        index=True,  # references are only ever read for one title
        nullable=False,
    )
    reference_type: Mapped[TitleReferenceTypeEnum] = mapped_column(
        Enum(TitleReferenceTypeEnum, name="title_reference_type_enum"), nullable=False
    )
    reference_url: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
