from decimal import Decimal, InvalidOperation
from typing import Any


TRUE_VALUES = {"1", "true", "t", "yes", "y"}
FALSE_VALUES = {"0", "false", "f", "no", "n", ""}


def clean_text(value: Any, default: str = "") -> str:
    """
    Convert a database or spreadsheet value to trimmed text.
    """
    if value is None:
        return default

    return str(value).strip()


def clean_email(value: Any) -> str:
    """
    Clean an email value without trying to invent or repair invalid addresses.
    """
    return clean_text(value).lower()


def clean_phone(value: Any) -> str:
    """
    Preserve the old phone representation while trimming surrounding spaces.
    """
    return clean_text(value)


def to_boolean(value: Any, default: bool = False) -> bool:
    """
    Convert common legacy boolean representations to Python bool.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    normalized = clean_text(value).lower()

    if normalized in TRUE_VALUES:
        return True

    if normalized in FALSE_VALUES:
        return False

    return default


def to_decimal(
    value: Any,
    default: Decimal | None = None,
) -> Decimal | None:
    """
    Safely convert a legacy value to Decimal.
    """
    if value in (None, ""):
        return default

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def is_populated(value: Any) -> bool:
    """
    Return True when a spreadsheet cell contains any meaningful value.

    This is used for the IGNORE column. Any nonblank value means skip.
    """
    return bool(clean_text(value))