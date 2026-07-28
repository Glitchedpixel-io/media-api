import copy
from typing import Optional, TypeVar, get_args

from pydantic import BaseModel, create_model
from pydantic.fields import FieldInfo

T = TypeVar("T", bound=BaseModel)


def make_partial_model(model: type[T], *, name: str | None = None) -> type[T]:
    """
    Create a subclass of `model` where all fields are optional (default None).
    Inherits config & validators from `model`. Each field's `FieldInfo` (title,
    description, and validation constraints like `pattern`/`max_length`) is
    preserved from the source model rather than discarded.
    """
    name = name or f"{model.__name__}Partial"
    new_fields: dict[str, tuple[object, FieldInfo]] = {}
    for fname, f in model.model_fields.items():
        ann = f.annotation
        # If not already Optional, wrap it
        if type(None) not in get_args(ann):
            ann = Optional[ann]  # type: ignore # type: ignore[index]
        new_field = copy.deepcopy(f)
        new_field.annotation = ann
        new_field.default = None
        new_field.default_factory = None
        new_fields[fname] = (ann, new_field)
    # inherit from the original model to keep validators/config;
    # overriding fields with optional versions
    return create_model(name, __base__=model, **new_fields)  # type: ignore # type: ignore[return-value]
