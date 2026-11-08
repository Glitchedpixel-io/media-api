# app/schemas/_orm.py
from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ModelWrapValidatorHandler, model_validator
from sqlalchemy import inspect
from sqlalchemy.orm import InstanceState


class _LoadedView:
    """An ORM instance as a read schema should see it: unloaded relationships empty.

    Reads go through to the instance, except for a ``lazy="raise"`` relationship the
    query did not load, which reads as ``[]`` for a collection and ``None`` for a single
    object. Every other attribute, loaded or not, is read from the instance unchanged.
    """

    __slots__ = ("_instance", "_empty")

    def __init__(self, instance: object, state: InstanceState[Any]) -> None:
        relationships = state.mapper.relationships
        self._instance = instance
        self._empty = {
            key: relationships[key].uselist
            for key in state.unloaded
            if key in relationships and relationships[key].lazy == "raise"
        }

    def __getattr__(self, name: str) -> Any:
        if name in self._empty:
            return [] if self._empty[name] else None
        return getattr(self._instance, name)


class UnloadedRelationshipsAsEmpty(BaseModel):
    """Read schema whose relationships are filled only when the query loaded them.

    A relationship gated behind ``include=`` is declared ``lazy="raise"`` on the model,
    so nothing can load it by accident: the repository eager-loads it when asked, and
    otherwise reading it raises. Pydantic reads every field when validating from
    attributes, so this schema reads an unloaded ``lazy="raise"`` relationship as empty
    instead -- ``[]`` or ``None`` -- which is what the API has always returned for a
    relationship that was not requested.

    Validation from anything that is not a mapped instance, such as a dict or keyword
    arguments, is unaffected.
    """

    @model_validator(mode="wrap")
    @classmethod
    def _empty_unloaded_relationships(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        state = inspect(data, raiseerr=False)
        if isinstance(state, InstanceState):
            data = _LoadedView(data, state)
        return handler(data)
