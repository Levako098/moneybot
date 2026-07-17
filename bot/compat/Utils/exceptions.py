from __future__ import annotations

from typing import Any


class CardinalCompatibilityError(Exception):
    """Base class for configuration errors exposed by Cardinal."""


class ParamNotFoundError(CardinalCompatibilityError):
    pass


class EmptyValueError(CardinalCompatibilityError):
    pass


class ValueNotValidError(CardinalCompatibilityError):
    pass


class ProductsFileNotFoundError(CardinalCompatibilityError):
    pass


class NoProductsError(CardinalCompatibilityError):
    pass


class NotEnoughProductsError(CardinalCompatibilityError):
    pass


class NoProductVarError(CardinalCompatibilityError):
    pass


class SectionNotFoundError(CardinalCompatibilityError):
    pass


class SubCommandAlreadyExists(CardinalCompatibilityError):
    pass


class DuplicateSectionErrorWrapper(CardinalCompatibilityError):
    pass


class ConfigParseError(CardinalCompatibilityError):
    pass


class FieldNotExistsError(CardinalCompatibilityError):
    pass


def __getattr__(name: str) -> Any:
    """Keep imports from newer Cardinal exception modules loadable."""
    if name.endswith(("Error", "Exception")):
        dynamic = type(name, (CardinalCompatibilityError,), {})
        globals()[name] = dynamic
        return dynamic
    raise AttributeError(name)
